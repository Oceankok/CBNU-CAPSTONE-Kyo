import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from video_sender.capture_control import publish_result
from video_sender.context_starter import Starter

class ProtectiveReasonTests(unittest.TestCase):
    def test_proof_reason_does_not_override_incomplete(self):
        with tempfile.TemporaryDirectory() as d:
            publish_result(d,'run',stopped_by_request=True,ffmpeg_exit=1,start_sequence=0,frame_count=0,fps=30,protective_stop_reason='spool_free_space_reserve')
            data=json.loads((Path(d)/'.capture-result-run.json').read_text())
            self.assertFalse(data['capture_stopped'])
            self.assertEqual(data['protective_stop_reason'],'spool_free_space_reserve')
    def test_unknown_reason_not_copied(self):
        with tempfile.TemporaryDirectory() as d:
            publish_result(d,'run',stopped_by_request=True,ffmpeg_exit=0,start_sequence=0,frame_count=0,fps=30,protective_stop_reason='untrusted text')
            self.assertNotIn('protective_stop_reason',json.loads((Path(d)/'.capture-result-run.json').read_text()))
    def test_gui_ack_still_requires_closed_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as d:
            s=Starter(d);s.directory=Path(d);s.active=lambda:False
            state={'closed':False,'protective_stop_reason':'spool_free_space_reserve'}
            s.journal=lambda:state;s.process=SimpleNamespace(returncode=0)
            self.assertIn('ACK 미확인',s.status())
            state['closed']=True
            self.assertIn('ACK 확인',s.status())
            s.process.returncode=1
            self.assertIn('ACK 미확인',s.status())

    def test_gui_polls_child_before_protective_ack(self):
        with tempfile.TemporaryDirectory() as d:
            s=Starter(d);s.directory=Path(d)
            s.journal=lambda:{'closed':True,'protective_stop_reason':'spool_soft_stop_threshold'}
            class Child:
                returncode=None
                def poll(self):
                    self.returncode=0
                    return 0
            s.process=Child()
            self.assertIn('ACK 확인',s.status())
