"""Versioned replacement entry. Install/call explicitly; never patches personal files."""
import argparse
import os
from pathlib import Path
import tempfile
import threading

from deploy.host_runtime.settings import arguments, load


def watch_stop(path, pid, finished, internal):
    while not finished.wait(.2):
        try:
            if path.read_text(encoding='utf-8-sig').strip() == str(pid):
                internal.touch()
                return
        except OSError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', required=True, type=Path)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--stop-file', type=Path)
    parser.add_argument('--check-settings', action='store_true',
                        help='Validate settings/profile only; no model, HTTP, state lock or inference')
    args = parser.parse_args(argv)
    source = args.source.resolve()
    # A module launched from another checkout must not report the wrong version.
    if source != Path(__file__).resolve().parents[2]:
        parser.error('Source must match the checkout containing this entry module')
    os.chdir(source)
    finished = threading.Event()
    watcher = None
    stage = 'settings'
    try:
        settings = load(args.settings)
        command = arguments(settings)
        if args.check_settings:
            if settings.get('runner') == 'continuous':
                from inference_worker.run_multi import continuous_profiles
                continuous_profiles(settings.get('camera_ids', [settings.get('camera_id')]),
                    entries=[p+'='+f for p, f in settings.get('continuous_profiles', {}).items()],
                    path=settings.get('continuous_config'), primary=settings.get('primary_camera_id'))
            print('Inference settings valid; runner='+settings.get('runner', 'legacy'), flush=True)
            return 0
        from inference_worker.video_client import VideoClient
        from backend.runtime_version import local_version, compare
        stage = 'server_version'
        client = VideoClient()
        try:
            server = client.get_runtime_version()
            if compare(local_version(source), server)['status'] != 'matched':
                raise RuntimeError('server_and_host_version_mismatch')
            if server.get('processing_mode') != 'remote':
                raise RuntimeError('remote_processing_required')
        finally:
            client.close()
        with tempfile.TemporaryDirectory(prefix='ppe_host_entry_') as folder:
            stop = Path(folder)/'stop'
            if args.stop_file:
                watcher = threading.Thread(target=watch_stop,
                    args=(args.stop_file, os.getpid(), finished, stop), daemon=True)
                watcher.start()
            stage = 'inference'
            from inference_worker.run_multi import main as run
            run([*command, '--stop-file', str(stop)])
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        # No raw exceptions, URLs, paths, tokens or response bodies in logs.
        print(f'Inference failed at {stage}: {type(error).__name__}', flush=True)
        return 1
    finally:
        finished.set()
        if watcher:
            watcher.join(timeout=2)


if __name__ == '__main__':
    raise SystemExit(main())
