# -*- coding: utf-8 -*-
"""Exact, independently checked runtime identities; never rewrite native versions."""
import os


PROFILES = {
    'pc_1_0_4': {
        'platform': 'pc',
        'app_version': '1.0.4',
        'script_version': '1.0.95',
    },
    'android_1_0_8_armv7': {
        'platform': 'android',
        'app_version': '1.0.8',
        'script_version': '1.0.125',
    },
}


def current_profile():
    name = os.environ.get('HSQSL_OFFLINE_PROFILE', 'pc_1_0_4')
    if name not in PROFILES:
        raise RuntimeError('OFFLINE_RUNTIME_PROFILE_UNKNOWN:' + name)
    value = dict(PROFILES[name])
    value['id'] = name
    return value


def verify_patch_identity(patch_utils, profile=None):
    profile = profile or current_profile()
    script = patch_utils.get_cur_patch_version()
    if script != profile['script_version']:
        raise RuntimeError('OFFLINE_SCRIPT_VERSION_MISMATCH:%s:%s' %
                           (script, profile['script_version']))
    app = None
    if profile['platform'] == 'android':
        app = patch_utils.get_app_base_version()
        if app != profile['app_version']:
            raise RuntimeError('OFFLINE_APP_VERSION_MISMATCH:%s:%s' %
                               (app, profile['app_version']))
    return {'profile': profile['id'], 'app_version': app,
            'script_version': script}
