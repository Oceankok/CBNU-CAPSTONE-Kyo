import unittest
from uuid import uuid4
from backend.tests.support import isolated_api, login
from backend.db.event_repository import get_connection, insert_candidate_event
from backend.db.migrations import apply_migrations, LATEST_VERSION
from backend.db.connection import is_postgres


class EventRelationPreviewTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        self.group = self.client.post('/api/camera-groups', headers=self.admin,
            json={'name': 'Overlap', 'camera_ids': ['CAM_001', 'CAM_002']}).json()

    def event(self, camera='CAM_001', ppe='helmet', start='2026-11-12T10:00:00+00:00', end='2026-11-12T10:00:10+00:00'):
        ident = 'EVT_' + uuid4().hex
        insert_candidate_event(dict(event_id=ident, camera_id=camera, tracking_id=None, ppe_type=ppe,
            timestamp_start=start, timestamp_end=end, duration_sec=10, frame_sample_count=1,
            thumbnail_path=None, video_clip_path=None, ai_confidence=0.9, person_detected=1,
            ppe_detected=0, model_version='synthetic', event_status='pending'))
        return ident

    def preview(self, ident, query=''):
        return self.client.get('/api/events/' + ident + '/suspected-duplicates/preview' + query, headers=self.admin)

    def test_overlap_and_pagination_do_not_modify_events_or_counts(self):
        first = self.event()
        matches = [self.event('CAM_002') for _ in range(3)]
        self.event('CAM_001')
        self.event('CAM_002', 'vest')
        self.event('CAM_002', start='2026-11-12T10:00:10Z', end='2026-11-12T10:00:20Z')
        with get_connection() as conn:
            before = [dict(r) for r in conn.execute('SELECT event_id,event_status FROM candidate_event ORDER BY event_id')]
        a = self.preview(first, '?limit=2').json()
        b = self.preview(first, '?limit=2&offset=2').json()
        self.assertEqual(a['total'], 3)
        self.assertTrue(a['has_more']); self.assertFalse(b['has_more'])
        self.assertEqual({r['event_id'] for r in a['items'] + b['items']}, set(matches))
        self.assertEqual(a['items'][0]['overlap_seconds'], 10)
        self.assertFalse(a['persisted']); self.assertFalse(a['automatic_merge'])
        with get_connection() as conn:
            self.assertEqual([dict(r) for r in conn.execute('SELECT event_id,event_status FROM candidate_event ORDER BY event_id')], before)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_merge').fetchone()[0], 0)

    def test_snapshot_not_current_membership_and_revision_is_conservative(self):
        a = self.event(); b = self.event('CAM_002')
        group_id = self.group['group_id']
        self.client.put('/api/camera-groups/' + group_id, headers=self.admin, json={
            'name': 'renamed', 'camera_ids': ['CAM_001', 'CAM_002'], 'expected_version': 0}).raise_for_status()
        newer = self.event('CAM_002')
        self.assertEqual([r['event_id'] for r in self.preview(a).json()['items']], [b])
        self.assertNotIn(newer, [r['event_id'] for r in self.preview(a).json()['items']])
        self.client.delete('/api/camera-groups/' + group_id + '?expected_version=1', headers=self.admin).raise_for_status()
        self.assertEqual([r['event_id'] for r in self.preview(a).json()['items']], [b])
        self.assertEqual(self.preview(self.event()).json()['reason'], 'no_registered_camera_groups')

    def test_legacy_unknown_zero_duration_and_merged_events_are_explicit(self):
        self.assertEqual(self.preview('EVT_0001').json()['reason'], 'registration_group_snapshot_unavailable')
        unknown = self.event(start='2026-11-12 10:00:00', end='2026-11-12 10:00:10')
        self.assertEqual(self.preview(unknown).json()['reason'], 'observation_timezone_unknown')
        zero = self.event(end='2026-11-12T10:00:00Z')
        self.assertEqual(self.preview(zero).json()['reason'], 'positive_observation_interval_unavailable')
        a = self.event(); b = self.event('CAM_002')
        with get_connection() as conn:
            conn.execute("INSERT INTO event_merge VALUES(?,?,'admin02','now','test','[]','[]')", (b,a))
        self.assertEqual(self.preview(b).json()['reason'], 'event_was_merged')
        self.assertEqual(self.preview(a).json()['total'], 0)

    def test_auth_input_and_nonexistent_event(self):
        ident = self.event()
        path = '/api/events/' + ident + '/suspected-duplicates/preview'
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, headers=login(self.client, 'worker01')).status_code, 403)
        for query in ('?limit=0', '?limit=101', '?offset=-1', '?offset=10001'):
            self.assertEqual(self.preview(ident, query).status_code, 422)
        self.assertEqual(self.preview('missing').status_code, 404)

    def test_snapshot_rolls_back_with_event_and_delete_cascades(self):
        from unittest.mock import patch
        with patch('backend.camera_groups.service.register_references', side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError): self.event()
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_group_context').fetchone()[0], 0)
        a = self.event()
        with get_connection() as conn:
            conn.execute('DELETE FROM candidate_event WHERE event_id=?', (a,))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_group_context').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_group_membership_snapshot').fetchone()[0], 0)

    def test_migration_preserves_old_events_without_inventing_snapshots(self):
        with get_connection() as conn:
            conn.execute('DROP TABLE event_group_membership_snapshot')
            conn.execute('DROP TABLE event_group_context')
            conn.execute('UPDATE app_schema_version SET version=25 WHERE singleton=1' if is_postgres() else 'PRAGMA user_version=25')
            conn.commit()
            if not is_postgres():
                apply_migrations(conn); apply_migrations(conn)
        if is_postgres():
            from backend.db.postgres import initialize
            initialize(); initialize()
        with get_connection() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM candidate_event WHERE event_id='EVT_0001'").fetchone())
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_group_context').fetchone()[0], 0)
        self.assertEqual(self.preview('EVT_0001').json()['reason'], 'registration_group_snapshot_unavailable')

    def test_timezone_equivalent_observation_and_page_cap(self):
        from backend.event_relations.service import page_state
        a = self.event()
        b = self.event('CAM_002', start='2026-11-12T19:00:00+09:00', end='2026-11-12T19:00:10+09:00')
        item = self.preview(a).json()['items'][0]
        self.assertEqual(item['event_id'], b)
        self.assertEqual(item['overlap_seconds'], 10)
        self.assertEqual(page_state(10200, 10000, 100), {
            'has_more': False, 'next_offset': None, 'pagination_truncated': True})
        self.assertEqual(page_state(10200, 9900, 100), {
            'has_more': True, 'next_offset': 10000, 'pagination_truncated': False})
