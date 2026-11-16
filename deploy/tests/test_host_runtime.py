"""Versioned host tooling tests: fake source/interpreter calls, no pip/GPU/services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from deploy.host_runtime import entry, prepare


class HostRuntimeTests(unittest.TestCase):
    def test_prepare_defaults_to_check_and_apply_targets_only_selected_interpreter(self):
        with patch.object(prepare, 'run_owned', return_value=0) as run:
            self.assertEqual(prepare.main(['--python', sys.executable]), 0)
            self.assertEqual(len(run.call_args_list), 1)
            self.assertEqual(run.call_args.args[0][0], sys.executable)
            self.assertEqual(run.call_args.args[0][1], '-c')
            self.assertEqual(run.call_args.kwargs['env']['YOLO_AUTOINSTALL'], 'false')
            self.assertNotIn('PPE_PROCESSING_NODE_TOKEN', run.call_args.kwargs['env'])
        with patch.object(prepare, 'run_owned', side_effect=[0, 0]) as run:
            self.assertEqual(prepare.main(['--python', sys.executable, '--apply']), 0)
            self.assertEqual(run.call_args_list[0].args[0][:4], [sys.executable, '-m', 'pip', 'install'])
        with patch.object(prepare, 'run_owned', return_value=7) as run:
            self.assertEqual(prepare.main(['--python', sys.executable, '--apply']), 7)
            self.assertEqual(run.call_count, 1)

    def test_entry_check_settings_never_contacts_server_or_runs_inference(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            model = root/'fixture.pt'; model.touch()
            settings = root/'settings.json'
            settings.write_text(json.dumps({'camera_ids': ['A'], 'model_path': str(model), 'mode': 'review'}))
            with patch('inference_worker.run_multi.main') as run, \
                 patch('inference_worker.video_client.VideoClient') as client:
                self.assertEqual(entry.main(['--settings', str(settings), '--source',
                    str(Path(entry.__file__).resolve().parents[2]), '--check-settings']), 0)
            run.assert_not_called(); client.assert_not_called()

    def test_pid_stop_watcher_ignores_stale_signal_and_honors_own_pid(self):
        import threading
        with TemporaryDirectory() as folder:
            stop, internal = Path(folder)/'signal', Path(folder)/'internal'
            stop.write_text('999')
            finished = threading.Event()
            thread = threading.Thread(target=entry.watch_stop, args=(stop, 123, finished, internal))
            thread.start()
            try:
                self.assertFalse(internal.exists())
                stop.write_text('123')
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
                self.assertTrue(internal.exists())
            finally:
                finished.set(); thread.join(timeout=2)

    def test_stop_watcher_restores_deleted_private_directory_only_for_own_signal(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            private = root/'private'; private.mkdir()
            internal = private/'stop'
            signal = root/'signal'; signal.write_text('999')
            polls = 0
            def wait(_):
                nonlocal polls
                polls += 1
                if polls == 1:
                    private.rmdir()  # Removed after the watcher has started polling.
                elif polls == 2:
                    self.assertFalse(private.exists())  # Stale PID must not recreate it.
                    signal.write_text('123')
                return polls > 2
            entry.watch_stop(signal, 123, Mock(wait=wait), internal)
            self.assertTrue(internal.is_file())

    def test_stop_watcher_reports_delivery_error_once_and_retries(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            signal = root/'signal'; signal.write_text('123')
            internal = root/'private'/'stop'
            finished = Mock(); finished.wait.side_effect = [False, False, False, True]
            with patch.object(Path, 'touch', side_effect=[PermissionError('private detail'),
                    PermissionError('private detail'), None]) as touch, patch('builtins.print') as output:
                entry.watch_stop(signal, 123, finished, internal)
            self.assertEqual(touch.call_count, 3)
            self.assertEqual(output.call_count, 1)
            message = output.call_args.args[0]
            self.assertIn('stage=deliver', message)
            self.assertIn('PermissionError', message)
            self.assertNotIn('private detail', message)
            self.assertNotIn(str(root), message)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), 'Windows PowerShell 7 wrapper test')
    def test_background_wrapper_preserves_real_child_exit_and_literal_paths(self):
        wrapper = Path(entry.__file__).parent/'Start-Inference.ps1'
        with TemporaryDirectory(prefix='kyo runtime spaces ') as folder:
            root = Path(folder)
            source = root/'source & literal'
            module = source/'deploy/host_runtime'; module.mkdir(parents=True)
            (module/'entry.py').write_text('import sys\nprint("fixture stdout")\nprint("fixture stderr",file=sys.stderr)\nraise SystemExit(17)\n')
            settings = root/'settings & literal.json'; settings.write_text('{}')
            runtime = root/'runtime'
            result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-File', str(wrapper),
                '-Python', sys.executable, '-SourceRoot', str(source), '-SettingsPath', str(settings),
                '-RuntimeDirectory', str(runtime)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 17, result.stdout+result.stderr)
            state = json.loads((runtime/'inference-state.json').read_text(encoding='utf-8-sig'))
            self.assertGreater(state['process_id'], 0)
            self.assertIn('fixture stdout', (runtime/'inference.stdout.log').read_text())
            self.assertIn('fixture stderr', (runtime/'inference.stderr.log').read_text())


if __name__ == '__main__':
    unittest.main()
