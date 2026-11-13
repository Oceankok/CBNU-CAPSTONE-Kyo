import unittest
import shutil
from unittest.mock import Mock, patch
from inference_worker.eos_delivery import drain_delivery
from inference_worker import test_continuous_runtime as fixture_module


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
class EOSDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.RuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def prepare_final_event(self):
        f = self.fixture
        for step in range(18):
            f.now = step / 2
            f.runtime.frame('ref', f.now, f.frame)
            f.runtime.frame('main', f.now, f.frame, assessments=[{'tracking_id':'t',
                'ppe':{'helmet':{'status':'suspected_no_helmet' if step < 12 else 'helmet'}}}])
        f.runtime.end_source('main'); f.runtime.end_source('ref')
        f.runtime.pump(now=f.now)
        return f

    def test_final_event_multiple_segments_drain_after_last_live_pump(self):
        f = self.prepare_final_event()
        self.assertGreater(sum(r['state'] != 'accepted' for r in f.dispatcher.status()), 0)
        result = drain_delivery(f.runtime)
        self.assertEqual(result['state'], 'drained')
        self.assertGreater(result['remaining']['accepted'], 1)
        self.assertFalse(f.capture.events)
        self.assertFalse(f.box.cleanup_obligations())

    def test_small_metadata_budget_still_finishes_last_event(self):
        self.fixture.runtime.budget = 1
        f = self.prepare_final_event()
        result = drain_delivery(f.runtime)
        self.assertEqual(result['state'], 'drained')
        self.assertGreater(result['remaining']['accepted'], 1)

    def test_expired_sources_never_become_successful_delivery(self):
        f = self.prepare_final_event()
        f.now = 1000
        result = drain_delivery(f.runtime)
        self.assertNotEqual(result['state'], 'drained')
        self.assertGreater(result['remaining']['uploads'], 0)
        self.assertGreater(result['remaining']['incomplete'], 0)

    def test_budget_leaves_durable_remaining_without_success(self):
        f = self.prepare_final_event()
        result = drain_delivery(f.runtime, max_steps=1)
        self.assertEqual(result['state'], 'budget_exhausted')
        self.assertGreater(result['remaining']['uploads'], 0)
        self.assertTrue(f.capture.events)

    def test_expired_wall_budget_never_pumps(self):
        f = self.prepare_final_event()
        with patch.object(f.runtime, 'pump') as pump:
            result = drain_delivery(f.runtime, clock=Mock(side_effect=[0,31]))
        self.assertEqual(result['state'], 'budget_exhausted'); pump.assert_not_called()

    def test_repeated_transport_refusal_stops_without_clearing_queue(self):
        f = self.prepare_final_event()
        with patch.object(f.dispatcher, 'upload_once', return_value=[('synthetic','blocked')]) as upload:
            result = drain_delivery(f.runtime)
        self.assertEqual(result['state'], 'blocked')
        self.assertLessEqual(upload.call_count, 2)
        self.assertGreater(result['remaining']['uploads'], 0)


class RunnerEOSBoundaryTests(unittest.TestCase):
    def execute_runner(self, reason, *, drained=True, verified=True):
        from contextlib import ExitStack
        from types import SimpleNamespace
        import inference_worker.run_continuous as runner
        cfg={'state_directory':'/tmp/no-io-mocked-runner','clocks':{c:{'offset':0,'error_bound':0,
            'valid_until':'2099-01-01T00:00:00Z'} for c in ('main','ref')},
            'metadata_steps_per_pump':1,'max_lag_seconds':5}
        for key in ('outbox','transport','delivery','capture','lifecycle','mux','decoder','source'): cfg[key]={}
        args=SimpleNamespace(submit_events=False,submit_review_candidates=True,replay_buffer=False,
            continuous_config='unused',camera_id='main',model_path=None,max_chunks=100,stop_file=None,poll=0)
        runtime=Mock(); capture=Mock(); capture.events={}
        mux=Mock(); mux.last_emitted=123.; mux.latest={'main':123.,'ref':123.}
        mux.cameras=('main','ref'); mux.ended={'main','ref'} if verified else {'main'}; mux.queue=[]
        mux.drained_sources.return_value=set(mux.ended)
        reader=Mock(); reader.poll.side_effect=[[],runner.SourceMuxError(reason)]
        reader.sequence={'main':4}; reader.end_reasons={'main':'source_ended','ref':'source_ended'}
        detector=Mock(); detector.supports_no_helmet=False
        with ExitStack() as st:
            st.enter_context(patch.object(runner,'read_config',return_value=cfg))
            st.enter_context(patch('inference_worker.run_server.create_detector',return_value=detector))
            st.enter_context(patch.dict(runner.os.environ,{'PPE_PROCESSING_NODE_TOKEN':'synthetic'}))
            st.enter_context(patch.object(runner.time,'sleep'))
            st.enter_context(patch('builtins.print'))
            for name in ('VideoClient','InferenceLoad','CaptureOutbox','PrivateClipTransport',
                         'ContinuousDispatcher','LifecycleJournal','PrivateChunkDecoder','CleanupSupervisor'):
                obj=Mock()
                if name=='VideoClient': obj.get_ppe_policy.return_value={}
                st.enter_context(patch.object(runner,name,return_value=obj))
            for name,obj in [('MultiCameraCapture',capture),('ContinuousFrameRuntime',runtime),
                             ('SourceMux',mux),('ContinuousSourceReader',reader)]:
                st.enter_context(patch.object(runner,name,return_value=obj))
            drain=st.enter_context(patch('inference_worker.eos_delivery.drain_delivery',
                return_value={'state':'drained' if drained else 'blocked'}))
            try:
                result=runner.run(args); error=None
            except runner.SourceMuxError as exc:
                result=None; error=str(exc)
        self.assertEqual(reader.poll.call_count,2)
        self.assertEqual(runtime.pump.call_count,1)
        return result,error,drain,runtime

    def test_verified_eos_runs_tail_without_new_reader_poll(self):
        for reason in ('session_stopped','primary_source_ended'):
            result,error,drain,runtime=self.execute_runner(reason)
            self.assertIsNone(error); self.assertEqual(result['state'],'drained')
            drain.assert_called_once_with(runtime); runtime.stop.assert_called_once_with(reason)

    def test_failed_delivery_is_not_success(self):
        result,error,drain,runtime=self.execute_runner('session_stopped',drained=False)
        self.assertIsNone(result); self.assertEqual(error,'eos_delivery_pending')
        drain.assert_called_once(); runtime.stop.assert_called_once_with('eos_delivery_pending')

    def test_timeout_or_unverified_eos_never_drains_as_normal_stop(self):
        for reason,verified in [('camera_missing_or_stalled',True),('session_stopped',False)]:
            result,error,drain,runtime=self.execute_runner(reason,verified=verified)
            self.assertIsNone(result); self.assertEqual(error,reason); drain.assert_not_called()
