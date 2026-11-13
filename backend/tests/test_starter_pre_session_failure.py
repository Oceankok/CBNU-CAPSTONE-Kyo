"""Recover only a positively identified new-session preparation failure."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
from video_sender.context_starter import Starter
from video_sender import context_sender

class PreSessionFailureTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(TemporaryDirectory()))
        self.child=Mock();self.child.poll.return_value=None
        self.spawn=Mock(return_value=self.child)
        self.starter=Starter(self.root,spawn=self.spawn)

    def test_explicit_preparation_failure_can_retry_without_ack_claim(self):
        self.starter.start();old=self.starter.directory
        old.mkdir();(old/'session.tmp').write_text('preserve interrupted local preparation')
        self.child.poll.return_value=context_sender.PRE_SESSION_FAILURE_EXIT
        self.assertIn('설정을 확인하고 새로 시작',self.starter.status())
        self.assertNotIn('ACK 확인',self.starter.status())
        self.starter.start()
        self.assertNotEqual(old,self.starter.directory)
        self.assertEqual(self.spawn.call_count,2)
        self.assertTrue((old/'session.tmp').exists())

    def test_missing_journal_after_arbitrary_exit_cannot_retry(self):
        for code in (0,1,-9):
            with self.subTest(code=code):
                self.starter=Starter(self.root,spawn=self.spawn)
                self.child.poll.return_value=None;self.starter.start()
                self.child.poll.return_value=code
                with self.assertRaises(FileNotFoundError):self.starter.start()

    def test_present_or_corrupt_journal_never_bypassed(self):
        self.starter.start();self.starter.directory.mkdir()
        self.child.poll.return_value=78
        path=self.starter.directory/'session.json'
        path.write_text(json.dumps(dict(run_id=str(uuid4()),session_id=str(uuid4()),stream_id=str(uuid4()),closed=False,capture_started=False)))
        with self.assertRaises(ValueError):self.starter.start()
        path.write_text('broken')
        with self.assertRaises(ValueError):self.starter.start()

    def test_resume_failure_is_not_new_session_preparation_failure(self):
        self.starter.directory=self.root/'old';self.starter.mode='resume-start'
        self.starter.process=self.child;self.child.poll.return_value=78
        with self.assertRaises(FileNotFoundError):self.starter.start()

    def test_main_missing_config_signals_preparation_failure_before_runner(self):
        with patch('sys.argv',['sender','--directory',str(self.root/'new')]),patch.dict(os.environ,{},clear=True),patch.object(context_sender.SessionRunner,'create') as create:
            self.assertEqual(context_sender.main(),78)
            create.assert_not_called()

    def test_main_create_failure_is_pre_session_but_capture_failure_is_not(self):
        env={'VIDEO_SERVER_URL':'http://localhost','VIDEO_NODE_TOKEN':'synthetic','VIDEO_CAMERA_ID':'CAM_001'}
        with patch('sys.argv',['sender','--directory',str(self.root/'new')]),patch.dict(os.environ,env,clear=True),patch.object(context_sender.SessionRunner,'create',side_effect=OSError('synthetic')):
            self.assertEqual(context_sender.main(),78)
        runner=Mock();runner.root=self.root
        runner.run_capture.side_effect=RuntimeError('capture phase')
        with patch('sys.argv',['sender','--directory',str(self.root/'new')]),patch.dict(os.environ,env,clear=True),patch.object(context_sender.SessionRunner,'create',return_value=runner),patch.object(context_sender,'runner_lock'):
            with self.assertRaisesRegex(RuntimeError,'capture phase'):context_sender.main()
