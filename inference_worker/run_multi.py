"""Manually supervise camera inference and one group-reference collector.

Credentials use VideoClient's existing environment; never pass them as arguments.
Each camera has its own model, buffer, tracking state and inference lease.
"""
import argparse
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def commands(cameras, model, stop_file, *, mode=None, buffer_mib=256):
    result = []
    for camera in cameras:
        command = [sys.executable, '-m', 'inference_worker.run_server',
                   '--camera-id', camera, '--model-path', str(model),
                   '--max-chunks', str(2**31-1), '--buffer-mib', str(buffer_mib),
                   '--stop-file', str(stop_file)]
        if mode:
            command.append(mode)
        result.append(command)
    result.append([sys.executable, '-m', 'inference_worker.reference_collector',
                   '--stop-file', str(stop_file)])
    return result


def supervise(command_list, stop_file, *, camera_count=0, restart_limit=0, restart_delay=0,
              reference_restart_limit=0, reference_restart_delay=0):
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
    environment = os.environ.copy()
    # Keep all decoder/clip temporary files within this supervisor's lifetime.
    environment.update(TEMP=str(stop_file.parent), TMP=str(stop_file.parent))
    try:
        for command in command_list:
            children.append(subprocess.Popen(command, env=environment))
        while not stop_file.exists():
            for index, child in enumerate(children):
                if child.poll() is None:
                    continue
                is_camera = index < camera_count
                limit = restart_limit if is_camera else reference_restart_limit
                delay = restart_delay if is_camera else reference_restart_delay
                role = 'CAMERA' if is_camera else 'REFERENCE'
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera-id', action='append', required=True)
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--buffer-mib', type=int, default=256)
    parser.add_argument('--camera-restart-limit', type=int, default=0,
                        help='Opt-in total restart attempts per camera; 0 keeps fail-fast')
    parser.add_argument('--camera-restart-delay', type=float,
                        help='Required positive seconds between attempts when recovery is enabled')
    parser.add_argument('--reference-restart-limit', type=int, default=0,
                        help='Opt-in total reference collector retries; 0 keeps fail-fast')
    parser.add_argument('--reference-restart-delay', type=float,
                        help='Positive backoff seconds for reference recovery')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--submit-events', action='store_true')
    modes.add_argument('--submit-review-candidates', action='store_true')
    args = parser.parse_args()
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
    with tempfile.TemporaryDirectory(prefix='ppe_supervisor_') as folder:
        stop = Path(folder)/'stop'
        supervise(commands(args.camera_id, args.model_path.resolve(), stop,
                            mode=mode, buffer_mib=args.buffer_mib), stop,
                  camera_count=len(args.camera_id), restart_limit=args.camera_restart_limit,
                  restart_delay=args.camera_restart_delay or 0,
                  reference_restart_limit=args.reference_restart_limit,
                  reference_restart_delay=args.reference_restart_delay or 0)


if __name__ == '__main__':
    main()
