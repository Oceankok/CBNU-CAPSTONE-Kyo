"""Build fresh, session-bound continuous profiles before loading a detector.

This never enables a server policy or certifies manual evidence. The source must
publish authenticated clock proofs first. Old sessions/profiles are not reused.
"""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from uuid import uuid4

import httpx


def load_template(path, cameras):
    from clock_contract import ClockPolicy, number
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    keys = {'version', 'source_node_ids', 'server_epoch', 'policy', 'wait_seconds',
            'poll_seconds', 'proof_poll_seconds', 'max_host_rtt_seconds',
            'state_root', 'profile'}
    if not isinstance(value, dict) or set(value) != keys or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('automatic_template_invalid')
    if (not isinstance(value['source_node_ids'], dict) or set(value['source_node_ids']) != set(cameras) or not value['server_epoch']
            or any(not isinstance(v, str) or not v for v in value['source_node_ids'].values())):
        raise ValueError('automatic_camera_scope_invalid')
    policy = ClockPolicy(**value['policy'])
    for key in ('wait_seconds', 'poll_seconds', 'proof_poll_seconds', 'max_host_rtt_seconds'):
        if number(value[key]) <= 0:
            raise ValueError('automatic_wait_policy_invalid')
    if (value['wait_seconds'] > 600 or value['poll_seconds'] > value['wait_seconds']
            or value['proof_poll_seconds'] >= policy.ttl_seconds / 2
            or value['max_host_rtt_seconds'] >= policy.ttl_seconds / 2):
        raise ValueError('automatic_wait_policy_invalid')
    root = Path(value['state_root'])
    if not root.is_absolute() or root.is_symlink():
        raise ValueError('automatic_private_state_required')
    profile = value['profile']
    if not isinstance(profile, dict) or {'clocks', 'state_directory', 'clock_proof'} & set(profile):
        raise ValueError('automatic_profile_template_invalid')
    if (profile.get('capture', {}).get('manual_review') or profile.get('mux', {}).get('manual_review')
            or profile.get('source', {}).get('tolerate_source_gaps')
            or profile.get('lifecycle', {}).get('review_episode_continuity')
            or profile.get('lifecycle', {}).get('evidence_mode', 'verified_continuous') != 'verified_continuous'):
        raise ValueError('automatic_manual_evidence_forbidden')
    # Bootstrap may write only a separate private runtime tree, never project
    # source or an ancestor containing the template/source checkout.
    source = Path(__file__).resolve().parents[2]
    if (source.is_relative_to(root.resolve()) or root.resolve().is_relative_to(source)
            or Path(path).resolve().is_relative_to(root.resolve())):
        raise ValueError('automatic_state_contains_source_or_template')
    return value


def _snapshot(client, template, cameras, *, mono):
    from clock_contract import ClockPolicy, sample_interval, within_envelope, number
    from inference_worker.clock_leases import IDENTITY
    policy = ClockPolicy(**template['policy'])
    streams = client.list_streams()
    proofs, bindings, clocks = {}, {}, {}
    boot = None
    for camera in cameras:
        selected = [s for s in streams if s.get('camera_id') == camera
                    and s.get('capture_stopped') is False and s.get('capture_context_id')]
        if len(selected) > 1:
            raise ValueError('automatic_ambiguous_active_streams')
        if not selected:
            return None
        stream = selected[0]
        stream_id = stream.get('stream_id', '')
        from uuid import UUID
        if str(UUID(stream_id)) != stream_id:
            raise ValueError('automatic_stream_identity_invalid')
        started = mono()
        response = client.client.get('/api/processing/video/streams/' + stream_id + '/clock-proofs')
        elapsed = mono() - started
        if response.status_code == 404:
            return None
        response.raise_for_status()
        if elapsed < 0 or elapsed > template['max_host_rtt_seconds']:
            raise ValueError('automatic_proof_rtt_exceeded')
        result = response.json()
        proof = result['proof']
        if (result['version'] != 1 or proof['version'] != 1
                or result['server_epoch'] != template['server_epoch']
                or proof['server_epoch'] != template['server_epoch']
                or proof['policy'] != policy.public()
                or proof['server_boot_id'] != result['server_boot_id']
                or not result['server_boot_id']
                or (boot is not None and boot != result['server_boot_id'])
                or proof['camera_id'] != camera or proof['stream_id'] != stream_id
                or proof['source_node_id'] != template['source_node_ids'][camera]):
            raise ValueError('automatic_proof_scope_mismatch')
        if stream.get('capture_context_id') != proof['capture_context_id']:
            raise ValueError('automatic_context_scope_mismatch')
        boot = result['server_boot_id']
        low, high = sample_interval(proof['server_sample'], proof['source_sent_at'], proof['source_received_at'],
                                    proof['monotonic_sent'], proof['monotonic_received'], policy)
        if (low != proof['offset_low'] or high != proof['offset_high']
                or proof['valid_until'] != proof['server_sample'] + policy.ttl_seconds):
            raise ValueError('automatic_proof_interval_invalid')
        # Reject stale/near-expired proofs. A fresh proof can be published while
        # waiting; its interval is measured, never extended here.
        remaining = number(proof['valid_until']) - number(result['server_time']) - elapsed - policy.drift()
        if remaining <= 2 * template['proof_poll_seconds']:
            return None
        offset = (low + high) / 2
        envelope = policy.max_error_seconds
        if not within_envelope(proof, offset, envelope, policy):
            raise ValueError('automatic_proof_outside_envelope')
        clocks[camera] = {'offset': offset, 'error_bound': envelope, 'verified': True,
                          'epoch': template['server_epoch'],
                          'valid_until': datetime.fromtimestamp(proof['valid_until'], timezone.utc).isoformat()}
        bindings[camera] = {key: proof[key] for key in IDENTITY}
        proofs[camera] = proof
    return clocks, bindings, proofs


def prepare(settings, client, *, mono=time.monotonic, sleep=time.sleep):
    """Wait for new proved streams and return continuous settings, never mutate input."""
    if (settings.get('runner') != 'continuous' or settings.get('mode') != 'review'
            or {'continuous_profiles', 'continuous_config', 'primary_camera_id'} & set(settings)):
        raise ValueError('automatic_bootstrap_selection_invalid')
    cameras = settings.get('camera_ids')
    if (not isinstance(cameras, list) or not cameras
            or any(not isinstance(c, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', c) for c in cameras)
            or len(set(cameras)) != len(cameras)):
        raise ValueError('automatic_camera_scope_invalid')
    template = load_template(settings['continuous_bootstrap'], cameras)
    deadline = mono() + template['wait_seconds']
    print('[AUTOMATIC INPUT] Waiting for fresh authenticated source proofs; inference has not started.', flush=True)
    while True:
        try:
            snapshot = _snapshot(client, template, cameras, mono=mono)
        except httpx.TransportError:
            snapshot = None
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in (429, 500, 502, 504):
                raise
            snapshot = None
        if snapshot is not None and mono() < deadline:
            break
        if mono() >= deadline:
            raise ValueError('automatic_source_proof_timeout')
        sleep(min(template['poll_seconds'], max(0, deadline - mono())))
    clocks, bindings, _ = snapshot
    proof_config = {'version': 1, 'origin': client.server_url, 'server_epoch': template['server_epoch'],
                    'policy': template['policy'], 'bindings': bindings,
                    'poll_seconds': template['proof_poll_seconds'],
                    'max_host_rtt_seconds': template['max_host_rtt_seconds']}
    # Reuse the same verifier as inference, including identity, freshness,
    # boot changes and immutable error-envelope checks before writing profiles.
    from inference_worker.clock_leases import ClockLeaseSet
    lease = ClockLeaseSet(client, proof_config, clocks)
    lease.refresh(force=True)
    root = Path(template['state_root']) / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    profile_dir = root / 'profiles'
    profile_dir.mkdir()
    result = copy.deepcopy(settings)
    result.pop('continuous_bootstrap')
    result['continuous_profiles'] = {}
    from inference_worker.run_continuous import read_config
    for camera in cameras:
        profile = copy.deepcopy(template['profile'])
        profile.update(state_directory=str(root / ('state-' + camera)), clocks=clocks, clock_proof=proof_config)
        path = profile_dir / (camera + '.json')
        path.write_text(json.dumps(profile, indent=2), encoding='utf-8')
        read_config(path, camera, now=time.time())
        result['continuous_profiles'][camera] = str(path)
    print('[AUTOMATIC INPUT] Fresh session profiles verified; rule eligibility is decided by the server.', flush=True)
    return result


def validate_selection(settings):
    """Check static settings without network IO or declaring live readiness."""
    if (settings.get('runner') != 'continuous' or settings.get('mode') != 'review'
            or {'continuous_profiles', 'continuous_config', 'primary_camera_id'} & set(settings)):
        raise ValueError('automatic_bootstrap_selection_invalid')
    from deploy.host_runtime.settings import arguments
    common = dict(settings)
    template = common.pop('continuous_bootstrap')
    common['runner'] = 'legacy'
    arguments(common)
    return load_template(template, common['camera_ids'])
