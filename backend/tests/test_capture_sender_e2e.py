"""Synthetic sender-to-real-ASGI integration; no device, live DB or model calls.

Transport adapter only translates requests.Session arguments to TestClient. API
validation, authorization, persistence and media ingestion are the real paths.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx

from backend.tests import test_video
from backend.db.event_repository import get_connection
from video_sender.context_sender import ContextClient, SessionRunner
from video_sender.capture_control import publish_result


class ASGITransport:
    def __init__(self, client):
        self.client = client
        self.headers = {}
        self.lose_once = None
        self.calls = []

    def post(self, url, **kwargs):
        path = urlsplit(url).path
        headers = dict(self.headers)
        headers.update(kwargs.pop('headers', {}))
        kwargs.pop('timeout', None)
        kwargs.pop('allow_redirects', None)
        if 'data' in kwargs:
            kwargs['content'] = kwargs.pop('data').read()
        result = self.client.post(path, headers=headers, follow_redirects=False, **kwargs)
        self.calls.append((path, result.status_code, result.json()))
        if self.lose_once and path.endswith(self.lose_once) and result.is_success:
            self.lose_once = None
            raise TimeoutError('synthetic response lost AFTER server committed')
        return result


class CaptureSenderE2ETests(TestCase):
    def setUp(self):
        test_video.VideoIngestTests.setUp(self)
        self.enterContext(patch.dict('os.environ', {'PPE_CAPTURE_CONTEXT_ENABLED': 'true'}))
        self.tmp = Path(self.enterContext(TemporaryDirectory()))
        self.transport = ASGITransport(self.client)
        self.protocol = ContextClient('http://localhost', self.nodes['source']['Authorization'][7:],
                                      session=self.transport)
        self.runner = SessionRunner.create(self.tmp / 'first', self.protocol, 'CAM_001')
        self.runner.connect()
        # Synthetic capture clock predates completed fixture media. This only
        # changes this isolated fixture, never a live registry.
        self.stamp = datetime.now(timezone.utc) - timedelta(seconds=20)
        with get_connection() as conn:
            self.destination = conn.execute('SELECT zone_name FROM zone_rule WHERE zone_name != (SELECT zone_name FROM capture_context_registry WHERE camera_id=?) LIMIT 1', ('CAM_001',)).fetchone()[0]
        with get_connection() as conn:
            conn.execute('UPDATE capture_context_registry SET valid_from=? WHERE context_id=?',
                         ((self.stamp-timedelta(seconds=1)).isoformat(), self.runner.state['context_id']))
        # Fixture's declaration is one second. MP4 parsing/ingestion stays real.
        self.enterContext(patch('video_sender.context_sender.get_video_duration_ms', return_value=1000))

    def chunk(self, sequence=0):
        p = self.runner.root / f'chunk_{sequence:05}.mp4'
        p.write_bytes(self.data)
        p.with_suffix('.txt').write_text((self.stamp+timedelta(seconds=sequence)).isoformat())
        p.with_suffix('.ready').touch()
        return p

    def proof(self, count=1):
        publish_result(self.runner.root, self.runner.state['run_id'], stopped_by_request=True,
                       ffmpeg_exit=0, start_sequence=0, frame_count=count*15, fps=15)

    def move(self):
        return self.client.patch('/api/cameras/CAM_001', headers=self.admin, json={'zone_name': self.destination})

    def test_context_stream_drain_stop_move_restart(self):
        self.assertEqual(self.move().status_code, 409)
        old = dict(self.runner.state)
        p = self.chunk(); self.proof(); self.runner.finish()
        self.assertFalse(p.exists()); self.assertTrue(self.runner.state['closed'])
        items = self.client.get(f"/api/processing/video/streams/{old['stream_id']}/chunks",
                                headers=self.nodes['processor']).json()['items']
        self.assertEqual(items[0]['capture_context_id'], old['context_id'])
        self.assertEqual(self.move().status_code, 200)
        fresh = SessionRunner.create(self.tmp / 'second', self.protocol, 'CAM_001')
        fresh.connect()
        self.assertNotEqual(fresh.state['context_id'], old['context_id'])
        with get_connection() as conn:
            previous = conn.execute('SELECT * FROM capture_context_registry WHERE context_id=?',
                                    (old['context_id'],)).fetchone()
            current = conn.execute('SELECT * FROM capture_context_registry WHERE context_id=?',
                                   (fresh.state['context_id'],)).fetchone()
            self.assertIsNotNone(previous['valid_until'])
            self.assertEqual(current['zone_name'], self.destination)
            self.assertGreater(current['assignment_version'], previous['assignment_version'])
        self.assertEqual(self.client.patch('/api/cameras/CAM_001', headers=self.admin,
                                           json={'zone_name': previous['zone_name']}).status_code, 409)

    def test_lost_upload_response_retry_keeps_original_until_real_ack(self):
        p = self.chunk(); self.proof(); original = p.read_bytes()
        self.transport.lose_once = '/chunks'
        with self.assertRaises(TimeoutError): self.runner.finish()
        self.assertEqual(p.read_bytes(), original)
        self.assertEqual(self.runner.state['acknowledged_sequence'], -1)
        self.assertEqual(self.move().status_code, 409)
        resumed = SessionRunner(self.runner.root, self.protocol); resumed.finish()
        self.assertTrue(resumed.state['closed']); self.assertFalse(p.exists())
        chunks = [body for path, status, body in self.transport.calls if path.endswith('/chunks')]
        self.assertEqual(len(chunks), 2); self.assertTrue(chunks[-1]['duplicate'])

    def test_lost_stop_response_retries_persisted_retirement(self):
        self.chunk(); self.proof(); self.transport.lose_once = '/stop'
        with self.assertRaises(TimeoutError): self.runner.finish()
        self.assertFalse(self.runner.state['closed'])
        responses = [body for path, _, body in self.transport.calls if path.endswith('/stop')]
        resumed = SessionRunner(self.runner.root, self.protocol); resumed.finish()
        last = self.transport.calls[-1][2]
        self.assertTrue(resumed.state['closed'])
        self.assertEqual(last['valid_until'], responses[0]['valid_until'])

    def test_lost_create_and_stream_response_retry_same_ids(self):
        self.proof(0); self.runner.finish()
        for index, endpoint in enumerate(('/capture-sessions', '/streams')):
            r = SessionRunner.create(self.tmp / f'retry-{index}', self.protocol, 'CAM_001')
            ids = (r.state['session_id'], r.state['stream_id'])
            self.transport.lose_once = endpoint
            with self.assertRaises(TimeoutError): r.connect()
            r = SessionRunner(r.root, self.protocol); r.connect()
            self.assertEqual(ids, (r.state['session_id'], r.state['stream_id']))
            self.assertFalse(r.state['capture_started'])
            publish_result(r.root, r.state['run_id'], stopped_by_request=True,
                           ffmpeg_exit=0, start_sequence=0, frame_count=0, fps=15)
            r.finish()

    def test_partial_outbox_never_stops_or_allows_move(self):
        p = self.chunk(); partial = self.runner.root / 'chunk_00001.mp4.part'
        partial.write_bytes(b'unfinished'); self.proof(2)
        with self.assertRaises(ValueError): self.runner.finish()
        self.assertEqual(partial.read_bytes(), b'unfinished')
        self.assertFalse(self.runner.state['closed'])
        self.assertEqual(self.move().status_code, 409)
        self.assertFalse(any(path.endswith('/stop') for path, _, _ in self.transport.calls))

    def test_server_sequence_gap_409_preserves_original(self):
        p = self.chunk(1); original = p.read_bytes()
        with self.assertRaises(httpx.HTTPStatusError) as failure:
            self.protocol.upload(self.runner.state['stream_id'], 1, p,
                                 p.with_suffix('.txt').read_text())
        self.assertEqual(failure.exception.response.status_code, 409)
        self.assertEqual(p.read_bytes(), original)
        self.assertEqual(self.move().status_code, 409)
        with get_connection() as conn:
            row = conn.execute('SELECT last_sequence FROM video_stream WHERE stream_id=?',
                               (self.runner.state['stream_id'],)).fetchone()
            self.assertEqual(row['last_sequence'], -1)

    def test_lost_ack_retry_after_raw_cleanup_uses_durable_receipt(self):
        from backend.video import service
        p = self.chunk(); self.proof(); original = p.read_bytes()
        self.transport.lose_once = '/chunks'
        with self.assertRaises(TimeoutError): self.runner.finish()
        with get_connection() as conn:
            conn.execute('UPDATE video_chunk SET expires_at=0 WHERE stream_id=?',
                         (self.runner.state['stream_id'],))
        service.cleanup()
        self.assertEqual(p.read_bytes(), original)
        resumed = SessionRunner(self.runner.root, self.protocol); resumed.finish()
        self.assertTrue(resumed.state['closed']); self.assertFalse(p.exists())
        bodies = [body for path, _, body in self.transport.calls if path.endswith('/chunks')]
        self.assertTrue(bodies[-1]['duplicate'])
