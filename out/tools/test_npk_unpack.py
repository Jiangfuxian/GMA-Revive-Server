# -*- coding: utf-8 -*-
"""覆盖已知 LZ4 向量和截断边界；不运行不受信任的脚本负载。"""
import unittest
import io
import struct
import zlib
from npk_unpack import DecodeError, decode_entry, lz4_decompress, read_index


def archive_bytes(payload, decoded, codec=0, encoded=False):
    index = 24 + len(payload)
    offset = 24
    if encoded:
        offset ^= ((-100 - decoded) & 0xffffffff) ^ 0x7a090d89
        codec += 62
    header = b'NXPK' + struct.pack('<IIIII', 1, 0, 0, 0, index)
    return header + payload + struct.pack('<IIIIQI', 17, offset, len(payload), decoded, 0, codec)


class ArchiveTests(unittest.TestCase):
    def test_encoded_and_plain_raw_zlib_lz4(self):
        original = b'hello'
        for codec, payload in ((0, original), (1, zlib.compress(original)), (2, b'\x50hello')):
            for encoded in (False, True):
                with self.subTest(codec=codec, encoded=encoded):
                    data = archive_bytes(payload, len(original), codec, encoded)
                    entries = read_index(io.BytesIO(data), len(data))
                    self.assertEqual(entries[0]['offset'], 24)
                    self.assertEqual(entries[0]['encoded_offset'], encoded)
                    self.assertEqual(decode_entry(entries[0], data), original)

    def test_decoded_bytes_agree_with_existing_checksum_rule(self):
        from gen_npkfilelist_md5 import block_md5s
        import hashlib
        data = archive_bytes(b'hello', 5, encoded=True)
        pairs, report = block_md5s(data)
        entry = read_index(io.BytesIO(data), len(data))[0]
        self.assertEqual(pairs, [entry['hash'], hashlib.md5(decode_entry(entry, data)).hexdigest()])
        self.assertEqual(report['编码偏移条目'], 1)

    def test_truncated_index_invalid_codec_and_raw_length(self):
        for data in (archive_bytes(b'hello', 5)[:-1], archive_bytes(b'hello', 5, 3),
                     archive_bytes(b'hello', 4), archive_bytes(b'', 1, 1)):
            with self.subTest(data=data):
                with self.assertRaises(DecodeError):
                    read_index(io.BytesIO(data), len(data))

    def test_overlapping_payloads_are_rejected(self):
        data = (b'NXPK' + struct.pack('<IIIII', 2, 0, 0, 0, 29) + b'hello'
                + struct.pack('<IIIIQI', 17, 24, 5, 5, 0, 0)
                + struct.pack('<IIIIQI', 18, 25, 4, 4, 0, 0))
        with self.assertRaises(DecodeError):
            read_index(io.BytesIO(data), len(data))

    def test_zlib_output_beyond_declared_size_is_rejected(self):
        data = archive_bytes(zlib.compress(b'a' * 10000), 5, 1)
        entry = read_index(io.BytesIO(data), len(data))[0]
        with self.assertRaises(DecodeError):
            decode_entry(entry, data)

    def test_empty_entry(self):
        data = archive_bytes(b'', 0)
        self.assertEqual(decode_entry(read_index(io.BytesIO(data), len(data))[0], data), b'')


class LZ4Tests(unittest.TestCase):
    def test_literal(self):
        self.assertEqual(lz4_decompress(b'\x50hello',5),b'hello')

    def test_extended_literal(self):
        self.assertEqual(lz4_decompress(b'\xf0\x01'+b'a'*16,16),b'a'*16)

    def test_overlapping_match(self):
        self.assertEqual(lz4_decompress(b'\x14a\x01\x00\x50hello',14),b'a'*9+b'hello')

    def test_final_low_nibble_does_not_imply_match(self):
        self.assertEqual(lz4_decompress(b'\x51hello',5),b'hello')

    def test_truncation_and_boundaries(self):
        for src, expected in [(b'\xf0',16),(b'\x50hell',5),
                              (b'\x50hello',4),(b'\x10a\x00\x00',5),
                              (b'\x10a\x02\x00',5),(b'\x10a\x01',5),
                              (b'\x1fa\x01\x00',20)]:
            with self.subTest(src=src):
                with self.assertRaises(DecodeError):
                    lz4_decompress(src,expected)


if __name__ == '__main__':
    unittest.main()
