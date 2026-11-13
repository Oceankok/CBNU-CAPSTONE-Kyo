"""A retention deadline is enforced at GET/HEAD, without waiting for cleanup."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login


class MediaAccessExpiryTests(TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        login(self.client)
        self.root = Path(self.enterContext(TemporaryDirectory()))
        (self.root / 'clip.mp4').write_bytes(b'0123456789')
        self.enterContext(patch('backend.api.main._STORAGE_DIR', self.root))
        clock = self.enterContext(patch('backend.api.main.datetime', wraps=datetime))
        self.deadline = datetime(2030, 1, 1, tzinfo=timezone.utc)
        clock.now.return_value = self.deadline
        with get_connection() as conn:
            conn.execute("INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,created_at) VALUES('EXPIRY','EVT_0001','CAM_001','video','clip',?,'ready','complete',CURRENT_TIMESTAMP)", (f'{self.root.name}/clip.mp4',))

    def retention(self, decision, until):
        with get_connection() as conn:
            conn.execute("INSERT INTO event_retention(event_id,decision,retain_until) VALUES('EVT_0001',?,?) ON CONFLICT(event_id) DO UPDATE SET decision=excluded.decision,retain_until=excluded.retain_until", (decision, until))

    def test_expired_and_exact_deadline_block_get_head_and_range_without_deletion(self):
        for until in (self.deadline.timestamp()-1, self.deadline.timestamp()):
            self.retention('retain', until)
            for method, headers in [('GET', {}), ('HEAD', {}), ('GET', {'Range': 'bytes=0-3'})]:
                with self.subTest(until=until, method=method, headers=headers):
                    response = self.client.request(method, '/storage/clip.mp4', headers=headers)
                    self.assertEqual(response.status_code, 404)
                    self.assertNotEqual(response.content, b'0123456789')
        self.assertTrue((self.root / 'clip.mp4').exists())
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT decision FROM event_retention WHERE event_id='EVT_0001'").fetchone()[0], 'retain')

    def test_valid_extension_legacy_unbounded_and_pending_remain_available(self):
        self.retention('retain', self.deadline.timestamp()-1)
        self.assertEqual(self.client.get('/storage/clip.mp4').status_code, 404)
        for decision, until in [('retain', self.deadline.timestamp()+1), ('retain', None), ('pending', None)]:
            self.retention(decision, until)
            self.assertEqual(self.client.get('/storage/clip.mp4').status_code, 200)
            self.assertEqual(self.client.head('/storage/clip.mp4').status_code, 200)
            response = self.client.get('/storage/clip.mp4', headers={'Range': 'bytes=0-3'})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.content, b'0123')
        with get_connection() as conn:
            conn.execute("DELETE FROM event_retention WHERE event_id='EVT_0001'")
        self.assertEqual(self.client.get('/storage/clip.mp4').status_code, 200)

    def test_delete_remains_blocked_even_with_future_deadline(self):
        self.retention('delete', self.deadline.timestamp()+1)
        self.assertEqual(self.client.get('/storage/clip.mp4').status_code, 404)
