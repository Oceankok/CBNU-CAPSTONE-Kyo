from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from clock_contract import AnchoredClock,ClockPolicy,sample_interval
from video_sender.clock_source import ClockPublisher,capture_stamp

POLICY=ClockPolicy(30,1,100,.005,.25,.05)


class SourceClockTests(unittest.TestCase):
    def setUp(self):
        self.now=100.;self.wall=100.;self.offset=2.5
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.runner=SimpleNamespace(root=Path(temporary.name),state=dict(stream_id='stream',run_id='run',
            camera_id='camera',context_id='context',session_id='session'),save=lambda:None)
        self.runner.client=SimpleNamespace(post=self.post)
        self.publisher=ClockPublisher(self.runner,(POLICY,'server','node',5),wall=lambda:self.wall,
            mono=lambda:self.now,sleep=self.advance)

    def advance(self,seconds):self.now+=seconds;self.wall+=seconds

    def post(self,path,body):
        if path.endswith('clock-challenges'):
            self.challenge=deepcopy(body)
            server=self.now+self.offset
            self.advance(.02)
            return dict(version=1,proof_id='proof-'+str(body['sample_sequence']),server_time=server,
                        server_epoch='server',server_boot_id='boot',policy=POLICY.public())
        sent=self.challenge['source_sent_at'];received=body['source_received_at'];server=self.challenge['monotonic_sent']+self.offset
        low,high=sample_interval(server,sent,received,self.challenge['monotonic_sent'],body['monotonic_received'],POLICY)
        return dict(version=1,proof_id=path.rsplit('/',1)[1],sample_sequence=self.challenge['sample_sequence'],
            source_node_id='node',camera_id='camera',stream_id='stream',capture_context_id='context',capture_session_id='session',
            run_id='run',epoch=self.runner.state['clock']['epoch'],server_epoch='server',server_boot_id='boot',server_sample=server,
            source_sent_at=sent,source_received_at=received,monotonic_sent=self.challenge['monotonic_sent'],
            monotonic_received=body['monotonic_received'],offset_low=low,offset_high=high,
            valid_until=server+POLICY.ttl_seconds,policy=POLICY.public())

    def test_capture_uses_actual_monotonic_observations_and_preserves_gap(self):
        self.publisher.refresh(force=True)
        self.advance(.1);first,_=capture_stamp(self.publisher.path,wall=lambda:self.wall,mono=lambda:self.now)
        self.advance(1.17);second,_=capture_stamp(self.publisher.path,wall=lambda:self.wall,mono=lambda:self.now)
        self.assertAlmostEqual(second-first,1.17)
        self.assertNotEqual(second-first,1)
        self.wall+=1
        with self.assertRaisesRegex(ValueError,'step'):capture_stamp(self.publisher.path,wall=lambda:self.wall,mono=lambda:self.now)

    def test_delayed_submission_uses_first_frame_observation(self):
        self.publisher.refresh(force=True)
        self.advance(.1)
        observed=self.now
        self.advance(.3)
        stamp,_=capture_stamp(self.publisher.path,wall=lambda:self.wall,mono=lambda:self.now,
                              observed_monotonic=observed)
        self.assertAlmostEqual(stamp,observed)
        with self.assertRaisesRegex(ValueError,'observation_invalid'):
            capture_stamp(self.publisher.path,wall=lambda:self.wall,mono=lambda:self.now,
                          observed_monotonic=self.now+1)

    def test_start_and_future_end_waits_for_both_offset_signs(self):
        for offset in (2.5,-2.5):
            with self.subTest(offset=offset):
                self.setUp();self.offset=offset;self.publisher.refresh(force=True)
                valid_from=100+offset
                self.publisher.wait(valid_from=valid_from)
                raw=self.publisher.clock.now()
                self.assertGreaterEqual(raw,valid_from)
                self.assertGreaterEqual(raw+self.publisher.state['proof']['offset_low']-POLICY.drift(),valid_from)
                end=raw+1
                self.advance(1)
                self.publisher.wait(raw_end=end)
                self.assertGreaterEqual(self.publisher.clock.now()+offset,end)

    def test_renewal_keeps_anchor_and_epoch_and_proof_cannot_extend_arbitrarily(self):
        self.publisher.refresh(force=True);state=deepcopy(self.publisher.state)
        self.advance(6);self.publisher.refresh()
        self.assertEqual(self.publisher.state['anchor'],state['anchor'])
        self.assertEqual(self.publisher.state['epoch'],state['epoch'])
        self.assertEqual(self.publisher.state['sample_sequence'],2)
        original=self.runner.client.post
        def forged(path,body):
            result=original(path,body)
            if '/clock-proofs/' in path:result['valid_until']+=3600
            return result
        self.runner.client.post=forged;self.advance(6)
        with self.assertRaisesRegex(ValueError,'binding'):self.publisher.refresh()

    def test_clock_proof_expiry_and_monotonic_regression_fail_closed(self):
        self.publisher.refresh(force=True);self.advance(31)
        with self.assertRaisesRegex(ValueError,'expired'):self.publisher.check()
        self.setUp();self.publisher.refresh(force=True);self.now-=1;self.wall-=1
        with self.assertRaisesRegex(ValueError,'regressed'):self.publisher.check()

    def test_static_sender_does_not_enable_clock_without_flag(self):
        from video_sender.clock_source import configuration
        with patch.dict('os.environ',{'VIDEO_CLOCK_PROOF_ENABLED':'false'}):self.assertIsNone(configuration())

    def test_bad_explicit_cli_policy_exits_before_session_or_http(self):
        from video_sender.context_sender import main,PRE_SESSION_FAILURE_EXIT
        target=self.runner.root/'not-created'
        with patch.dict('os.environ',{},clear=True),patch('sys.argv',['sender','--directory',str(target),'--clock-proof']),\
             patch('video_sender.context_sender.ContextClient') as client:
            self.assertEqual(main(),PRE_SESSION_FAILURE_EXIT)
            client.assert_not_called();self.assertFalse(target.exists())
