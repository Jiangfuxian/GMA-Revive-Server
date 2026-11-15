import builtins
import hashlib
import importlib
import io
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

import android_offline_apk as apk_tool
import android_offline_bootstrap as bootstrap
import android_offline_profile as profile
import android_offline_startup as startup_tool
from android_offline_startup import dump, patch_drpf, prepare_probe, rebuild_npk
from mem_marshal_extract import Loader
import npk_unpack


def fixture_code(name=b'<module>'):
    # Synthetic code only: no extracted game bytecode is part of this test suite.
    return dict(type='code', argcount=0, nlocals=0, stacksize=1, flags=64,
                bytecode=bytes((153, 0, 0, 7, 153, 1, 0, 83)),
                consts=(-1, None, b'unchanged', (b'synthetic',)), names=(),
                varnames=(), freevars=(), cellvars=(), filename=b'drpf.py',
                name=name, firstlineno=1, lnotab=b'')


class StartupTests(unittest.TestCase):
    def test_cli_resolves_repository_helpers_and_rejects_wrong_mobile_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'wrong.npk'
            source.write_bytes(b'not-the-audited-mobile-input')
            argv = ['android_offline_startup.py', '--candidate', str(root), '--npk', str(source),
                    '--bindings', str(root / 'unused-bindings.json'), '--output', str(root / 'output')]
            with patch.object(sys, 'argv', argv):
                with self.assertRaisesRegex(ValueError, 'mobile script.npk identity mismatch'):
                    startup_tool.main()
            self.assertFalse((root / 'output').exists())

    def test_marshal_patch_preserves_original_code_and_is_fully_readable(self):
        original = fixture_code()
        modified, call = patch_drpf(original, b'pass\n')
        self.assertEqual(modified['bytecode'], original['bytecode'][:-4] + call + original['bytecode'][-4:])
        self.assertEqual(modified['consts'][:len(original['consts'])], original['consts'])
        raw = dump(modified)
        parsed = Loader(raw)
        self.assertEqual(parsed.r_object(), modified)
        self.assertEqual(parsed.p, len(raw))
        self.assertEqual(original['bytecode'], fixture_code()['bytecode'])

    def test_unrecognized_entry_and_already_patched_code_are_rejected(self):
        code = fixture_code()
        code['filename'] = b'unknown.py'
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            patch_drpf(code, b'pass')
        code = fixture_code()
        code['consts'] += (b'hsqsl_android_startup',)
        with self.assertRaisesRegex(ValueError, 'already patched'):
            patch_drpf(code, b'pass')

    def test_profile_checks_versions_without_rewriting_integrity_functions(self):
        utils = types.SimpleNamespace(get_cur_patch_version=lambda: '1.0.125',
                                      get_app_base_version=lambda: '1.0.8', app_check=object())
        original = dict(vars(utils))
        with patch.dict(os.environ, {'HSQSL_OFFLINE_PROFILE': 'android_1_0_8_armv7'}):
            self.assertEqual(profile.verify_patch_identity(utils)['script_version'], '1.0.125')
            utils.get_cur_patch_version = lambda: '1.0.95'
            with self.assertRaisesRegex(RuntimeError, 'SCRIPT_VERSION_MISMATCH'):
                profile.verify_patch_identity(utils)
        self.assertIs(utils.app_check, original['app_check'])
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(profile.current_profile()['script_version'], '1.0.95')

    def test_existing_adaptation_is_idempotent_and_partial_source_is_rejected(self):
        source = ("from offline_runtime_profile import current_profile, verify_patch_identity\n"
                  "PROFILE = current_profile()\ndef install_hooks():\n    pass\n"
                  "def check(utils):\n    verify_patch_identity(utils, PROFILE)\n").encode()
        self.assertEqual(prepare_probe(source), source)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            prepare_probe(b'def install_hooks():\n    pass\n')
        with self.assertRaisesRegex(ValueError, 'supported startup layout'):
            prepare_probe(b'# unsupported observer\n')

    def test_known_old_observer_is_ported_without_retaining_pc_version_gate(self):
        source = '''from offline_paths import resolve_root, runtime_run_dir
ROOT = ''
RUN = runtime_run_dir(ROOT)
_real_import = __builtin__.__import__
def check(utils):
        if utils.get_cur_patch_version() != '1.0.95':
            block('UNEXPECTED_LOCAL_PATCH_VERSION')
        event('patch_original_request_skipped', kind='patchlist', local_version='1.0.95')
def resume(wrapper_namespace):
    try:
        event('research_module.entered')
        __builtin__.__import__ = importing
        original_exception = sys.excepthook
        def exception_hook(kind, value, tb):
            original_exception(kind, value, tb)
        sys.excepthook = exception_hook
        importer = sys.path_importer_cache.get('script')
        return importer
    except Exception:
        raise
'''
        result = prepare_probe(source.encode())
        compile(result, '<synthetic-private-observer>', 'exec')
        self.assertIn(b'verify_patch_identity(utils, PROFILE)', result)
        self.assertIn(b'def install_hooks():', result)
        self.assertIn(b"importer = sys.path_importer_cache.get('script')", result)
        self.assertNotIn(b"local_version='1.0.95'", result)
        self.assertEqual(prepare_probe(result), result)

    def test_encoded_offset_npk_is_rebuilt_without_changing_other_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, target = root / 'source.npk', root / 'target.npk'
            blocks = [b'old-script', b'unchanged-data']
            header = bytearray(b'NXPK' + struct.pack('<I', 2) + b'\0' * 16)
            index_at = 24 + sum(map(len, blocks))
            struct.pack_into('<I', header, 20, index_at)
            offset = 24
            index = []
            for key, block in zip([0xE3E44BDB, 17], blocks):
                encoded_offset = offset ^ (((-100 - len(block)) & 0xffffffff) ^ 0x7a090d89)
                index.append(struct.pack('<IIIIQI', key, encoded_offset, len(block), len(block), 1234, 62))
                offset += len(block)
            source.write_bytes(bytes(header) + b''.join(blocks) + b''.join(index))
            report = rebuild_npk(source, target, dump(fixture_code()), npk_unpack, b'fixture-rotor')
            self.assertEqual(report['unrelated_stored_blocks_identical'], 1)
            self.assertTrue(report['patched_script_roundtrip'])
            entries, data = npk_unpack.parse_archive(target)
            self.assertTrue(entries[0]['encoded_offset'])
            self.assertEqual(npk_unpack.decode_entry(entries[1], data), blocks[1])


class ImporterTests(unittest.TestCase):
    def test_bundled_importer_works_with_current_host_and_removes_failed_module(self):
        sources = {'hsqsl_synthetic_bundle': b'value = 7\n'}
        hashes = {name: hashlib.sha256(body).hexdigest() for name, body in sources.items()}
        importer = bootstrap.BundledSourceImporter(sources, hashes)
        sys.meta_path.insert(0, importer)
        try:
            self.assertEqual(importlib.import_module('hsqsl_synthetic_bundle').value, 7)
        finally:
            sys.meta_path.remove(importer)
            sys.modules.pop('hsqsl_synthetic_bundle', None)
        sources = {'hsqsl_synthetic_failure': b"raise ValueError('FIXTURE_FAILURE')\n"}
        hashes = {name: hashlib.sha256(body).hexdigest() for name, body in sources.items()}
        importer = bootstrap.BundledSourceImporter(sources, hashes)
        with self.assertRaisesRegex(ValueError, 'FIXTURE_FAILURE'):
            importer.load_module('hsqsl_synthetic_failure')
        self.assertNotIn('hsqsl_synthetic_failure', sys.modules)

    def test_desktop_wrapper_and_corrupt_source_cannot_shadow_native_drpf(self):
        with self.assertRaisesRegex(RuntimeError, 'BUNDLE_INVALID'):
            bootstrap.BundledSourceImporter({'drpf': b''}, {'drpf': hashlib.sha256(b'').hexdigest()})
        with self.assertRaisesRegex(RuntimeError, 'SOURCE_HASH_MISMATCH'):
            bootstrap.BundledSourceImporter({'example': b'pass'}, {'example': '0' * 64})

    def test_native_root_bootstrap_installs_once_and_failure_rolls_back(self):
        source = b"def install_hooks():\n    pass\ndef event(*a, **kw):\n    pass\n"
        sources = {'main_city_probe': source}
        hashes = {'main_city_probe': hashlib.sha256(source).hexdigest()}
        game = types.SimpleNamespace(PLATFORM_ANDROID=2, get_platform=lambda: 2)
        alias = types.SimpleNamespace(__import__=builtins.__import__)
        previous_meta, previous_exception = list(sys.meta_path), sys.excepthook
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), \
                patch.dict(sys.modules, {'__builtin__': alias}):
            game.get_doc_dir = lambda: temp.encode()
            try:
                self.assertTrue(bootstrap.install(game, sources, hashes))
                self.assertFalse(bootstrap.install(game, sources, hashes))
                self.assertEqual(os.environ['HSQSL_OFFLINE_ROOT'], temp + '/')
                self.assertFalse((Path(temp) / 'research/gacha/accounts').exists())
            finally:
                sys.meta_path[:] = previous_meta
                sys.modules.pop('main_city_probe', None)
            broken = b"def install_hooks():\n    raise ValueError('INSTALL_FAILURE')\n"
            sources, hashes = {'main_city_probe': broken}, {'main_city_probe': hashlib.sha256(broken).hexdigest()}
            del game._hsqsl_offline_startup
            os.environ.clear()
            with self.assertRaisesRegex(ValueError, 'INSTALL_FAILURE'):
                bootstrap.install(game, sources, hashes)
            self.assertEqual(sys.meta_path, previous_meta)
            self.assertIs(sys.excepthook, previous_exception)
            self.assertNotIn('main_city_probe', sys.modules)
            self.assertNotIn('HSQSL_OFFLINE_ROOT', os.environ)


class ApkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.script, self.output = (self.root / p for p in ('source.apk', 'script.npk', 'output.apk'))
        self.script.write_bytes(b'NXPK synthetic replacement')

    def test_only_script_and_stale_signature_entries_change(self):
        entries = {'assets/script.npk': b'old', 'lib/armeabi-v7a/libexample.so': b'unchanged-elf-fixture',
                   'AndroidManifest.xml': b'manifest-fixture', 'META-INF/CERT.RSA': b'signature-fixture',
                   'META-INF/CERT.SF': b'signature-fixture', 'META-INF/MANIFEST.MF': b'signature-fixture',
                   'META-INF/appchannel': b'keep-channel', 'assets/other.bin': b'keep-resource'}
        with zipfile.ZipFile(self.source, 'w') as z:
            for name, value in entries.items():
                z.writestr(name, value)
        report = apk_tool.repack(self.source, self.script, self.output)
        self.assertEqual(report['native_library_count'], 1)
        self.assertEqual(len(report['removed_signature_entries']), 3)
        apk_tool.verify_output(self.output, report['_entry_sha256'])
        with zipfile.ZipFile(self.output) as z:
            self.assertEqual(z.read('assets/script.npk'), self.script.read_bytes())
            for name in entries:
                if name != 'assets/script.npk' and not apk_tool.signature_entry(name):
                    self.assertEqual(z.read(name), entries[name])
        with zipfile.ZipFile(self.output, 'a') as z:
            z.writestr('unexpected.bin', b'post-processing-corruption')
        with self.assertRaisesRegex(ValueError, 'content verification failed'):
            apk_tool.verify_output(self.output, report['_entry_sha256'])

    def test_invalid_archive_cleans_partial_output_and_existing_output_is_preserved(self):
        with zipfile.ZipFile(self.source, 'w') as z:
            z.writestr('assets/script.npk', b'old')
            z.writestr('../escape', b'bad')
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            apk_tool.repack(self.source, self.script, self.output)
        self.assertFalse(self.output.exists())
        self.output.write_bytes(b'keep-existing')
        with self.assertRaisesRegex(ValueError, 'existing output'):
            apk_tool.repack(self.source, self.script, self.output)
        self.assertEqual(self.output.read_bytes(), b'keep-existing')

    def test_signing_passwords_are_only_referenced_by_environment_name(self):
        with patch.dict(os.environ, {'HSQSL_TEST_STORE_PASS': 'fixture-only'}):
            command = apk_tool.signing_command(Path('apksigner.jar'), Path('in.apk'), Path('out.apk'),
                                               Path('local.keystore'), 'local', 'HSQSL_TEST_STORE_PASS')
        self.assertIn('env:HSQSL_TEST_STORE_PASS', command)
        self.assertNotIn('fixture-only', command)

    def test_cli_report_cannot_overwrite_an_input(self):
        self.source.write_bytes(b'preserve-original-input')
        argv = ['android_offline_apk.py', '--apk', str(self.source), '--script-npk', str(self.script),
                '--startup-report', str(self.root / 'startup.json'), '--output', str(self.output),
                '--report', str(self.source)]
        errors = io.StringIO()
        with patch.object(sys, 'argv', argv), patch.object(sys, 'stderr', errors):
            with self.assertRaises(SystemExit):
                apk_tool.main()
        self.assertIn('report path must be distinct', errors.getvalue())
        self.assertEqual(self.source.read_bytes(), b'preserve-original-input')
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
