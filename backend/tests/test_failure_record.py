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

    def test_os_error_details_are_safe_and_first_record_is_immutable(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);run=str(uuid4());(root/'session.json').write_text(json.dumps({'run_id':run}))
            error=PermissionError(13,'secret path and token')
            error.winerror=32;error.sender_stage='atomic_json'
            record_failure(root,run,'sender',error)
            path=root/f'.failure-sender-{run}.json';first=path.read_bytes()
            data=json.loads(first)
            self.assertEqual((data['exception_type'],data['errno'],data['winerror'],data['stage']),
                             ('PermissionError',13,32,'atomic_json'))
            self.assertNotIn('secret',str(data))
            record_failure(root,run,'sender',OSError(28,'disk full'))
            self.assertEqual(path.read_bytes(),first)

    def test_atomic_json_retries_only_sharing_violations(self):
        from video_sender.context_sender import atomic_json
        import os
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'session.json';target.write_text('original')
            real_replace=os.replace;calls=[]
            def transient(source,dest):
                calls.append(source)
                if len(calls)<3:
                    error=PermissionError(13,'private');error.winerror=32;raise error
                real_replace(source,dest)
            with patch('video_sender.context_sender.os.replace',side_effect=transient), \
                 patch('video_sender.context_sender.time.sleep'):
                atomic_json(target,{'ok':True})
            self.assertEqual(json.loads(target.read_text()),{'ok':True})
            self.assertEqual(len(calls),3);self.assertFalse(list(Path(d).glob('*.tmp')))
            target.write_text('original')
            with patch('video_sender.context_sender.os.replace',side_effect=OSError(28,'private')) as replace, \
                 patch('video_sender.context_sender.time.sleep') as sleep:
                with self.assertRaises(OSError) as raised:atomic_json(target,{'ok':True})
            self.assertEqual(raised.exception.sender_stage,'atomic_json')
            self.assertEqual(replace.call_count,1);sleep.assert_not_called()
            self.assertEqual(target.read_text(),'original');self.assertFalse(list(Path(d).glob('*.tmp')))

    def test_cleanup_codes_and_network_types_are_recorded_safely(self):
        for error, name, code in (
                (OSError('acknowledged_clip_cleanup_pending'), 'OSError', 'acknowledged_clip_cleanup_pending'),
                (OSError('cleanup_pending'), 'OSError', 'cleanup_pending'),
                (requests.exceptions.ChunkedEncodingError('secret'), 'ChunkedEncodingError', None),
                (requests.exceptions.ContentDecodingError('secret'), 'ContentDecodingError', None),
                (requests.exceptions.RequestException('secret'), 'RequestException', None)):
            with self.subTest(name=name, code=code), tempfile.TemporaryDirectory() as d:
                root=Path(d);run=str(uuid4());(root/'session.json').write_text(json.dumps({'run_id':run}))
                record_failure(root,run,'sender',error)
                data=json.loads((root/f'.failure-sender-{run}.json').read_text())
                self.assertEqual((data['exception_type'],data['code']),(name,code))
                self.assertNotIn('secret',str(data))

    def test_failure_to_record_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            record_failure(Path(d)/'missing',str(uuid4()),'sender',ValueError('clock_wall_step'))

if __name__=='__main__':unittest.main()
