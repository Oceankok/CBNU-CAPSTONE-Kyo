"""Simulated network time only; no cameras, sockets or real sleeps."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
import requests

from video_sender.backpressure import retry_upload
from video_sender.context_sender import ContextClient, SessionRunner
from backend.tests.test_capture_context_sender import Client


class NetworkRetryTests(TestCase):
    def setUp(self):
        self.clock = 0

    def now(self): return self.clock
    def sleep(self, seconds): self.clock += seconds

    def retry(self, attempt, **kwargs):
        return retry_upload(attempt, monotonic=self.now, sleep=self.sleep, **kwargs)

    def response(self, code=202):
        result = Mock(status_code=code, headers={})
        result.json.return_value = {'received': True, 'sequence': 0}
        return result

    def test_delay_and_twenty_second_outage_recover_with_same_attempt(self):
        for outage in (1, 5, 20):
            with self.subTest(outage=outage):
                self.clock = 0
                calls = []
                def attempt(timeout):
                    calls.append(timeout)
                    if self.clock < outage: raise requests.ConnectionError('offline')
                    self.clock += 5  # Slow successful upload.
                    return self.response()
                self.assertEqual(self.retry(attempt).status_code, 202)
                self.assertLessEqual(len(calls), 8)
                self.assertLessEqual(self.clock, 60)

    def test_retryable_statuses_and_permanent_failures(self):
        for code in (408, 429, 502, 503, 504):
            attempt = Mock(side_effect=[self.response(code), self.response()])
            self.retry(attempt); self.assertEqual(attempt.call_count, 2)
        for code in (400, 401, 403, 409, 413, 422, 500):
            attempt = Mock(return_value=self.response(code))
            self.retry(attempt); attempt.assert_called_once()
        attempt = Mock(side_effect=requests.exceptions.SSLError('certificate'))
        with self.assertRaises(requests.exceptions.SSLError): self.retry(attempt)
        attempt.assert_called_once()

    def test_timeout_budget_and_guard_abort_are_bounded(self):
        calls = []
        def timeout(limit):
            calls.append(limit); self.clock += limit
            raise requests.Timeout('offline')
        with self.assertRaises(TimeoutError): self.retry(timeout)
        self.assertLessEqual(self.clock, 60)
        self.assertLessEqual(len(calls), 8)
        attempt = Mock(return_value=self.response(503))
        guard = Mock(side_effect=[None, RuntimeError('stop')])
        with self.assertRaisesRegex(RuntimeError, 'stop'): self.retry(attempt, guard=guard)
        attempt.assert_called_once()

    def test_retry_after_is_honored_or_fails_without_retrying_early(self):
        first = self.response(503); first.headers = {'Retry-After': '5'}
        attempt = Mock(side_effect=[first, self.response()])
        self.retry(attempt)
        self.assertAlmostEqual(self.clock, 5)
        for hint, failure in [('61', TimeoutError), ('invalid', ValueError)]:
            response = self.response(429); response.headers = {'Retry-After': hint}
            attempt = Mock(return_value=response)
            with self.assertRaises(failure): self.retry(attempt)
            attempt.assert_called_once()

    def test_control_requests_retry_identical_persisted_body(self):
        for endpoint, body in [('/api/video/capture-sessions', {'session_id': 'fixed'}),
                               ('/api/video/streams', {'stream_id': 'fixed'}),
                               ('/api/video/capture-sessions/context/stop',
                                {'capture_stopped': True, 'final_sequences': {'stream': 2}})]:
            with self.subTest(endpoint=endpoint):
                http = Mock(headers={}); seen = []
                def post(url, **kwargs):
                    seen.append((url, kwargs['json'].copy()))
                    if len(seen) == 1: raise requests.ConnectionError('response lost')
                    return self.response()
                http.post.side_effect = post
                client = ContextClient('https://test.invalid', 'secret', session=http)
                with patch('video_sender.context_sender.retry_upload', side_effect=self.retry):
                    client.post(endpoint, body)
                self.assertEqual(seen, [('https://test.invalid'+endpoint, body)]*2)

    def test_exhaustion_keeps_original_and_journal_then_explicit_drain_recovers(self):
        with TemporaryDirectory() as folder:
            runner = SessionRunner.create(Path(folder)/'session', Client(), 'CAM_001')
            runner.connect()
            clip = runner.root/'chunk_00000.mp4'; clip.write_bytes(b'original')
            clip.with_suffix('.txt').write_text('2026-11-16T00:00:00+00:00')
            clip.with_suffix('.ready').touch()
            before = runner.path.read_bytes()
            http = Mock(headers={})
            seen = []
            offline = True
            def post(url, **kwargs):
                seen.append((url, kwargs['params'], kwargs['headers'], kwargs['data'].read()))
                if offline: raise requests.ReadTimeout('lost acknowledgement')
                return self.response()
            http.post.side_effect = post
            runner.client = ContextClient(Client.origin, 'secret', session=http)
            with patch('video_sender.context_sender.retry_upload', side_effect=self.retry), \
                 patch('video_sender.context_sender.get_video_duration_ms', return_value=1000), \
                 patch('video_sender.context_sender.subprocess.Popen') as spawn:
                with self.assertRaises(requests.ReadTimeout): runner.drain()
                self.assertEqual(clip.read_bytes(), b'original')
                self.assertEqual(runner.path.read_bytes(), before)
                self.assertFalse(runner.state['closed'])
                self.assertEqual(runner.state['acknowledged_sequence'], -1)
                offline = False
                runner.drain()
                spawn.assert_not_called()
            self.assertFalse(clip.exists())
            self.assertEqual(runner.state['acknowledged_sequence'], 0)
            self.assertTrue(all(item == seen[0] for item in seen))

    def test_lost_ack_retry_reopens_body_before_acknowledged_cleanup(self):
        with TemporaryDirectory() as folder:
            clip = Path(folder)/'clip.mp4'; clip.write_bytes(b'original')
            http = Mock(headers={}); seen = []
            def post(url, **kwargs):
                seen.append(kwargs['data'].read())
                self.assertTrue(clip.exists())
                if len(seen) == 1: raise requests.ReadTimeout('committed but response lost')
                return self.response()
            http.post.side_effect = post
            client = ContextClient('https://test.invalid', 'secret', session=http)
            with patch('video_sender.context_sender.retry_upload', side_effect=self.retry), \
                 patch('video_sender.context_sender.get_video_duration_ms', return_value=1000):
                client.upload('same-stream', 0, clip, 'same-time')
            self.assertEqual(seen, [b'original', b'original'])
            self.assertTrue(clip.exists())  # Cleanup belongs to persisted-ACK runner.
