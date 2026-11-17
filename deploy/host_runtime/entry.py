"""Versioned replacement entry. Install/call explicitly; never patches personal files."""
import argparse
import os
from pathlib import Path
import tempfile
import threading

from deploy.host_runtime.settings import arguments, load


def watch_stop(path, pid, finished, internal):
    reported_error = False
    while not finished.wait(.2):
        stage = 'read'
        try:
            if path.read_text(encoding='utf-8-sig').strip() == str(pid):
                stage = 'deliver'
                # Only restore this run's allocated directory, never its ancestors.
                internal.parent.mkdir(exist_ok=True)
                internal.touch()
                return
        except (OSError, UnicodeError) as error:
            if stage == 'read' and isinstance(error, FileNotFoundError):
                continue  # No external stop request yet.
            if not reported_error:
                print(f'[STOP SIGNAL ERROR] stage={stage} type={type(error).__name__}; '
                      'cooperative stop not delivered; retrying; check runtime directory/access',
                      flush=True)
                reported_error = True


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
        bootstrap = 'continuous_bootstrap' in settings
        if bootstrap:
            from deploy.host_runtime.automatic_profile import validate_selection
            validate_selection(settings)
            command = None
        else:
            command = arguments(settings)
        if args.check_settings:
            if bootstrap:
                print('Automatic input template valid; live source proofs have not been checked.', flush=True)
                return 0
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
        base_settings = settings
        def preparation_stopped():
            if args.stop_file is None:
                return False
            try:
                return args.stop_file.read_text(encoding='utf-8-sig').strip() == str(os.getpid())
            except FileNotFoundError:
                return False
        while True:
            if bootstrap:
                stage = 'automatic_source_proof'
                from deploy.host_runtime.automatic_profile import prepare, PreparationStopped
                client = VideoClient()
                try:
                    try:
                        settings = prepare(base_settings, client, stopped=preparation_stopped)
                    except PreparationStopped:
                        print('Automatic input preparation stopped; inference was not started.', flush=True)
                        return 0
                finally:
                    client.close()
                command = arguments(settings)
            # Each real recording has its own cooperative signal and watcher.
            # Supervisor cleanup must never become the next run's user stop.
            finished = threading.Event()
            watcher = None
            try:
                with tempfile.TemporaryDirectory(prefix='ppe_host_entry_') as folder:
                    stop = Path(folder)/'stop'
                    if args.stop_file:
                        watcher = threading.Thread(target=watch_stop,
                            args=(args.stop_file, os.getpid(), finished, stop), daemon=True)
                        watcher.start()
                    stage = 'inference'
                    from inference_worker.run_multi import main as run
                    if bootstrap:
                        clean_end = run([*command, '--stop-file', str(stop)], return_after_clean_source=True)
                    else:
                        run([*command, '--stop-file', str(stop)])
                        return 0
            finally:
                finished.set()
                if watcher:
                    watcher.join(timeout=2)
            if clean_end is not True or preparation_stopped():
                return 0
            print('[AUTOMATIC INPUT] Source ended cleanly; waiting for a new source session.', flush=True)
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
