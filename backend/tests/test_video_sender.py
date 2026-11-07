from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from email.utils import format_datetime

import requests

from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login
from video_sender import live_sender
from backend.video import service


class VideoSenderDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.root = Path(self.enterContext(TemporaryDirectory(prefix='kyo-sender-test-')))
        admin = login(self.client)
        token = self.client.post('/api/nodes', headers=admin, json={'node_id': 'sender-test', 'name': 'Test sender'}).json()['token']
        self.client.put('/api/cameras/CAM_001/node', headers=admin,
                        json={'source_node_id': 'sender-test', 'output_node_id': 'sender-test'}).raise_for_status()
        for name, value in {'OUTPUT_DIR': self.root/'out', 'SERVER_URL': 'http://testserver',
                            'CAMERA_ID': 'CAM_001', 'NODE_TOKEN': token}.items():
            self.enterContext(patch.object(live_sender, name, value))
        self.enterContext(patch('backend.video.service.ROOT', self.root/'buffer'))
        live_sender.OUTPUT_DIR.mkdir()
        self.completed = live_sender.OUTPUT_DIR/'chunk_00000.mp4'
        self.data = (Path(__file__).resolve().parents[1]/'db/demo_assets/sample.mp4').read_bytes()
        self.completed.write_bytes(self.data)
        self.completed.with_suffix('.txt').write_text(datetime.now(timezone.utc).isoformat(), encoding='utf-8')
        self.writing = live_sender.OUTPUT_DIR/'chunk_00001.mp4'
        self.writing.write_bytes(b'currently-being-written')
        self.uploads = []

        def post(url, *, headers=None, json=None, params=None, data=None, timeout=None):
            if data is not None:
                content = data.read()
                self.uploads.append(content)
                return self.client.post(url, headers=headers, params=params, content=content)
            return self.client.post(url, headers=headers, json=json)

        self.post_bridge = post
        self.enterContext(patch.object(live_sender.requests, 'post', side_effect=post))
        self.enterContext(patch.object(live_sender, 'get_video_duration_ms', return_value=1000))
        # End after one pass (or the first reconnect wait); no camera/FFmpeg process starts.
        self.enterContext(patch.object(live_sender.time, 'sleep', side_effect=KeyboardInterrupt))

    def test_fresh_chunk_reaches_real_receiver_before_local_cleanup(self):
        live_sender.watch_and_send()
        self.assertEqual(self.uploads, [self.data])
        self.assertFalse(self.completed.exists())
        self.assertFalse(self.completed.with_suffix('.txt').exists())
        self.assertTrue(self.writing.exists())
        with get_connection() as conn:
            row = conn.execute('SELECT sequence,byte_size FROM video_chunk').fetchone()
            self.assertEqual((row['sequence'], row['byte_size']), (0, len(self.data)))

    def test_stale_chunk_is_discarded_without_upload(self):
        self.completed.with_suffix('.txt').write_text((datetime.now(timezone.utc)-timedelta(seconds=46)).isoformat(), encoding='utf-8')
        live_sender.watch_and_send()
        self.assertEqual(self.uploads, [])
        self.assertFalse(self.completed.exists())
        self.assertFalse(self.completed.with_suffix('.txt').exists())
        self.assertTrue(self.writing.exists())

    def test_numeric_order_does_not_upload_current_tail_after_five_digits(self):
        self.completed.with_suffix('.txt').rename(live_sender.OUTPUT_DIR/'chunk_99999.txt')
        self.completed = self.completed.rename(live_sender.OUTPUT_DIR/'chunk_99999.mp4')
        self.writing = self.writing.rename(live_sender.OUTPUT_DIR/'chunk_100000.mp4')
        live_sender.watch_and_send()
        self.assertEqual(self.uploads, [self.data])
        self.assertFalse(self.completed.exists())
        self.assertTrue(self.writing.exists())
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT sequence FROM video_chunk').fetchone()[0], 99999)

    def test_network_failure_keeps_fresh_chunk_for_retry(self):
        with patch.object(live_sender, 'send_chunk', side_effect=requests.ConnectionError('test-only disconnect')):
            live_sender.watch_and_send()
        self.assertTrue(self.completed.exists())
        self.assertTrue(self.completed.with_suffix('.txt').exists())
        self.assertTrue(self.writing.exists())
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM video_chunk').fetchone()[0], 0)

    def limited_post(self, url, **kwargs):
        if kwargs.get('data') is None:
            return self.post_bridge(url, **kwargs)
        self.attempts.append((url, kwargs['params'].copy(), kwargs['headers']['X-Capture-Start']))
        if len(self.attempts) == 1:
            with patch.object(service, 'TOTAL_BYTES', 1):
                actual = self.post_bridge(url, **kwargs)
            self.assertEqual(actual.status_code, 429)
            response = requests.Response()
            response.status_code = actual.status_code
            response._content = actual.content
            response.headers['Retry-After'] = '2'
            response.url = url
            return response
        return self.post_bridge(url, **kwargs)

    def test_backpressure_retries_same_stream_and_payload_before_cleanup(self):
        self.attempts = []
        sleeps = []
        def sleep(delay):
            sleeps.append(delay)
            if len(sleeps) == 1:
                self.assertEqual(delay, 2)
                self.assertTrue(self.completed.exists())
                self.assertTrue(self.completed.with_suffix('.txt').exists())
                with get_connection() as conn:
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM video_chunk').fetchone()[0], 0)
            if not self.completed.exists():
                raise KeyboardInterrupt
        with patch.object(live_sender.requests, 'post', side_effect=self.limited_post), \
             patch.object(live_sender.time, 'sleep', side_effect=sleep):
            live_sender.watch_and_send()
        self.assertEqual(len(self.attempts), 2)
        self.assertEqual(self.attempts[0], self.attempts[1])
        self.assertEqual(self.uploads, [self.data, self.data])
        self.assertFalse(self.completed.exists())
        self.assertTrue(self.writing.exists())
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM video_stream').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM video_chunk').fetchone()[0], 1)

    def test_chunk_expiring_during_backpressure_is_not_reuploaded(self):
        self.attempts = []
        def sleep(delay):
            if delay == 2:
                self.completed.with_suffix('.txt').write_text(
                    (datetime.now(timezone.utc)-timedelta(seconds=46)).isoformat(), encoding='utf-8')
            if not self.completed.exists():
                raise KeyboardInterrupt
        with patch.object(live_sender.requests, 'post', side_effect=self.limited_post), \
             patch.object(live_sender.time, 'sleep', side_effect=sleep):
            live_sender.watch_and_send()
        self.assertEqual(len(self.attempts), 1)
        self.assertFalse(self.completed.exists())
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM video_stream').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM video_chunk').fetchone()[0], 0)

    def test_interrupting_backpressure_wait_keeps_unsent_chunk(self):
        self.attempts = []
        with patch.object(live_sender.requests, 'post', side_effect=self.limited_post):
            live_sender.watch_and_send()
        self.assertTrue(self.completed.exists())
        self.assertTrue(self.completed.with_suffix('.txt').exists())
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM video_stream').fetchone()[0], 1)


class BackpressureHeaderTests(unittest.TestCase):
    def response(self, value):
        response = requests.Response()
        response.status_code = 429
        response.headers['Retry-After'] = value
        return response

    def test_retry_after_seconds_and_malformed_values(self):
        self.assertEqual(live_sender.backpressure_delay(self.response('3')), 3)
        for value in ['', 'bad', '-1', '0', 'NaN', 'inf']:
            with self.subTest(value=value):
                self.assertEqual(live_sender.backpressure_delay(self.response(value)), 1)

    def test_retry_after_http_date(self):
        now = datetime(2026, 11, 6, tzinfo=timezone.utc)
        with patch.object(live_sender, 'datetime') as clock:
            clock.now.return_value = now
            response = self.response(format_datetime(now+timedelta(seconds=5), usegmt=True))
            self.assertEqual(live_sender.backpressure_delay(response), 5)

    def test_only_short_error_codes_are_logged(self):
        response = self.response('1')
        for data, expected in [(b'{"detail":"video_buffer_full"}', 'video_buffer_full'),
                               (b'<html>blocked</html>', 'rate_limited'),
                               (b'{"detail":"Bearer secret-like-value"}', 'rate_limited')]:
            response._content = data
            self.assertEqual(live_sender.backpressure_reason(response), expected)
