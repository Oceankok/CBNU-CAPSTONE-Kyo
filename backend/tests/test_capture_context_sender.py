import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from video_sender.context_sender import SessionRunner, ContextClient, atomic_json
from video_sender.capture_control import publish_result, stop_requested


class Client:
    origin='https://test.invalid'
    def __init__(self):self.calls=[];self.uploads=[];self.fail=False;self.context=str(uuid4())
    def post(self,path,body):
        self.calls.append((path,body))
        if path.endswith('capture-sessions'):
            return dict(body,context_id=self.context)
        if path.endswith('/streams'):return {'stream_id':body['stream_id']}
        return {'retired':True}
    def upload(self,stream,n,path,stamp):
        if self.fail:raise OSError('offline')
        self.uploads.append((stream,n,path.read_bytes(),stamp))


class ContextSenderTests(TestCase):
    def setUp(self):
        self.tmp=self.enterContext(TemporaryDirectory());self.client=Client()
        self.runner=SessionRunner.create(Path(self.tmp)/'run',self.client,'CAM_001')
        self.runner.connect()
    def chunk(self,n=0):
        p=self.runner.root/f'chunk_{n:05}.mp4';p.write_bytes(b'mp4')
        p.with_suffix('.txt').write_text('2026-11-13T00:00:00+00:00')
        p.with_suffix('.ready').touch();return p
    def proof(self,frames=15,stopped=True,exit=0):
        publish_result(self.runner.root,self.runner.state['run_id'],stopped_by_request=stopped,
                       ffmpeg_exit=exit,start_sequence=0,frame_count=frames,fps=15)
    def test_identity_is_persisted_before_capture_and_retries(self):
        first=list(self.client.calls);self.runner.connect()
        self.assertEqual(first,self.client.calls[2:])
        self.assertFalse(self.runner.state['capture_started'])
    def test_healthy_stop_only_after_all_chunks_acknowledged_and_deleted(self):
        p=self.chunk();self.proof();self.runner.finish()
        self.assertFalse(p.exists());self.assertTrue(self.runner.state['closed'])
        self.assertEqual(self.client.calls[-1][1]['final_sequences'],{self.runner.state['stream_id']:0})
    def test_offline_never_discards_or_stops(self):
        p=self.chunk();self.proof();self.client.fail=True
        with self.assertRaises(OSError):self.runner.finish()
        self.assertTrue(p.exists());self.assertEqual(self.runner.state['acknowledged_sequence'],-1)
        self.assertFalse(any(p.endswith('/stop') for p,_ in self.client.calls))
    def test_retry_after_network_failure_preserves_context(self):
        self.chunk();self.proof();self.client.fail=True
        with self.assertRaises(OSError):self.runner.finish()
        self.client.fail=False;r=SessionRunner(self.runner.root,self.client);r.finish()
        self.assertTrue(r.state['closed']);self.assertEqual(r.state['context_id'],self.client.context)
    def test_no_proof_or_failed_capture_cannot_stop(self):
        with self.assertRaises(FileNotFoundError):self.runner.finish()
        self.proof(stopped=False)
        with self.assertRaises(ValueError):self.runner.finish()
        self.assertFalse(self.runner.state['closed'])
    def test_partial_and_sequence_gap_fail_closed(self):
        self.chunk(1)
        with self.assertRaisesRegex(ValueError,'gap'):self.runner.drain()
        self.assertEqual(self.client.uploads,[])
        (self.runner.root/'chunk_00000.mp4.part').write_bytes(b'partial')
        self.proof(frames=30)
        with self.assertRaises(ValueError):self.runner.finish()
    def test_ack_persisted_before_cleanup_failure(self):
        self.chunk();self.proof()
        with patch('video_sender.context_sender.delete_chunk_files',return_value=False):
            with self.assertRaises(OSError):self.runner.drain()
        self.assertEqual(SessionRunner(self.runner.root,self.client).state['acknowledged_sequence'],0)
        self.runner.finish();self.assertEqual(len(self.client.uploads),1)
    def test_final_sequence_missing_blocks_stop(self):
        self.chunk();self.proof(frames=30)
        with self.assertRaisesRegex(ValueError,'final_outbox'):self.runner.finish()
        self.assertFalse(self.runner.state['closed'])
    def test_stop_request_is_explicit_and_does_not_mark_complete(self):
        self.assertFalse(stop_requested(self.runner.root,self.runner.state['run_id']))
        self.runner.request_stop();self.assertTrue(stop_requested(self.runner.root,self.runner.state['run_id']))
        with self.assertRaises(FileNotFoundError):self.runner.finish()
    def test_journal_bound_to_origin(self):
        self.client.origin='https://other.invalid'
        with self.assertRaisesRegex(ValueError,'origin'):SessionRunner(self.runner.root,self.client)
    def test_existing_directory_never_reused(self):
        with self.assertRaises(FileExistsError):SessionRunner.create(self.runner.root,self.client,'CAM_001')
    def test_invalid_origins_and_no_secret_in_journal(self):
        for origin in ['http://example.com','https://x/?token=x','https://user:pass@x','https://x/path']:
            with self.assertRaises(ValueError):ContextClient(origin,'secret')
        self.assertNotIn('token',self.runner.path.read_text())
    def test_os_lock_rejects_concurrent_runner(self):
        from video_sender.context_sender import runner_lock
        with runner_lock(self.runner.root):
            with self.assertRaises(OSError):
                with runner_lock(self.runner.root):pass
        with runner_lock(self.runner.root):pass
    def test_corrupt_ack_state_cannot_delete_outbox(self):
        p=self.chunk();self.runner.state['acknowledged_sequence']=True;self.runner.save()
        with self.assertRaisesRegex(ValueError,'state_values'):SessionRunner(self.runner.root,self.client)
        self.assertTrue(p.exists())
    def test_started_journal_never_reopens_capture(self):
        self.runner.state['capture_started']=True;self.runner.save()
        with patch('video_sender.context_sender.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'explicit_recovery'):self.runner.run_capture()
            spawn.assert_not_called()
    def test_pre_capture_connection_timeout_can_retry_same_ids(self):
        # Simulate a server having accepted creation but response lost before capture.
        original=self.client.post
        seen=[]
        def lost(path,body):
            result=original(path,body);seen.append(dict(body))
            raise TimeoutError('response lost')
        with patch.object(self.client,'post',side_effect=lost):
            with self.assertRaises(TimeoutError):self.runner.connect()
        saved=json.loads(self.runner.path.read_text())
        self.assertFalse(saved['capture_started'])
        retry=SessionRunner(self.runner.root,self.client);retry.connect()
        self.assertEqual(seen[0]['session_id'],retry.state['session_id'])
        self.assertEqual(saved['stream_id'],retry.state['stream_id'])
        self.assertFalse(retry.state['capture_started'])
    def test_ctrl_c_requests_stop_waits_for_proof_then_acknowledges(self):
        from unittest.mock import Mock
        child=Mock();child.poll.side_effect=KeyboardInterrupt;child.returncode=0
        def wait(timeout):
            self.assertTrue(stop_requested(self.runner.root,self.runner.state['run_id']))
            self.proof(frames=0)
            return 0
        child.wait.side_effect=wait
        with patch('video_sender.context_sender.subprocess.Popen',return_value=child) as spawn:
            self.runner.run_capture()
        self.assertTrue(self.runner.state['closed'])
        self.assertNotIn('VIDEO_NODE_TOKEN',spawn.call_args.kwargs['env'])
    def test_hung_capture_never_acknowledges(self):
        from unittest.mock import Mock
        import subprocess
        child=Mock();child.poll.side_effect=KeyboardInterrupt
        child.wait.side_effect=subprocess.TimeoutExpired('capture',30)
        with patch('video_sender.context_sender.subprocess.Popen',return_value=child):
            with self.assertRaisesRegex(RuntimeError,'unconfirmed'):self.runner.run_capture()
        self.assertFalse(any(p.endswith('/stop') for p,_ in self.client.calls))
        child.terminate.assert_not_called()
    def test_abnormal_capture_exit_never_acknowledges(self):
        from unittest.mock import Mock
        child=Mock();child.poll.return_value=1;child.returncode=1;child.wait.return_value=1
        with patch('video_sender.context_sender.subprocess.Popen',return_value=child):
            with self.assertRaisesRegex(RuntimeError,'failed'):self.runner.run_capture()
        self.assertFalse(any(p.endswith('/stop') for p,_ in self.client.calls))
