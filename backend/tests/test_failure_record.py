import json,tempfile,unittest,sys
from unittest.mock import patch,Mock
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4
import requests
from video_sender.failure_record import record_failure

class FailureRecords(unittest.TestCase):
    def test_sanitized_separate_atomic_records_and_session_binding(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);run=str(uuid4());(root/'session.json').write_text(json.dumps({'run_id':run}))
            secret='secret-token https://server.invalid/private?key=secret-token'
            response=requests.Response();response.status_code=503
            record_failure(root,run,'sender',requests.HTTPError(secret,response=response))
            record_failure(root,run,'capture',ValueError('clock_wall_step'))
            sender=json.loads((root/f'.failure-sender-{run}.json').read_text())
            capture=json.loads((root/f'.failure-capture-{run}.json').read_text())
            self.assertEqual(sender['exception_type'],'HTTPError');self.assertEqual(sender['http_status'],503)
            self.assertIsNone(sender['code']);self.assertEqual(capture['code'],'clock_wall_step')
            self.assertNotIn(secret,json.dumps([sender,capture]));self.assertNotIn('server.invalid',json.dumps([sender,capture]))
            self.assertFalse(list(root.glob('*.tmp')))
            record_failure(root,str(uuid4()),'sender',ValueError(secret))
            self.assertEqual(len(list(root.glob('.failure-*'))),2)

    def test_sender_records_post_creation_failure_and_reraises(self):
        from video_sender import context_sender
        run=str(uuid4());runner=Mock();runner.root=Path('session');runner.state={'run_id':run}
        runner.run_capture.side_effect=ValueError('clock_wall_step')
        with patch.object(sys,'argv',['sender','--directory','session']), \
             patch.dict('os.environ',{'VIDEO_SERVER_URL':'https://example.invalid','VIDEO_NODE_TOKEN':'token','VIDEO_CAMERA_ID':'CAM'}), \
             patch('video_sender.clock_source.configuration'), patch.object(context_sender,'ContextClient'), \
             patch.object(context_sender.SessionRunner,'create',return_value=runner), \
             patch.object(context_sender,'runner_lock',return_value=nullcontext()), \
             patch('video_sender.failure_record.record_failure') as record:
            with self.assertRaisesRegex(ValueError,'clock_wall_step'):context_sender.main()
            self.assertEqual(record.call_args.args[:3],(runner.root,run,'sender'))

    def test_first_failure_survives_later_recovery_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);run=str(uuid4());(root/'session.json').write_text(json.dumps({'run_id':run}))
            record_failure(root,run,'sender',ValueError('clock_wall_step'))
            path=root/f'.failure-sender-{run}.json';first=path.read_bytes()
            record_failure(root,run,'sender',ValueError('source_clock_proof_expired'))
            self.assertEqual(path.read_bytes(),first)
            self.assertEqual(json.loads(first)['code'],'clock_wall_step')
            self.assertFalse(list(root.glob('*.tmp')))

    def test_failure_to_record_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            record_failure(Path(d)/'missing',str(uuid4()),'sender',ValueError('clock_wall_step'))

if __name__=='__main__':unittest.main()
