"""Strict settings forwarding. Credentials stay in the inherited environment."""
import json
import math
from pathlib import Path
import re


POLICY = {'camera_restart_limit': ('--camera-restart-limit', int, 0),
          'reference_restart_limit': ('--reference-restart-limit', int, 0),
          'camera_restart_delay': ('--camera-restart-delay', float, 0),
          'reference_restart_delay': ('--reference-restart-delay', float, 0),
          'preflight_timeout': ('--preflight-timeout', float, 0),
          'buffer_mib': ('--buffer-mib', int, 8)}


def arguments(settings):
    allowed = {'camera_id', 'camera_ids', 'model_path', 'mode', 'runner', 'primary_camera_id',
               'continuous_config', 'continuous_profiles', 'failure_policy'} | set(POLICY)
    if not isinstance(settings, dict) or set(settings) - allowed:
        raise ValueError('Unknown inference settings; credentials do not belong in this file')
    cameras = settings.get('camera_ids', [settings.get('camera_id', '')])
    if (not isinstance(cameras, list) or not cameras or
            any(not isinstance(c, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', c) for c in cameras) or
            len(set(cameras)) != len(cameras)):
        raise ValueError('camera_ids must be distinct camera identifiers')
    model = Path(settings.get('model_path', ''))
    if not model.is_absolute() or not model.is_file() or model.suffix.lower() != '.pt':
        raise ValueError('Existing absolute .pt model path required')
    mode = settings.get('mode')
    if mode not in {'inspect', 'review', 'events'}:
        raise ValueError('mode must be inspect, review or events')
    runner = settings.get('runner', 'legacy')
    if runner not in {'legacy', 'continuous'}:
        raise ValueError('runner must be legacy or continuous')
    result = ['--model-path', str(model), '--runner', runner]
    for camera in cameras:
        result += ['--camera-id', camera]
    if mode != 'inspect':
        result.append('--submit-review-candidates' if mode == 'review' else '--submit-events')
    if 'failure_policy' in settings:
        if settings['failure_policy'] not in {'isolate', 'fail-fast'}:
            raise ValueError('Invalid failure_policy')
        result += ['--failure-policy', settings['failure_policy']]
    for key, (flag, kind, lower) in POLICY.items():
        if key not in settings:
            continue
        value = settings[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                (kind is int and type(value) is not int) or not math.isfinite(value) or
                value < lower or (kind is float and value == 0)):
            raise ValueError('Invalid '+key)
        result += [flag, str(value)]
    if runner == 'continuous':
        if mode == 'inspect':
            raise ValueError('Continuous requires explicit submission mode')
        if 'continuous_profiles' in settings:
            profiles = settings['continuous_profiles']
            if (not isinstance(profiles, dict) or set(profiles) != set(cameras) or
                    {'primary_camera_id', 'continuous_config'} & set(settings)):
                raise ValueError('Profiles must exactly match detection primaries; choose one profile form')
            for primary, filename in profiles.items():
                config = Path(filename)
                if not config.is_absolute() or not config.is_file():
                    raise ValueError('Existing absolute profile required')
                result += ['--continuous-profile', primary+'='+str(config)]
        else:
            primary = settings.get('primary_camera_id')
            config = Path(settings.get('continuous_config', ''))
            if cameras != [primary] or not config.is_absolute() or not config.is_file():
                raise ValueError('Single profile detects only its primary; references belong in the profile')
            result += ['--primary-camera-id', primary, '--continuous-config', str(config)]
        if settings.get('camera_restart_limit', 0) != 0 or settings.get('reference_restart_limit', 0) != 0:
            raise ValueError('Continuous ownership uses no automatic process restart')
    elif {'primary_camera_id', 'continuous_config', 'continuous_profiles'} & set(settings):
        raise ValueError('Legacy mode cannot accept continuous profile/primary fields')
    return result


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))
