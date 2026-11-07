"""Manually supervise camera inference and one group-reference collector.

Credentials use VideoClient's existing environment; never pass them as arguments.
Each camera has its own model, buffer, tracking state and inference lease.
"""
import argparse
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


def supervise(command_list, stop_file):
    children = []
    environment = os.environ.copy()
    # Keep all decoder/clip temporary files within this supervisor's lifetime.
    environment.update(TEMP=str(stop_file.parent), TMP=str(stop_file.parent))
    try:
        for command in command_list:
            children.append(subprocess.Popen(command, env=environment))
        while not stop_file.exists():
            if any(child.poll() is not None for child in children):
                raise RuntimeError('A camera or reference collector exited; all peers are stopping')
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
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--submit-events', action='store_true')
    modes.add_argument('--submit-review-candidates', action='store_true')
    args = parser.parse_args()
    if (any(not camera.strip() for camera in args.camera_id) or
            len(set(args.camera_id)) != len(args.camera_id)):
        parser.error('Camera IDs must be nonempty and unique')
    if not args.model_path.is_file() or args.buffer_mib < 8:
        parser.error('An existing local model and buffer budget of at least 8 MiB are required')
    mode = ('--submit-events' if args.submit_events else
            '--submit-review-candidates' if args.submit_review_candidates else None)
    with tempfile.TemporaryDirectory(prefix='ppe_supervisor_') as folder:
        stop = Path(folder)/'stop'
        supervise(commands(args.camera_id, args.model_path.resolve(), stop,
                            mode=mode, buffer_mib=args.buffer_mib), stop)


if __name__ == '__main__':
    main()
