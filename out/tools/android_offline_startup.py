#!/usr/bin/env python3
"""Build one Android drpf startup patch, preserving all original nested code.

No game code is executed. Only the injected call uses already-attested NeoX
opcodes; bundled source is compiled later by the game's own interpreter.
"""
import argparse
import copy
import ctypes
import ctypes.util
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib


INSTALL_HOOKS = '''def install_hooks():
    """Install once; Android keeps its original drpf module."""
    global _hooks_installed
    if _hooks_installed:
        return False
    if __builtin__.__import__ is importing:
        raise RuntimeError('OFFLINE_IMPORT_HOOK_STATE_INCONSISTENT')
    original_exception = sys.excepthook

    def exception_hook(kind, value, tb):
        stop('FIRST_UNHANDLED_EXCEPTION', traceback=''.join(traceback.format_exception(kind, value, tb)))
        original_exception(kind, value, tb)

    sys.excepthook = exception_hook
    __builtin__.__import__ = importing
    _hooks_installed = True
    event('research_module.entered', runtime_profile=PROFILE['id'])
    return True
'''


def prepare_probe(raw):
    """Adapt the known private observer without publishing its business code."""
    source = raw.decode('utf8').replace('\r\n', '\n')
    if 'def install_hooks():' in source:
        required = ('verify_patch_identity(utils, PROFILE)', 'PROFILE = current_profile()',
                    'from offline_runtime_profile import current_profile, verify_patch_identity')
        if any(value not in source for value in required):
            raise ValueError('incomplete existing Android observer adaptation')
        return source.encode('utf8')

    def replace_once(old, new):
        nonlocal source
        if source.count(old) != 1:
            raise ValueError('private observer does not match the supported startup layout')
        source = source.replace(old, new, 1)

    replace_once('from offline_paths import resolve_root, runtime_run_dir\n',
                 'from offline_paths import resolve_root, runtime_run_dir\n'
                 'from offline_runtime_profile import current_profile, verify_patch_identity\n')
    replace_once('RUN = runtime_run_dir(ROOT)\n', 'RUN = runtime_run_dir(ROOT)\nPROFILE = current_profile()\n')
    replace_once('_real_import = __builtin__.__import__\n',
                 '_real_import = __builtin__.__import__\n_hooks_installed = False\n')
    replace_once("        if utils.get_cur_patch_version() != '1.0.95':\n"
                 "            block('UNEXPECTED_LOCAL_PATCH_VERSION')",
                 '        try:\n            identity = verify_patch_identity(utils, PROFILE)\n'
                 '        except RuntimeError as error:\n            block(str(error))')
    replace_once("        event('patch_original_request_skipped', kind='patchlist', local_version='1.0.95')",
                 "        event('patch_original_request_skipped', kind='patchlist',\n"
                 "              local_version=identity['script_version'], runtime_profile=identity['profile'])")
    begin = source.find('def resume(wrapper_namespace):\n    try:\n')
    end = source.find("        importer = sys.path_importer_cache.get('script')", begin)
    if begin < 0 or end < 0:
        raise ValueError('private observer resume boundary not found')
    previous = source[begin:end]
    required = ('original_exception = sys.excepthook', '__builtin__.__import__ = importing',
                'def exception_hook(kind, value, tb):', 'sys.excepthook = exception_hook')
    if any(value not in previous for value in required) or len(previous) > 1200:
        raise ValueError('private observer exception hook layout is not recognized')
    source = (source[:begin] + INSTALL_HOOKS + '\n\ndef resume(wrapper_namespace):\n'
              '    try:\n        install_hooks()\n' + source[end:])
    return source.encode('utf8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(value):
    """Explicit Python-2 marshal writer; names are emitted as interned strings."""
    def integer(n):
        return struct.pack('<i', n)

    def string(raw, interned=False):
        return (b't' if interned else b's') + integer(len(raw)) + raw

    if value is None:
        return b'N'
    if value is True:
        return b'T'
    if value is False:
        return b'F'
    if isinstance(value, int):
        if -(1 << 31) <= value < (1 << 31):
            return b'i' + integer(value)
        sign, n, digits = (-1 if value < 0 else 1), abs(value), []
        while n:
            digits.append(struct.pack('<H', n & 32767))
            n >>= 15
        return b'l' + integer(sign * len(digits)) + b''.join(digits)
    if isinstance(value, float):
        return b'g' + struct.pack('<d', value)
    if isinstance(value, complex):
        return b'y' + struct.pack('<dd', value.real, value.imag)
    if isinstance(value, bytes):
        return string(value)
    if isinstance(value, str):
        raw = value.encode('utf8')
        return b'u' + integer(len(raw)) + raw
    if isinstance(value, tuple):
        return b'(' + integer(len(value)) + b''.join(dump(v) for v in value)
    if isinstance(value, dict) and value.get('type') == 'code':
        out = b'c' + b''.join(integer(value[k]) for k in ('argcount', 'nlocals', 'stacksize', 'flags'))
        out += dump(value['bytecode']) + dump(value['consts'])
        for field in ('names', 'varnames', 'freevars', 'cellvars'):
            out += b'(' + integer(len(value[field])) + b''.join(string(n, True) for n in value[field])
        out += dump(value['filename']) + dump(value['name']) + integer(value['firstlineno']) + dump(value['lnotab'])
        return out
    raise ValueError('unsupported marshal type: ' + type(value).__name__)


def patch_drpf(original, source):
    code = copy.deepcopy(original)
    if code['filename'] != b'drpf.py' or code['name'] != b'<module>':
        raise ValueError('unexpected drpf module')
    if not code['bytecode'].endswith(bytes((153, 1, 0, 83))) or code['consts'][1] is not None:
        raise ValueError('unexpected final return')
    if b'hsqsl_android_startup' in code['consts']:
        raise ValueError('already patched')
    names, consts, call = list(code['names']), list(code['consts']), bytearray()

    def emit(opcode, arg=None):
        call.append(opcode)
        if arg is not None:
            if not 0 <= arg < 65536:
                raise ValueError('operand out of range')
            call.extend(struct.pack('<H', arg))

    def name(value):
        raw = value.encode('ascii')
        if raw not in names:
            names.append(raw)
        emit(101, names.index(raw))

    def constant(raw):
        # Append without deduplicating: bool/int equality must not alias operands.
        consts.append(raw)
        emit(153, len(consts) - 1)

    # eval(compile(source, '<hsqsl-android-startup>', 'exec'),
    #      dict(__name__='hsqsl_android_startup'))
    name('eval')
    name('compile')
    constant(source)
    constant(b'<hsqsl-android-startup>')
    constant(b'exec')
    emit(131, 3)
    name('dict')
    constant(b'__name__')
    constant(b'hsqsl_android_startup')
    emit(131, 256)
    emit(131, 2)
    emit(7)
    code['bytecode'] = code['bytecode'][:-4] + bytes(call) + code['bytecode'][-4:]
    code['names'], code['consts'] = tuple(names), tuple(consts)
    code['stacksize'] = max(code['stacksize'], 5)
    return code, bytes(call)


def make_source(candidate, bootstrap):
    # The desktop drpf wrapper must never shadow the original Android module.
    sources = {p.stem: p.read_bytes() for p in sorted(candidate.glob('*.py')) if p.name != 'drpf.py'}
    for required in ('main_city_probe', 'offline_paths'):
        if required not in sources:
            raise ValueError('missing startup source: ' + required)
    sources['main_city_probe'] = prepare_probe(sources['main_city_probe'])
    sources['offline_runtime_profile'] = Path(__file__).with_name('android_offline_profile.py').read_bytes()
    hashes = {name: sha(value) for name, value in sources.items()}
    prefix = ('# -*- coding: utf-8 -*-\nBUNDLE_SOURCES = ' + ascii(sources) +
              '\nBUNDLE_HASHES = ' + repr(hashes) + '\nANDROID_STARTUP_AUTORUN = True\n').encode('ascii')
    return prefix + bootstrap.read_bytes(), hashes


def rebuild_npk(original, patched, replacement, npk, rotor_key):
    entries, data = npk.parse_archive(original)
    matches = [e for e in entries if e['hash'] == 0xE3E44BDB]
    if len(matches) != 1:
        raise ValueError('drpf key must be present exactly once')
    from rotor_compat import Rotor
    wrapped = b'\0' + replacement + b'\0'
    transformed = bytes(b ^ 233 if i < 99 else b for i, b in enumerate(wrapped))
    encoded = Rotor(rotor_key).encrypt(zlib.compress(transformed, 9))
    lib = ctypes.CDLL(ctypes.util.find_library('lz4'))
    lib.LZ4_compressBound.argtypes = [ctypes.c_int]
    lib.LZ4_compressBound.restype = ctypes.c_int
    lib.LZ4_compress_default.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    lib.LZ4_compress_default.restype = ctypes.c_int
    capacity = lib.LZ4_compressBound(len(encoded))
    buffer = ctypes.create_string_buffer(capacity)
    size = lib.LZ4_compress_default(encoded, buffer, len(encoded), capacity)
    if size <= 0:
        raise ValueError('LZ4 compression failed')
    blocks, metadata, offset = [], [], 24
    for entry in entries:
        if entry['hash'] == 0xE3E44BDB:
            block, decoded_size, codec = buffer.raw[:size], len(encoded), 2
        else:
            block = data[entry['offset']:entry['offset'] + entry['stored_size']]
            decoded_size, codec = entry['decoded_size'], entry['codec']
        stored_offset, stored_codec = offset, codec
        if entry['encoded_offset']:
            stored_offset ^= ((-100 - decoded_size) & 0xffffffff) ^ 0x7a090d89
            stored_codec += 62
        metadata.append(struct.pack('<IIIIQI', entry['hash'], stored_offset, len(block),
                                    decoded_size, entry['hash64'], stored_codec))
        blocks.append(block)
        offset += len(block)
    header = bytearray(data[:24])
    struct.pack_into('<I', header, 20, offset)
    patched.write_bytes(bytes(header) + b''.join(blocks) + b''.join(metadata))
    new_entries, new_data = npk.parse_archive(patched)
    unchanged = 0
    for old, new in zip(entries, new_entries):
        if old['hash'] != new['hash']:
            raise ValueError('entry order changed')
        decoded = npk.decode_entry(new, new_data)
        if old['hash'] != 0xE3E44BDB:
            if data[old['offset']:old['offset'] + old['stored_size']] != new_data[new['offset']:new['offset'] + new['stored_size']]:
                raise ValueError('unrelated stored block modified')
            unchanged += 1
        else:
            clear = zlib.decompress(Rotor(rotor_key).decrypt(decoded))
            clear = bytes(b ^ 233 if i < 99 else b for i, b in enumerate(clear))[1:-1]
            if clear != replacement:
                raise ValueError('patched script round trip mismatch')
    return {'entries': len(new_entries), 'unrelated_stored_blocks_identical': unchanged,
            'all_outer_entries_decoded': True, 'patched_script_roundtrip': True,
            'index_hash64': 'original values preserved; checksum semantics and native runtime acceptance not verified'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--bootstrap', type=Path, default=Path(__file__).with_name('android_offline_bootstrap.py'))
    parser.add_argument('--drpf', type=Path, help='Optional audited marshal; otherwise extracted statically from --npk')
    parser.add_argument('--npk', type=Path, required=True)
    parser.add_argument('--bindings', type=Path, required=True)
    parser.add_argument('--tools', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.tools))
    from mem_marshal_extract import Loader
    import npk_unpack
    if sha(args.npk.read_bytes()) != '8629ab140227b003995f75fe6bdf4dffe2eaf4754e804584e6db940f83f4e309':
        raise ValueError('mobile script.npk identity mismatch')
    key = json.loads(args.bindings.read_text())['rotor_key'].encode('ascii')
    if args.drpf:
        original_bytes = args.drpf.read_bytes()
    else:
        from rotor_compat import Rotor
        entries, packed = npk_unpack.parse_archive(args.npk)
        entry = next(e for e in entries if e['hash'] == 0xE3E44BDB)
        cipher = npk_unpack.decode_entry(entry, packed)
        clear = zlib.decompress(Rotor(key).decrypt(cipher))
        original_bytes = bytes(b ^ 233 if i < 99 else b for i, b in enumerate(clear))[1:-1]
    loader = Loader(original_bytes)
    original = loader.r_object()
    if (loader.p != len(original_bytes)
            or sha(original_bytes) != 'c1f0278a15f14a04dedd5c036ada1605d1fbf7e89eecf382d4231b5c73bfd307'):
        raise ValueError('mobile drpf identity mismatch')
    source, hashes = make_source(args.candidate, args.bootstrap)
    result, call = patch_drpf(original, source)
    encoded = dump(result)
    restored = Loader(encoded)
    parsed = restored.r_object()
    if restored.p != len(encoded) or parsed != result:
        raise ValueError('marshal writer roundtrip mismatch')
    for old, new in zip(original['consts'], parsed['consts']):
        if old != new:
            raise ValueError('original constant or nested function changed')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'patched-drpf.marshal').write_bytes(encoded)
    (args.output / 'embedded-startup.py').write_bytes(source)
    npk_report = rebuild_npk(args.npk, args.output / 'script.npk', encoded, npk_unpack, key)
    report = {'schema': 1, 'runtime_profile': 'android_1_0_8_armv7',
              'original_drpf_sha256': sha(original_bytes), 'patched_drpf_sha256': sha(encoded),
              'bootstrap_source_sha256': sha(source), 'bundled_sources': hashes,
              'desktop_drpf_excluded': True, 'original_nested_code_and_constants_preserved': True,
              'original_top_level_code_preserved_except_insertion_before_return': True,
              'insertion_offset': len(original['bytecode']) - 4, 'injected_bytecode_hex': call.hex(),
              'native_app_integrity_overrides': [], 'npk': npk_report,
              'script_npk_sha256': sha((args.output / 'script.npk').read_bytes()),
              'apk_repackaged': False, 'android_installed_or_executed': False}
    (args.output / 'build-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'bundled_sources'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
