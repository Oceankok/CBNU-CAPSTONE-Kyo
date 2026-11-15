"""Read-only media projection regression. Synthetic evidence, isolated PostgreSQL."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.tests import test_continuous_events as baseline
from backend.db.event_repository import get_connection


@unittest.skipUnless(os.environ.get('PPE_TEST_POSTGRES_URL'), 'Dedicated test PostgreSQL required')
class TerminalEvidenceProjectionTests(unittest.TestCase):
    setUp = baseline.ContinuousEventTests.setUp
    ts = baseline.ContinuousEventTests.ts
    start = baseline.ContinuousEventTests.start
    progress = baseline.ContinuousEventTests.progress
    segment = baseline.ContinuousEventTests.segment
    capture_report = baseline.ContinuousEventTests.capture_report

    def snapshot(self):
        with get_connection() as conn:
            return {table: [dict(row) for row in conn.execute(f'SELECT * FROM {table} WHERE event_id=?', (self.ident,))]
                    for table in ('event_clip_request', 'event_media', 'processing_camera_capture',
                                  'processing_event_lifecycle', 'event_review')}

    def test_authoritative_missing_projection_preserves_pending_received_and_review_gates(self):
        self.start().raise_for_status()
        first = self.segment(0, -2, 1).json()['segments']
        self.segment(1, 1, 5).raise_for_status()
        self.progress(kind='close', end=3, close_reason='reference_unavailable',
                      complete=False, final_segment_count=2).raise_for_status()
        self.capture_report('CAM_001', state='incomplete', reason='event_capture_incomplete').raise_for_status()
        self.capture_report('CAM_002', state='incomplete', reason='source_failed',
            captured_ranges=[{'start_at': self.ts(-2), 'end_at': self.ts(1)}],
            missing_ranges=[{'start_at': self.ts(1), 'end_at': self.ts(5), 'reason': 'source_failed'}]).raise_for_status()
        received = next(row for row in first if row['camera_id'] == 'CAM_001')
        with TemporaryDirectory() as folder:
            file = Path(folder)/'redacted-fixture.mp4'; file.write_bytes(b'synthetic-ready-media')
            key = 'storage/terminal-projection-'+uuid4().hex+'.mp4'
            with get_connection() as conn:
                conn.execute("""INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,
                    status,redaction_status,created_at,request_id,capture_start_at,capture_end_at)
                    VALUES(?,?,?,'video',?,?,'ready','complete',CURRENT_TIMESTAMP,?,?,?)""",
                    (str(uuid4()), self.ident, received['camera_id'], received['role'], key,
                     received['request_id'], received['capture_start_at'], received['capture_end_at']))
                conn.execute("UPDATE event_clip_request SET status='received' WHERE request_id=?", (received['request_id'],))
            before = self.snapshot()
            with patch('backend.media.paths.owned_path', return_value=file), \
                 patch('backend.media.service.owned_path', return_value=file):
                response = self.client.get('/api/events/'+self.ident+'/media', headers=self.admin)
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                detail = self.client.get('/api/events/'+self.ident, headers=self.admin).json()['event']
            self.assertEqual(result['collection'], detail['collection'])
            self.assertEqual(result['collection']['phase'], 'interrupted')
            report = next(row['capture_report'] for row in result['collection']['cameras'] if row['camera_id'] == 'CAM_002')
            self.assertEqual(report['state'], 'incomplete')
            self.assertEqual(report['missing_ranges'][0]['reason'], 'source_failed')
            self.assertNotIn('session_id', report)
            self.assertEqual(len(result['collection']['segments']), 4)
            self.assertEqual(sum(row['status']=='pending' for row in result['clip_requests']), 3)
            self.assertEqual(sum(row['status']=='received' for row in result['clip_requests']), 1)
            self.assertEqual(result['items'][0]['url'], '/'+key)
            self.assertFalse(result['review_availability']['can_review'])
            self.assertEqual(self.snapshot(), before, 'GET projection must not write evidence/review state')
            self.assertEqual(self.client.get('/api/events/'+self.ident+'/media').status_code, 401)
            if os.environ.get('PPE_TERMINAL_DISPLAY_FIXTURE'):
                output = Path(os.environ['PPE_TERMINAL_DISPLAY_FIXTURE'])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_legacy_media_response_has_no_invented_collection(self):
        response = self.client.get('/api/events/EVT_0001/media', headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()['collection'])


if __name__ == '__main__':
    unittest.main()
