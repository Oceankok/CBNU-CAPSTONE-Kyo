from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4
import json

from video_sender.context_starter import Starter


class StarterTests(TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.child = Mock(); self.child.poll.return_value = None; self.child.returncode = None
        self.spawn = Mock(return_value=self.child)
        self.s = Starter(self.root, executable='python', spawn=self.spawn)

    def state(self, **changes):
        self.s.directory.mkdir(parents=True, exist_ok=True)
        data = dict(run_id=str(uuid4()), session_id=str(uuid4()), stream_id=str(uuid4()),
                    closed=False, capture_started=False)
        data.update(changes)
        (self.s.directory/'session.json').write_text(json.dumps(data))
        return data

    def test_new_argv_and_no_secret(self):
        with patch.dict('os.environ', {'VIDEO_NODE_TOKEN': 'private-token'}): self.s.start()
        args, kwargs = self.spawn.call_args
        self.assertNotIn('private-token', str(args))
        self.assertIn('video_sender.context_sender', args[0])
        self.assertEqual(kwargs['env']['VIDEO_NODE_TOKEN'], 'private-token')

    def test_active_double_start_and_close_status(self):
        self.s.start(); self.state()
        with self.assertRaises(ValueError): self.s.start()
        self.assertNotIn('ACK 확인', self.s.status())

    def test_early_stop_retries_marker_until_journal_exists(self):
        self.s.start(); self.s.request_stop()
        data = self.state(); self.s.status()
        self.assertTrue((self.s.directory/f".stop-{data['run_id']}").exists())
        self.assertNotIn('ACK 확인', self.s.status())

    def test_exit_zero_is_not_stop_ack(self):
        self.s.start(); self.state(); self.child.poll.return_value = 0; self.child.returncode = 0
        self.assertIn('종료 미확인', self.s.status())
        with self.assertRaises(ValueError): self.s.start()

    def test_ack_and_exit_required_before_new_session(self):
        self.s.start(); self.state(closed=True)
        self.assertNotIn('ACK 확인', self.s.status())
        self.child.poll.return_value = 0; self.child.returncode = 0
        self.assertIn('종료 ACK 확인', self.s.status())
        self.s.start(); self.assertEqual(self.spawn.call_count, 2)

    def test_resume_start_rejects_started_capture(self):
        self.s.directory = self.root/'resume'; self.state(capture_started=True)
        with self.assertRaises(ValueError): self.s.start(self.s.directory, mode='resume-start')
        self.spawn.assert_not_called()

    def test_resume_modes_argv_and_proof_gate(self):
        self.s.directory = self.root/'resume'; data = self.state()
        self.s.start(self.s.directory, mode='resume-start')
        self.assertIn('--resume-start', self.spawn.call_args.args[0])
        self.child.poll.return_value = 1
        with self.assertRaises(ValueError): self.s.start(self.s.directory, mode='resume-drain')
        (self.s.directory/f".capture-result-{data['run_id']}.json").write_text('{}')
        self.s.start(self.s.directory, mode='resume-drain')
        self.assertIn('--resume-drain', self.spawn.call_args.args[0])

    def test_corrupt_state_not_marked_stopped(self):
        self.s.start(); self.state(closed='true')
        with self.assertRaises(ValueError): self.s.status()

    def test_windows_legacy_shared_mutex_refuses_overlap(self):
        from video_sender.context_starter import launcher_lock
        kernel=Mock();kernel.CreateMutexW.return_value=99;kernel.GetLastError.return_value=183
        with self.assertRaises(ValueError):
            with launcher_lock(kernel): self.fail('Must not launch GUI')
        kernel.CloseHandle.assert_called_once_with(99)
        self.assertEqual(kernel.CreateMutexW.call_args.args[2], 'Local\\KyoCameraLauncherV1')

    def test_mutex_retained_until_gui_exits(self):
        from video_sender.context_starter import launcher_lock
        kernel=Mock();kernel.CreateMutexW.return_value=99;kernel.GetLastError.return_value=0
        with launcher_lock(kernel): kernel.CloseHandle.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(99)
