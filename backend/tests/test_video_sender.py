from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login
from video_sender import live_sender


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

        self.enterContext(patch.object(live_sender.requests, 'post', side_effect=post))
        self.enterContext(patch.object(live_sender, 'get_video_duration_ms', return_value=1000))
        # End after one pass (or the first reconnect wait); no camera/FFmpeg process starts.
        self.enterContext(patch.object(live_sender, 'time', SimpleNamespace(sleep=Mock(side_effect=KeyboardInterrupt))))

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

    def test_network_failure_keeps_fresh_chunk_for_retry(self):
        with patch.object(live_sender, 'send_chunk', side_effect=requests.ConnectionError('test-only disconnect')):
            live_sender.watch_and_send()
        self.assertTrue(self.completed.exists())
        self.assertTrue(self.completed.with_suffix('.txt').exists())
        self.assertTrue(self.writing.exists())
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM video_chunk').fetchone()[0], 0)
