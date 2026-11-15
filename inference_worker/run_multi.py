"""Manually supervise camera inference and one group-reference collector.

Credentials use VideoClient's existing environment; never pass them as arguments.
Each camera has its own model, buffer, tracking state and inference lease.
"""
import argparse
import math
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time


FATAL_EXIT = 78


class ContinuousProtocolError(RuntimeError):
    """Safe startup reason; no response bodies or authentication data."""


def probe_continuous_protocol(client):
    import httpx
    try:
        response = client.client.get('/api/processing/continuous-events/active', params={'limit': 1})
        if response.status_code == 503:
            try:
                disabled = response.json().get('detail') == 'continuous_events_disabled'
            except (ValueError, AttributeError):
                disabled = False
            if disabled:
                raise ContinuousProtocolError('disabled')
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        code = error.response.status_code
        raise ContinuousProtocolError('auth' if code in {401, 403} else f'http_{code}') from None
    except httpx.TransportError:
        raise ContinuousProtocolError('unreachable') from None
    try:
        result = response.json()
        if (not isinstance(result, dict) or not isinstance(result.get('items'), list) or
                len(result['items']) > 1 or 'next_after' not in result or
                (result['next_after'] is not None and not isinstance(result['next_after'], str))):
            raise ValueError()
    except (ValueError, TypeError):
        raise ContinuousProtocolError('invalid_response') from None


def validate_continuous_selection(path, primary, cameras=None):
    from inference_worker.run_continuous import read_config
    config = read_config(path, primary, now=time.time())
    if cameras is not None and list(cameras) != [primary]:
        raise ValueError('Single-profile selection detects only its primary; references are in the profile')
    state = Path(config['state_directory'])
    # State is owned/locked by CaptureOutbox at runtime; never create/clean it here.
    if state.exists() and (not state.is_dir() or state.is_symlink()):
        raise ValueError('Private state must be an ordinary owned directory')
    project = Path(__file__).resolve().parents[1]
    if project.is_relative_to(state.resolve()) or Path(path).resolve().is_relative_to(state.resolve()):
        raise ValueError('State cannot contain project source or its profile')
    return config


def continuous_profiles(cameras, *, entries=None, path=None, primary=None, validate=True):
    profiles = {}
    if entries:
        if path is not None or primary is not None:
            raise ValueError('Choose profiles or single-profile fields, not both')
        for entry in entries:
            camera, separator, filename = entry.partition('=')
            if not separator or not filename or camera in profiles:
                raise ValueError('Distinct PRIMARY=PROFILE entries required')
            profiles[camera] = Path(filename)
    else:
        if path is None or primary is None:
            raise ValueError('Explicit continuous primary/profile required')
        profiles[primary] = Path(path)
    if set(profiles) != set(cameras):
        raise ValueError('Camera selection must exactly equal detection primaries')
    states = []
    for camera, filename in profiles.items():
        if not filename.is_absolute() or not filename.is_file():
            raise ValueError('Existing absolute continuous profile required')
        if not validate:
            continue
        config = validate_continuous_selection(filename, camera)
        state = Path(config['state_directory']).resolve()
        if any(state.is_relative_to(old) or old.is_relative_to(state) for old in states):
            raise ValueError('Continuous state directories must be distinct and non-overlapping')
        states.append(state)
    return profiles


def child_main(module):
    """Preserve permanent request/config errors across the process boundary."""
    try:
        runpy.run_module(module, run_name='__main__')
    except Exception as error:
        status = getattr(getattr(error, 'response', None), 'status_code', None)
        # Never log exception bodies: they may contain tokens or server URLs.
        reason = ''
        if type(error).__name__ == 'SourceMuxError' and str(error) in {
                'source_initial_frame_timeout', 'camera_missing_or_stalled', 'camera_chunk_discontinuity'}:
            reason = ' reason=' + str(error)
        print(f'[CHILD ERROR] type={type(error).__name__} status={status}{reason}', flush=True)
        return FATAL_EXIT if status in {400, 401, 403, 422} else 1
    return 0


def preflight(cameras, model, mode, *, continuous=False):
    """Run once in a short-lived process, before any inference peer starts."""
    from inference_worker.video_client import VideoClient
    from inference_worker.run_server import create_detector
    from inference_worker.person_tracker import PersonTracker
    from inference_worker.ppe_rules import assess_ppe
    client = VideoClient()
    try:
        if continuous or mode=='--submit-review-candidates':
            probe_continuous_protocol(client)
        for camera in cameras:
            client.get_ppe_policy(camera)
    finally:
        client.close()
    detector = create_detector(model)
    PersonTracker()  # Fail before peers launch if tracker dependencies/config are invalid.
    assess_ppe([], (64, 64, 3))  # Exercise lazy assignment dependencies without inference.
    if mode == '--submit-events' and detector.supports_no_helmet is False:
        raise ValueError('Event submission requires no_helmet classes')


def run_preflight(command, *, timeout=120, stop_file=None, process_trees=False):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Preflight timeout must be finite and positive')
    from deploy.host_runtime.processes import ProcessTrees
    trees = ProcessTrees() if process_trees else None
    child = None
    try:
        if stop_file is not None and stop_file.exists():
            return False
        child = trees.spawn(command) if trees else subprocess.Popen(command)
        deadline = time.monotonic() + timeout
        while child.poll() is None:
            if stop_file is not None and stop_file.exists():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('Shared preflight timed out; no peers started')
            try:
                child.wait(timeout=min(.2, remaining))
            except subprocess.TimeoutExpired:
                pass
        if child.returncode != 0:
            raise RuntimeError('Shared configuration/auth/model preflight failed; no peers started')
        return True
    finally:
        try:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        finally:
            if trees:
                trees.close()


def commands(cameras, model, stop_file, *, mode=None, buffer_mib=256,
             continuous_config=None, primary_camera=None, profiles=None):
    if continuous_config is not None or profiles is not None:
        profiles = profiles or {primary_camera: continuous_config}
        if set(profiles) != set(cameras) or not mode:
            raise ValueError('Continuous runner requires a configured primary and submission mode')
        return [[sys.executable, '-m', 'inference_worker.run_multi', '--child-module',
                 'inference_worker.run_server', '--camera-id', camera,
                 '--model-path', str(model), '--max-chunks', str(2**31-1),
                 '--buffer-mib', str(buffer_mib), '--stop-file', str(stop_file),
                 '--continuous-config', str(config), mode] for camera, config in profiles.items()]
    result = []
    for camera in cameras:
        command = [sys.executable, '-m', 'inference_worker.run_multi',
                   '--child-module', 'inference_worker.run_server',
                   '--camera-id', camera, '--model-path', str(model),
                   '--max-chunks', str(2**31-1), '--buffer-mib', str(buffer_mib),
                   '--stop-file', str(stop_file)]
        if mode:
            command.append(mode)
        if mode=='--submit-review-candidates':
            for reference in cameras:
                if reference!=camera:command+=['--reference-camera-id',reference]
        result.append(command)
    if mode=='--submit-review-candidates':return result
    result.append([sys.executable, '-m', 'inference_worker.run_multi',
                   '--child-module', 'inference_worker.reference_collector',
                   '--stop-file', str(stop_file)])
    return result


def supervise(command_list, stop_file, *, camera_count=0, restart_limit=0, restart_delay=0,
              reference_restart_limit=0, reference_restart_delay=0, failure_policy='fail-fast',
              process_trees=False, temporary_root=None, shutdown_grace_seconds=70):
    if not math.isfinite(shutdown_grace_seconds) or shutdown_grace_seconds < 0:
        raise ValueError('Invalid shutdown grace')
    if failure_policy not in {'isolate', 'fail-fast'}:
        raise ValueError('Invalid failure policy')
    if failure_policy == 'isolate' and not camera_count:
        raise ValueError('Isolation requires camera slots')
    if (restart_limit < 0 or not math.isfinite(restart_delay) or restart_delay < 0 or
            not 0 <= camera_count <= len(command_list)):
        raise ValueError("Invalid camera restart policy")
    if restart_limit and (not camera_count or restart_delay <= 0):
        raise ValueError("Camera recovery requires a positive delay and camera count")
    if (reference_restart_limit < 0 or not math.isfinite(reference_restart_delay) or
            reference_restart_delay < 0):
        raise ValueError("Invalid reference restart policy")
    if reference_restart_limit and (camera_count != len(command_list)-1 or reference_restart_delay <= 0):
        raise ValueError("Reference recovery requires one reference slot and a positive delay")
    children = []
    from deploy.host_runtime.processes import ProcessTrees
    trees = ProcessTrees() if process_trees else None
    spawn = trees.spawn if trees else subprocess.Popen
    retries = [0] * len(command_list)
    restart_at = {}
    inactive = set()
    environment = os.environ.copy()
    # Keep all decoder/clip temporary files within this supervisor's lifetime.
    environment.update(TEMP=str(temporary_root or stop_file.parent), TMP=str(temporary_root or stop_file.parent))
    try:
        for command in command_list:
            if stop_file.exists():
                break
            children.append(spawn(command, env=environment))
        while not stop_file.exists():
            for index, child in enumerate(children):
                if index in inactive or child.poll() is None:
                    continue
                if stop_file.exists():
                    break
                if trees:
                    trees.release(child)
                is_camera = index < camera_count
                limit = restart_limit if is_camera else reference_restart_limit
                delay = restart_delay if is_camera else reference_restart_delay
                role = 'CAMERA' if is_camera else 'REFERENCE'
                if child.returncode in {2, FATAL_EXIT}:
                    raise RuntimeError(f'{role.title()} configuration/auth failure; all peers are stopping')
                if failure_policy == 'isolate' and child.returncode == 0:
                    inactive.add(index)
                    print(f'[{role} STOPPED] slot={index} exit=0; no restart', flush=True)
                    continue
                if failure_policy == 'isolate' and retries[index] >= limit:
                    inactive.add(index)
                    print(f'[{role} UNAVAILABLE] slot={index} retries={retries[index]}; '
                          'evidence continuity not guaranteed; healthy peers continue', flush=True)
                    continue
                if not limit:
                    raise RuntimeError('A camera or reference collector exited; all peers are stopping')
                if index not in restart_at:
                    if retries[index] >= limit:
                        raise RuntimeError(f'{role.title()} restart budget exhausted; all peers are stopping')
                    restart_at[index] = time.monotonic() + delay
                    print(f'[{role} RESTART] slot={index} exit={child.returncode}; waiting {delay}s', flush=True)
                if time.monotonic() < restart_at[index]:
                    continue
                if stop_file.exists():
                    break
                retries[index] += 1
                del restart_at[index]
                try:
                    children[index] = spawn(command_list[index], env=environment)
                except OSError:
                    # Keep the dead child in its slot, consuming this attempt.
                    # A subsequent loop either schedules a bounded retry or fails.
                    print(f'[{role} RESTART] slot={index} launch failed', flush=True)
            if failure_policy == 'isolate' and all(index in inactive for index in range(camera_count)):
                raise RuntimeError('No active camera slots remain; all peers are stopping')
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            stop_file.touch()
            # Allow current requests/capture to release their files cooperatively.
            deadline = time.monotonic() + shutdown_grace_seconds
            for child in children:
                try:
                    child.wait(timeout=max(0, deadline-time.monotonic()))
                except subprocess.TimeoutExpired:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        finally:
            if trees:
                trees.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera-id', action='append', required=True)
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--buffer-mib', type=int, default=256)
    parser.add_argument('--runner', choices=['legacy', 'continuous'], default='legacy')
    parser.add_argument('--primary-camera-id')
    parser.add_argument('--continuous-config', type=Path)
    parser.add_argument('--continuous-profile', action='append',
                        help='Repeat PRIMARY=ABSOLUTE_PROFILE for multiple detection primaries')
    parser.add_argument('--stop-file', type=Path, help='Optional externally owned stop signal')
    parser.add_argument('--preflight-timeout', type=float, default=120,
                        help='Finite shared initialization deadline in seconds (default: 120)')
    parser.add_argument('--failure-policy', choices=['isolate', 'fail-fast'], default='isolate',
                        help='After retry exhaustion keep healthy peers (default) or stop all peers')
    parser.add_argument('--camera-restart-limit', type=int,
                        help='Total restart attempts (legacy default: 3; continuous: 0)')
    parser.add_argument('--camera-restart-delay', type=float, default=5,
                        help='Positive seconds between attempts (default: 5)')
    parser.add_argument('--reference-restart-limit', type=int,
                        help='Total reference retries (legacy default: 3; continuous: 0)')
    parser.add_argument('--reference-restart-delay', type=float, default=5,
                        help='Positive backoff seconds for reference recovery')
    parser.add_argument('--preflight-only', action='store_true', help=argparse.SUPPRESS)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--submit-events', action='store_true')
    modes.add_argument('--submit-review-candidates', action='store_true')
    args = parser.parse_args(argv)
    for name in ('camera_restart_limit', 'reference_restart_limit'):
        if getattr(args, name) is None:
            setattr(args, name, 0 if args.runner == 'continuous' else 3)
    if args.runner == 'continuous' and (args.camera_restart_limit or args.reference_restart_limit):
        parser.error('Continuous process restart is disabled; reconcile durable state before relaunch')
    if (any(not camera.strip() for camera in args.camera_id) or
            len(set(args.camera_id)) != len(args.camera_id)):
        parser.error('Camera IDs must be nonempty and unique')
    if not args.model_path.is_file() or args.buffer_mib < 8:
        parser.error('An existing local model and buffer budget of at least 8 MiB are required')
    if not math.isfinite(args.preflight_timeout) or args.preflight_timeout <= 0:
        parser.error('Preflight timeout must be finite and positive')
    if args.runner == 'continuous':
        if not (args.submit_events or args.submit_review_candidates):
            parser.error('Continuous selection requires config, primary camera and submission mode')
        if not args.continuous_profile and (args.continuous_config is None or args.primary_camera_id is None):
            parser.error('Continuous profile selection required')
    elif args.continuous_config is not None or args.primary_camera_id is not None or args.continuous_profile:
        parser.error('Continuous config/primary requires --runner continuous')
    if (args.camera_restart_limit < 0 or
            (args.camera_restart_limit and
             (args.camera_restart_delay is None or args.camera_restart_delay <= 0)) or
            (args.camera_restart_delay is not None and
             (not math.isfinite(args.camera_restart_delay) or args.camera_restart_delay <= 0))):
        parser.error('Recovery needs a nonnegative limit and a finite positive delay')
    if (args.reference_restart_limit < 0 or
            (args.reference_restart_limit and
             (args.reference_restart_delay is None or args.reference_restart_delay <= 0)) or
            (args.reference_restart_delay is not None and
             (not math.isfinite(args.reference_restart_delay) or args.reference_restart_delay <= 0))):
        parser.error('Reference recovery needs a nonnegative limit and a finite positive delay')
    mode = ('--submit-events' if args.submit_events else
            '--submit-review-candidates' if args.submit_review_candidates else None)
    if args.preflight_only:
        stage = 'shared_auth_model_dependencies'
        try:
            if args.runner == 'continuous':
                stage = 'continuous_clock_calibration_snapshot_state'
                continuous_profiles(args.camera_id, entries=args.continuous_profile,
                                    path=args.continuous_config, primary=args.primary_camera_id)
                stage = 'shared_auth_model_dependencies'
            preflight(args.camera_id, args.model_path.resolve(), mode, continuous=args.runner == 'continuous')
        except Exception as error:
            if isinstance(error, ContinuousProtocolError):
                stage = 'continuous_protocol_'+str(error)
            print(f'[PREFLIGHT FAILED] stage={stage} type={type(error).__name__}; check configuration/auth/model',
                  file=sys.stderr, flush=True)
            raise SystemExit(FATAL_EXIT) from None
        return
    # Do not keep a third model/GPU context resident in the supervisor.
    from deploy.host_runtime.ownership import primary_ownership
    with primary_ownership(os.environ.get('PPE_SERVER_URL', 'http://127.0.0.1:8000'), args.camera_id), \
            tempfile.TemporaryDirectory(prefix='ppe_supervisor_') as folder:
        stop = args.stop_file.resolve() if args.stop_file else Path(folder)/'stop'
        # External stop is honored during initialization as well as supervision.
        if not run_preflight([sys.executable, '-m', 'inference_worker.run_multi', '--preflight-only',
                              *(sys.argv[1:] if argv is None else argv)],
                             timeout=args.preflight_timeout, stop_file=stop, process_trees=True):
            return
        continuous = args.runner == 'continuous'
        profiles = (continuous_profiles(args.camera_id, entries=args.continuous_profile,
                    path=args.continuous_config, primary=args.primary_camera_id, validate=False) if continuous else None)
        print(f'[SUPERVISOR] runner={args.runner} policy={args.failure_policy} '
              f'camera_retries={0 if continuous else args.camera_restart_limit} '
              f'reference_retries={0 if continuous or mode=="--submit-review-candidates" else args.reference_restart_limit}', flush=True)
        supervise(commands(args.camera_id, args.model_path.resolve(), stop,
                            mode=mode, buffer_mib=args.buffer_mib,
                            profiles=profiles), stop,
                  camera_count=len(args.camera_id),
                  restart_limit=0 if continuous else args.camera_restart_limit,
                  restart_delay=args.camera_restart_delay or 0,
                  reference_restart_limit=0 if continuous or mode=='--submit-review-candidates' else args.reference_restart_limit,
                  reference_restart_delay=args.reference_restart_delay or 0,
                  failure_policy=args.failure_policy, process_trees=True, temporary_root=Path(folder))


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--child-module':
        module = sys.argv[2]
        if module not in {'inference_worker.run_server', 'inference_worker.reference_collector'}:
            raise SystemExit(2)
        sys.argv = [module, *sys.argv[3:]]
        raise SystemExit(child_main(module))
    main()
