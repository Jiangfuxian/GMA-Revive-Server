# -*- coding: utf-8 -*-
"""Embedded Android-only startup. BUNDLE_SOURCES and BUNDLE_HASHES are build inputs.

Executed by the original NeoX interpreter after its unmodified drpf definitions.
The source bundle never overrides drpf or the original patch_utils integrity API.
"""
import hashlib
import os
import sys
import types

PROFILE_ID = 'android_1_0_8_armv7'


class BundledSourceImporter(object):
    def __init__(self, sources, hashes):
        if set(sources) != set(hashes) or 'drpf' in sources:
            raise RuntimeError('ANDROID_STARTUP_BUNDLE_INVALID')
        for name, source in sources.items():
            if not name.replace('_', '').isalnum() or '.' in name:
                raise RuntimeError('ANDROID_STARTUP_MODULE_NAME_INVALID')
            if hashlib.sha256(source).hexdigest() != hashes[name]:
                raise RuntimeError('ANDROID_STARTUP_SOURCE_HASH_MISMATCH:' + name)
        self.sources, self.hashes = sources, hashes
        self.loaded = []

    def find_module(self, fullname, path=None):
        if fullname in self.sources:
            return self
        return None

    def find_spec(self, fullname, path=None, target=None):
        # Host verification uses Python 3.12; the game's Python 2 uses find_module.
        if fullname not in self.sources:
            return None
        import importlib.util
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        self._execute(module)

    def _execute(self, module):
        fullname = module.__name__
        source = self.sources[fullname]
        if hashlib.sha256(source).hexdigest() != self.hashes[fullname]:
            raise RuntimeError('ANDROID_STARTUP_SOURCE_HASH_MISMATCH:' + fullname)
        module.__file__ = '<hsqsl-android-offline>/' + fullname + '.py'
        module.__loader__ = self
        module.__package__ = ''
        try:
            # The native interpreter compiles its own opcode dialect. We do not
            # insert Python 3 or standard Python 2 bytecode into the game.
            eval(compile(source, module.__file__, 'exec'), module.__dict__)
        except BaseException:
            if sys.modules.get(fullname) is module:
                del sys.modules[fullname]
            raise
        self.loaded.append(fullname)

    def load_module(self, fullname):
        if fullname not in self.sources:
            raise ImportError(fullname)
        previous = sys.modules.get(fullname)
        if previous is not None:
            if getattr(previous, '__loader__', None) is not self:
                raise RuntimeError('ANDROID_STARTUP_MODULE_COLLISION:' + fullname)
            return previous
        module = types.ModuleType(fullname)
        sys.modules[fullname] = module
        self._execute(module)
        return module


def install(game=None, sources=None, hashes=None):
    if game is None:
        import game3d
        game = game3d
    sources = BUNDLE_SOURCES if sources is None else sources
    hashes = BUNDLE_HASHES if hashes is None else hashes
    if game.get_platform() != game.PLATFORM_ANDROID:
        raise RuntimeError('ANDROID_STARTUP_REQUIRES_ANDROID')
    root = game.get_doc_dir()
    if isinstance(root, bytes):
        root = root.decode(sys.getfilesystemencoding() or 'utf8')
    if not root or not os.path.isabs(root) or not os.path.isdir(root):
        raise RuntimeError('ANDROID_STARTUP_DOC_DIR_INVALID')
    root = os.path.realpath(root).replace('\\', '/').rstrip('/') + '/'
    importer = BundledSourceImporter(sources, hashes)
    existing = getattr(game, '_hsqsl_offline_startup', None)
    if existing is not None:
        if (existing['root'] == root and existing['profile'] == PROFILE_ID
                and existing['hashes'] == hashes):
            return False
        raise RuntimeError('ANDROID_STARTUP_RESTART_REQUIRED')
    collisions = sorted(name for name in sources if name in sys.modules)
    if collisions:
        raise RuntimeError('ANDROID_STARTUP_MODULE_COLLISION:' + ','.join(collisions))
    for name, value in [('HSQSL_OFFLINE_ROOT', root), ('HSQSL_OFFLINE_PROFILE', PROFILE_ID)]:
        previous = os.environ.get(name)
        if previous and previous.rstrip('/') != value.rstrip('/'):
            raise RuntimeError('ANDROID_STARTUP_ENV_CONFLICT:' + name)
    # Verify actual write access without modifying accounts, resource packs or saves.
    import tempfile
    handle = tempfile.NamedTemporaryFile(prefix='.hsqsl-startup-', dir=root)
    handle.write(b'write-check')
    handle.flush()
    handle.close()
    previous_env = dict((name, os.environ.get(name)) for name in
                        ('HSQSL_OFFLINE_ROOT', 'HSQSL_OFFLINE_PROFILE'))
    previous_import = __import__('__builtin__').__import__
    previous_exception = sys.excepthook
    os.environ['HSQSL_OFFLINE_ROOT'] = (root.encode(sys.getfilesystemencoding() or 'utf8')
                                      if sys.version_info[0] < 3 else root)
    os.environ['HSQSL_OFFLINE_PROFILE'] = PROFILE_ID
    sys.meta_path.insert(0, importer)
    try:
        # Loading the probe only defines its hooks. drpf is already initialized;
        # do not call its desktop resume(), which would reload drpf recursively.
        probe = importer.load_module('main_city_probe')
        probe.install_hooks()
        probe.event('android_startup.installed', root_source='game3d.get_doc_dir',
                    profile=PROFILE_ID, native_app_integrity='original_unmodified',
                    bundled_modules=len(sources))
    except BaseException:
        __import__('__builtin__').__import__ = previous_import
        sys.excepthook = previous_exception
        sys.meta_path.remove(importer)
        for name in list(sources):
            module = sys.modules.get(name)
            if module is not None and getattr(module, '__loader__', None) is importer:
                del sys.modules[name]
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        raise
    game._hsqsl_offline_startup = {'root': root, 'profile': PROFILE_ID,
                                  'hashes': dict(hashes), 'importer': importer}
    return True


if globals().get('ANDROID_STARTUP_AUTORUN'):
    install()
