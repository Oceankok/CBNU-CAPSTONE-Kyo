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


def child_main(module):
    """Preserve permanent request/config errors across the process boundary."""
    try:
        runpy.run_module(module, run_name='__main__')
    except Exception as error:
        status = getattr(getattr(error, 'response', None), 'status_code', None)
        # Never log exception bodies: they may contain tokens or server URLs.
        print(f'[CHILD ERROR] type={type(error).__name__} status={status}', flush=True)
        return FATAL_EXIT if status in {400, 401, 403, 422} else 1
    return 0


def preflight(cameras, model, mode):
    """Run once in a short-lived process, before any inference peer starts."""
    from inference_worker.video_client import VideoClient
    from inference_worker.run_server import create_detector
    from inference_worker.person_tracker import PersonTracker
    client = VideoClient()
    try:
        for camera in cameras:
            client.get_ppe_policy(camera)
    finally:
        client.close()
    detector = create_detector(model)
    PersonTracker()  # Fail before peers launch if tracker dependencies/config are invalid.
    if mode == '--submit-events' and detector.supports_no_helmet is False:
        raise ValueError('Event submission requires no_helmet classes')
    if mode == '--submit-review-candidates' and detector.supports_no_helmet is not False:
        raise ValueError('Review-only mode requires a three-class model')


def run_preflight(command):
    child = subprocess.Popen(command)
    try:
        if child.wait() != 0:
            raise RuntimeError('Shared configuration/auth/model preflight failed; no peers started')
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def commands(cameras, model, stop_file, *, mode=None, buffer_mib=256):
    result = []
    for camera in cameras:
        command = [sys.executable, '-m', 'inference_worker.run_multi',
                   '--child-module', 'inference_worker.run_server',
                   '--camera-id', camera, '--model-path', str(model),
                   '--max-chunks', str(2**31-1), '--buffer-mib', str(buffer_mib),
                   '--stop-file', str(stop_file)]
        if mode:
            command.append(mode)
        result.append(command)
    result.append([sys.executable, '-m', 'inference_worker.run_multi',
                   '--child-module', 'inference_worker.reference_collector',
                   '--stop-file', str(stop_file)])
    return result


def supervise(command_list, stop_file, *, camera_count=0, restart_limit=0, restart_delay=0,
              reference_restart_limit=0, reference_restart_delay=0, failure_policy='fail-fast'):
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
    retries = [0] * len(command_list)
    restart_at = {}
    inactive = set()
    environment = os.environ.copy()
    # Keep all decoder/clip temporary files within this supervisor's lifetime.
    environment.update(TEMP=str(stop_file.parent), TMP=str(stop_file.parent))
    try:
        for command in command_list:
            if stop_file.exists():
                break
            children.append(subprocess.Popen(command, env=environment))
        while not stop_file.exists():
            for index, child in enumerate(children):
                if index in inactive or child.poll() is None:
                    continue
                if stop_file.exists():
                    break
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
                    children[index] = subprocess.Popen(command_list[index], env=environment)
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
        stop_file.touch()
        # Allow current HTTP requests/reference assembly to release their files.
        deadline = time.monotonic() + 70
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera-id', action='append', required=True)
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--buffer-mib', type=int, default=256)
    parser.add_argument('--failure-policy', choices=['isolate', 'fail-fast'], default='isolate',
                        help='After retry exhaustion keep healthy peers (default) or stop all peers')
    parser.add_argument('--camera-restart-limit', type=int, default=3,
                        help='Total restart attempts per camera over this run (default: 3)')
    parser.add_argument('--camera-restart-delay', type=float, default=5,
                        help='Positive seconds between attempts (default: 5)')
    parser.add_argument('--reference-restart-limit', type=int, default=3,
                        help='Total reference retries over this run (default: 3)')
    parser.add_argument('--reference-restart-delay', type=float, default=5,
                        help='Positive backoff seconds for reference recovery')
    parser.add_argument('--preflight-only', action='store_true', help=argparse.SUPPRESS)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--submit-events', action='store_true')
    modes.add_argument('--submit-review-candidates', action='store_true')
    args = parser.parse_args(argv)
    if (any(not camera.strip() for camera in args.camera_id) or
            len(set(args.camera_id)) != len(args.camera_id)):
        parser.error('Camera IDs must be nonempty and unique')
    if not args.model_path.is_file() or args.buffer_mib < 8:
        parser.error('An existing local model and buffer budget of at least 8 MiB are required')
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
        try:
            preflight(args.camera_id, args.model_path.resolve(), mode)
        except Exception as error:
            print(f'[PREFLIGHT FAILED] type={type(error).__name__}; check configuration/auth/model',
                  file=sys.stderr, flush=True)
            raise SystemExit(FATAL_EXIT) from None
        return
    # Do not keep a third model/GPU context resident in the supervisor.
    run_preflight([sys.executable, '-m', 'inference_worker.run_multi', '--preflight-only',
                   *(sys.argv[1:] if argv is None else argv)])
    print(f'[SUPERVISOR] policy={args.failure_policy} camera_retries={args.camera_restart_limit} '
          f'reference_retries={args.reference_restart_limit}', flush=True)
    with tempfile.TemporaryDirectory(prefix='ppe_supervisor_') as folder:
        stop = Path(folder)/'stop'
        supervise(commands(args.camera_id, args.model_path.resolve(), stop,
                            mode=mode, buffer_mib=args.buffer_mib), stop,
                  camera_count=len(args.camera_id), restart_limit=args.camera_restart_limit,
                  restart_delay=args.camera_restart_delay or 0,
                  reference_restart_limit=args.reference_restart_limit,
                  reference_restart_delay=args.reference_restart_delay or 0,
                  failure_policy=args.failure_policy)


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--child-module':
        module = sys.argv[2]
        if module not in {'inference_worker.run_server', 'inference_worker.reference_collector'}:
            raise SystemExit(2)
        sys.argv = [module, *sys.argv[3:]]
        raise SystemExit(child_main(module))
    main()
