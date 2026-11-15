"""Check a chosen interpreter; --apply explicitly installs versioned host requirements."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from deploy.host_runtime.processes import ProcessTrees


SMOKE = r'''
import importlib.metadata as metadata, json
expected=json.loads(__import__('sys').argv[1])
failures=[]
versions={}
for package, version in expected.items():
    try:
        versions[package]=metadata.version(package)
        if versions[package]!=version: failures.append(package+':version')
    except metadata.PackageNotFoundError: failures.append(package+':missing')
try:
    import cv2, httpx, imageio_ffmpeg
    from inference_worker.person_tracker import PersonTracker
    from inference_worker.ppe_rules import assess_ppe
    PersonTracker()
    assess_ppe([], (64,64,3))
except Exception as error: failures.append(type(error).__name__)
print(json.dumps({'versions':versions,'failures':failures,'model_loaded':False,'network_requested':False}))
raise SystemExit(bool(failures))
'''


def run_owned(command, *, source, timeout, env=None):
    trees = ProcessTrees()
    try:
        child = trees.spawn(command, cwd=source, env=env)
        return child.wait(timeout=timeout)
    finally:
        trees.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', required=True, type=Path)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--apply', action='store_true', help='Install packages into exactly the chosen interpreter')
    parser.add_argument('--timeout', type=float, default=600)
    args = parser.parse_args(argv)
    source = args.source.resolve()
    if (not args.python.is_absolute() or not args.python.is_file() or
            source != Path(__file__).resolve().parents[2] or not 0 < args.timeout <= 3600):
        parser.error('Existing absolute interpreter, matching source and bounded positive timeout required')
    pins = {}
    for line in (source/'inference_worker/requirements-tracking.txt').read_text().splitlines():
        if '==' in line and not line.lstrip().startswith('#'):
            name, version = line.strip().split('==')
            pins[name] = version
    try:
        if args.apply:
            result = run_owned([str(args.python), '-m', 'pip', 'install', '-r',
                str(source/'deploy/host_runtime/requirements.txt')], source=source, timeout=args.timeout)
            if result:
                return result
        with tempfile.TemporaryDirectory(prefix='ppe_dependency_check_') as cache:
            env = os.environ.copy()
            for name in ('PPE_PROCESSING_NODE_TOKEN', 'PPE_SERVER_URL'):
                env.pop(name, None)
            env.update(YOLO_CONFIG_DIR=cache, YOLO_AUTOINSTALL='false', YOLO_OFFLINE='true')
            return run_owned([str(args.python), '-c', SMOKE, json.dumps(pins)],
                             source=source, env=env, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print('Dependency preparation/check deadline exceeded', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
