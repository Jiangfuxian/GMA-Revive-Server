# -*- coding: utf-8 -*-
"""NXPK 严格解包器：原始存储块按标准 LZ4 解压，不执行取反。

索引 +8=存储长度、+12=解压长度，证据是 PC VA 0xbf69a3 调用及
Unicorn VA 0x1309310 实际返回值。输出统一使用 .bin；19 73 头的
脚本负载尚未证实为 marshal，禁止用 .marshal 后缀暗示已解密。
运行：python out/tools/npk_unpack.py 输入.npk 输出目录 [条目上限]
"""
import hashlib
import io
import json
import struct
import sys
import zlib
from pathlib import Path


class DecodeError(ValueError):
    """损坏或尚未支持的数据必须显式失败。"""


def read_index(stream, size):
    """只读取索引；偏移规则与 gen_npkfilelist_md5.block_md5s 保持一致。"""
    stream.seek(0)
    header = stream.read(24)
    if len(header) != 24 or header[:4] != b'NXPK':
        raise DecodeError('不是完整的 NXPK 文件')
    count = struct.unpack_from('<I', header, 4)[0]
    index = struct.unpack_from('<I', header, 20)[0]
    index_end = index + count * 28
    if index < 24 or index_end > size:
        raise DecodeError('索引区越界')
    entries = []
    seen = set()
    spans = []
    stream.seek(index)
    for i in range(count):
        record = stream.read(28)
        if len(record) != 28:
            raise DecodeError('索引区被截断')
        key, offset, stored, decoded, hash64, codec = struct.unpack_from(
            '<IIIIQI', record)
        encoded_offset = codec >= 62
        if encoded_offset:
            offset ^= ((-100 - decoded) & 0xffffffff) ^ 0x7a090d89
            codec -= 62
        if codec not in (0, 1, 2):
            raise DecodeError('尚未验证的压缩类型：%d' % codec)
        end = offset + stored
        overlaps_index = offset < index_end and end > index
        if key in seen or (stored and (offset < 24 or end > size or overlaps_index)):
            raise DecodeError('条目哈希重复或存储区越界：%08X' % key)
        if not stored and decoded:
            raise DecodeError('空存储块声明了非零解压长度：%08X' % key)
        if codec == 0 and stored != decoded:
            raise DecodeError('原始存储块长度不匹配：%08X' % key)
        seen.add(key)
        if stored:
            spans.append((offset, end))
        entries.append(dict(hash=key, offset=offset, stored_size=stored,
                            decoded_size=decoded, hash64=hash64, codec=codec,
                            encoded_offset=encoded_offset, after_index=offset >= index_end))
    spans.sort()
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise DecodeError('存储块互相重叠')
    return entries


def parse_archive(path):
    data = Path(path).read_bytes()
    entries = read_index(io.BytesIO(data), len(data))
    return entries, data


def lz4_decompress(src, expected):
    """带严格读写边界的标准 LZ4 block 解码，支持重叠回溯复制。"""
    out = bytearray()
    i = 0

    def length(initial):
        nonlocal i
        value = initial
        if initial == 15:
            while True:
                if i >= len(src):
                    raise DecodeError('长度扩展被截断')
                b = src[i]
                i += 1
                value += b
                if b != 255:
                    break
        return value

    if expected < 0 or expected > 64 * 1024 * 1024:
        raise DecodeError('目标长度超过工具限制')
    while i < len(src):
        token = src[i]
        i += 1
        literal = length(token >> 4)
        if i + literal > len(src) or len(out) + literal > expected:
            raise DecodeError('字面量读取或输出越界')
        out.extend(src[i:i+literal])
        i += literal
        if i == len(src):
            break
        if i + 2 > len(src):
            raise DecodeError('回溯偏移被截断')
        offset = int.from_bytes(src[i:i+2], 'little')
        i += 2
        if offset == 0 or offset > len(out):
            raise DecodeError('非法回溯偏移')
        match = length(token & 15) + 4
        if len(out) + match > expected:
            raise DecodeError('回溯输出越界')
        for _ in range(match):
            out.append(out[-offset])
    if len(out) != expected:
        raise DecodeError('输出长度不匹配：%d != %d' % (len(out), expected))
    return bytes(out)


def decode_entry(entry, data):
    if not 0 <= entry['decoded_size'] <= 64 * 1024 * 1024:
        raise DecodeError('目标长度超过工具限制')
    start = entry['offset']
    raw = data[start:start+entry['stored_size']]
    codec = entry['codec']
    if not raw and entry['decoded_size'] == 0:
        result = b''
    elif codec == 0:
        result = raw
        if len(result) != entry['decoded_size']:
            raise DecodeError('原始存储块长度不匹配')
    elif codec == 2:
        result = lz4_decompress(raw, entry['decoded_size'])
    elif codec == 1:
        decoder = zlib.decompressobj()
        result = decoder.decompress(raw, entry['decoded_size'] + 1)
        if len(result) != entry['decoded_size'] or not decoder.eof or decoder.unconsumed_tail:
            raise DecodeError('zlib 输出长度不匹配')
    else:
        raise DecodeError('尚未验证的压缩类型：%d' % codec)
    return result


def main(path, outdir, limit=None):
    entries, data = parse_archive(path)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    selected = entries if limit is None else entries[:limit]
    rows = []
    for entry in selected:
        row = dict(entry)
        row['hash'] = '%08X' % entry['hash']
        try:
            result = decode_entry(entry, data)
            name = row['hash'] + '.bin'
            (outdir/name).write_bytes(result)
            row.update(status='已解压', file=name, sha256=hashlib.sha256(result).hexdigest(),
                       prefix=result[:16].hex(), payload='未识别')
        except (ValueError, zlib.error) as exc:
            row.update(status='失败', error=str(exc))
        rows.append(row)
    report = dict(source=str(Path(path).resolve()), archive_sha256=hashlib.sha256(data).hexdigest(),
                  total=len(entries), tested=len(rows),
                  blocks_after_index=sum(e['after_index'] for e in entries),
                  success=sum(r['status']=='已解压' for r in rows), entries=rows)
    (outdir/'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('%s：总数=%d，本次=%d，成功=%d，失败=%d' % (
        path, len(entries), len(rows), report['success'], len(rows)-report['success']))
    for row in rows:
        if row['status'] == '失败':
            print('  %s：%s' % (row['hash'], row['error']))
    return report


if __name__ == '__main__':
    result = main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv)>3 else None)
    sys.exit(0 if result['success']==result['tested'] else 1)
