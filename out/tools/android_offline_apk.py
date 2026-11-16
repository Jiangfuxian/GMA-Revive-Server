#!/usr/bin/env python3
"""Repackage a locally supplied APK; game assets and signing keys stay outside Git."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import zipfile

ORIGINAL_APK_SHA256 = 'e8f06e22d985549a7b11f17bde963a45c37c17b107448934ea124a3c26449b03'


def signature_entry(name):
    path = PurePosixPath(name)
    return (len(path.parts) == 2 and path.parts[0].upper() == 'META-INF' and
            (path.name.upper() == 'MANIFEST.MF' or path.suffix.upper() in ('.RSA', '.DSA', '.EC', '.SF')))


def digest(stream):
    value = hashlib.sha256()
    with stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def validate_archive(archive):
    seen = set()
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if (path.is_absolute() or '..' in path.parts or '\\' in info.filename
                or info.filename in seen or info.flag_bits & 1):
            raise ValueError('unsafe, duplicate or encrypted APK entry')
        seen.add(info.filename)
    if 'assets/script.npk' not in seen:
        raise ValueError('assets/script.npk is missing')


def verify_output(path, expected, allow_signatures=False):
    """Check payload bytes; APK signatures are verified separately by apksigner."""
    with zipfile.ZipFile(path) as output:
        validate_archive(output)
        actual = {info.filename: digest(output.open(info)) for info in output.infolist()
                  if not (allow_signatures and signature_entry(info.filename))}
    if actual != expected:
        raise ValueError('APK content verification failed')


def repack(apk, script_npk, destination):
    if destination.exists():
        raise ValueError('refusing to replace an existing output')
    expected, removed = {}, []
    try:
        with zipfile.ZipFile(apk) as source, zipfile.ZipFile(destination, 'w', allowZip64=True) as output:
            validate_archive(source)
            for info in source.infolist():
                if signature_entry(info.filename):
                    removed.append(info.filename)
                    continue
                item = copy.copy(info)
                if info.is_dir():
                    output.writestr(item, b'')
                    expected[info.filename] = hashlib.sha256(b'').hexdigest()
                    continue
                if info.filename == 'assets/script.npk':
                    stream = script_npk.open('rb')
                    item.file_size = script_npk.stat().st_size
                    item.compress_type = zipfile.ZIP_STORED
                else:
                    stream = source.open(info)
                value = hashlib.sha256()
                with stream, output.open(item, 'w', force_zip64=item.file_size >= zipfile.ZIP64_LIMIT) as target:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        value.update(block)
                        target.write(block)
                expected[info.filename] = value.hexdigest()
        verify_output(destination, expected)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    natives = {name: value for name, value in expected.items() if name.startswith('lib/') and name.endswith('.so')}
    return {'changed_asset': 'assets/script.npk', 'removed_signature_entries': removed,
            'verified_output_entries': len(expected), 'unchanged_entries': len(expected) - 1,
            'native_libraries': natives, 'native_library_count': len(natives),
            '_entry_sha256': expected,
            'installed_or_executed': False}


def signing_command(jar, source, destination, keystore, alias, store_env, key_env=None):
    for name in [store_env] + ([key_env] if key_env else []):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ValueError('invalid password environment variable name')
        if not os.environ.get(name):
            raise ValueError('signing password environment variable is not set')
    command = ['java', '-jar', str(jar), 'sign', '--ks', str(keystore),
               '--ks-key-alias', alias, '--ks-pass', 'env:' + store_env]
    if key_env:
        command += ['--key-pass', 'env:' + key_env]
    return command + ['--out', str(destination), str(source)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk', type=Path, required=True)
    parser.add_argument('--script-npk', type=Path, required=True)
    parser.add_argument('--startup-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--zipalign', type=Path)
    parser.add_argument('--apksigner-jar', type=Path)
    parser.add_argument('--keystore', type=Path)
    parser.add_argument('--key-alias')
    parser.add_argument('--store-pass-env')
    parser.add_argument('--key-pass-env')
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve() in (args.apk.resolve(), args.script_npk.resolve()):
        parser.error('output must be a new path distinct from all inputs')
    protected = [args.apk, args.script_npk, args.startup_report, args.output,
                 args.keystore, args.zipalign, args.apksigner_jar]
    if args.report.resolve() in {p.resolve() for p in protected if p is not None}:
        parser.error('report path must be distinct from inputs, tools and APK output')
    signing = any([args.apksigner_jar, args.keystore, args.key_alias, args.store_pass_env, args.key_pass_env])
    if signing and not all([args.apksigner_jar, args.keystore, args.key_alias, args.store_pass_env, args.zipalign]):
        parser.error('signing requires zipalign, apksigner jar, keystore, alias and password environment name')
    if digest(args.apk.open('rb')) != ORIGINAL_APK_SHA256:
        parser.error('APK is not the audited Android 1.0.8 input')
    startup = json.loads(args.startup_report.read_text(encoding='utf8'))
    if (startup.get('runtime_profile') != 'android_1_0_8_armv7'
            or startup.get('script_npk_sha256') != digest(args.script_npk.open('rb'))):
        parser.error('startup report does not match the supplied mobile script patch')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='hsqsl-apk-', dir=args.output.parent) as temporary:
        folder = Path(temporary)
        built = folder / 'unsigned.apk'
        report = repack(args.apk, args.script_npk, built)
        expected = report.pop('_entry_sha256')
        if args.zipalign:
            aligned = folder / 'aligned.apk'
            subprocess.run([str(args.zipalign), '-f', '-p', '4', str(built), str(aligned)], check=True)
            subprocess.run([str(args.zipalign), '-c', '-p', '4', str(aligned)], check=True)
            built = aligned
        if signing:
            signed = folder / 'signed.apk'
            command = signing_command(args.apksigner_jar, built, signed, args.keystore,
                                      args.key_alias, args.store_pass_env, args.key_pass_env)
            subprocess.run(command, check=True)
            subprocess.run(['java', '-jar', str(args.apksigner_jar), 'verify', '--verbose', str(signed)], check=True)
            built = signed
        verify_output(built, expected, allow_signatures=signing)
        report['final_content_verified'] = True
        os.replace(built, args.output)
    report.update(signed=signing, aligned=bool(args.zipalign),
                  apk_bytes=args.output.stat().st_size,
                  apk_sha256=digest(args.output.open('rb')),
                  boundary='Package/content checks only; native NPK acceptance, startup and gameplay remain untested.')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps({k: v for k, v in report.items() if k != 'native_libraries'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
