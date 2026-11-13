from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import os

from video_sender.backpressure import Policy, retry_delay, retry_upload
from video_sender.context_sender import ContextClient, SessionRunner
from backend.tests.test_capture_context_sender import Client


ENV = {'VIDEO_BACKPRESSURE_ENABLED':'true', 'VIDEO_SPOOL_THRESHOLD_BYTES':'10000',
       'VIDEO_SPOOL_RESERVE_BYTES':'1000', 'VIDEO_SPOOL_MIN_FREE_BYTES':'2000'}


class BackpressureTests(TestCase):
    def setUp(self):
        self.clock=0
    def now(self): return self.clock
    def sleep(self, seconds): self.clock+=seconds
    def response(self, code, delay='1'):
        return SimpleNamespace(status_code=code,headers={'Retry-After':delay})
    def run_retry(self, attempt, guard=lambda:None):
        return retry_upload(attempt, policy=Policy(10000,1000,2000), guard=guard,
                            monotonic=self.now,sleep=self.sleep)

    def test_default_off_ignores_budget(self):
        with patch.dict(os.environ, {'VIDEO_BACKPRESSURE_ENABLED':'false'}):
            self.assertIsNone(Policy.from_env())
        attempt=Mock(return_value=self.response(429))
        retry_upload(attempt,policy=None);attempt.assert_called_once_with(20)

    def test_enabled_missing_explicit_budget_fails(self):
        with patch.dict(os.environ, {'VIDEO_BACKPRESSURE_ENABLED':'true'}, clear=True):
            with self.assertRaises(ValueError): Policy.from_env()
        with patch.dict(os.environ, dict(ENV, VIDEO_SPOOL_RESERVE_BYTES='10000')):
            with self.assertRaises(ValueError): Policy.from_env()

    def test_retry_429_then_success_and_stop_checks(self):
        guard=Mock(); attempt=Mock(side_effect=[self.response(429,'2'),self.response(202)])
        self.assertEqual(self.run_retry(attempt,guard).status_code,202)
        self.assertEqual(attempt.call_count,2);self.assertGreaterEqual(guard.call_count,10)
        self.assertAlmostEqual(self.clock,2)

    def test_only_429_retried(self):
        for code in [401,403,409,422,500,202]:
            attempt=Mock(return_value=self.response(code));self.run_retry(attempt)
            attempt.assert_called_once()
        attempt=Mock(side_effect=OSError('offline'))
        with self.assertRaises(OSError):self.run_retry(attempt)
        attempt.assert_called_once()

    def test_five_attempt_limit_and_large_retry_after_fail(self):
        attempt=Mock(return_value=self.response(429))
        self.assertEqual(self.run_retry(attempt).status_code,429)
        self.assertEqual(attempt.call_count,5)
        attempt=Mock(return_value=self.response(429,'61'))
        with self.assertRaises(TimeoutError):self.run_retry(attempt)
        attempt.assert_called_once()

    def test_elapsed_request_time_counts_toward_budget(self):
        def attempt(timeout):self.clock+=20;return self.response(429,'21')
        with self.assertRaises(TimeoutError):self.run_retry(attempt)
        self.assertLessEqual(self.clock,61)

    def test_retry_after_http_date_or_invalid(self):
        now=datetime.now(timezone.utc).replace(microsecond=0)
        self.assertEqual(retry_delay(format_datetime(now+timedelta(seconds=10)),now),10)
        for bad in ['-1','1.2','garbage']:
            with self.assertRaises(ValueError):retry_delay(bad,now)

    def test_soft_threshold_counts_partial_and_preserves(self):
        with TemporaryDirectory() as root:
            p=Path(root)/'chunk_00000.mp4.part';p.write_bytes(b'x'*9000)
            self.assertEqual(Policy(10000,1000,2000).reason(root),'spool_soft_stop_threshold')
            self.assertEqual(p.stat().st_size,9000)

    def test_free_space_reserve_triggers(self):
        with TemporaryDirectory() as root, patch('video_sender.backpressure.shutil.disk_usage',return_value=SimpleNamespace(free=2999)):
            self.assertEqual(Policy(10000,1000,2000).reason(root),'spool_free_space_reserve')

    def test_spool_requests_stop_without_deleting_unacked_or_closing(self):
        with TemporaryDirectory() as root, patch.dict(os.environ,ENV):
            runner=SessionRunner.create(Path(root)/'session',Client(),'CAM_001')
            p=runner.root/'chunk_00000.mp4.part';p.write_bytes(b'x'*9000)
            self.assertEqual(runner.check_spool(),'spool_soft_stop_threshold')
            self.assertTrue((runner.root/f".stop-{runner.state['run_id']}").exists())
            self.assertTrue(p.exists());self.assertFalse(runner.state['closed'])
            with patch('video_sender.context_sender.subprocess.Popen') as spawn:
                with self.assertRaises(RuntimeError):runner.run_capture()
                spawn.assert_not_called()

    def test_upload_reopens_same_original_for_retry(self):
        with TemporaryDirectory() as root, patch.dict(os.environ,ENV):
            p=Path(root)/'part.mp4';p.write_bytes(b'original')
            http=Mock();http.headers={}; seen=[]
            def post(*args,**kwargs):
                seen.append(kwargs['data'].read())
                r=Mock();r.status_code=429 if len(seen)==1 else 202;r.headers={'Retry-After':'0'}
                r.json.return_value={'received':True,'sequence':0};return r
            http.post.side_effect=post
            client=ContextClient('https://test.invalid','secret',session=http)
            with patch('video_sender.context_sender.get_video_duration_ms',return_value=1000):
                client.upload('s',0,p,'timestamp')
            self.assertEqual(seen,[b'original',b'original']);self.assertEqual(p.read_bytes(),b'original')

    def test_slow_guard_never_sleeps_negative(self):
        def guard():self.clock+=2
        attempt=Mock(side_effect=[self.response(429,'1'),self.response(202)])
        def checked_sleep(seconds):
            self.assertGreaterEqual(seconds,0);self.sleep(seconds)
        result=retry_upload(attempt,policy=Policy(10000,1000,2000),guard=guard,
                            monotonic=self.now,sleep=checked_sleep)
        self.assertEqual(result.status_code,202)
