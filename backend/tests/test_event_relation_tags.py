"""Optional computed tags preserve legacy responses and explicit release semantics."""
import unittest
from uuid import uuid4
from backend.tests.support import isolated_api, login
from backend.db.event_repository import get_connection, insert_candidate_event


class AutomaticRelationTagTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        self.client.post('/api/camera-groups', headers=self.admin,
            json={'name': 'Overlap', 'camera_ids': ['CAM_001', 'CAM_002']}).raise_for_status()

    def event(self, camera='CAM_001', ppe='helmet'):
        ident = 'EVT_' + uuid4().hex
        insert_candidate_event(dict(event_id=ident, camera_id=camera, tracking_id=None, ppe_type=ppe,
            timestamp_start='2026-11-12T10:00:00Z', timestamp_end='2026-11-12T10:00:10Z',
            duration_sec=10, frame_sample_count=1, thumbnail_path=None, video_clip_path=None,
            ai_confidence=.9, person_detected=1, ppe_detected=0, model_version='synthetic', event_status='pending'))
        return ident

    def tag(self, ident):
        response = self.client.get('/api/events/'+ident+'?include_suspected_duplicates=true', headers=self.admin)
        response.raise_for_status()
        return response.json()['event']['suspected_duplicate_tag']

    def test_auto_computed_list_detail_and_legacy_unchanged(self):
        a=self.event(); b=self.event('CAM_002'); self.event('CAM_002','vest')
        legacy=self.client.get('/api/events/'+a,headers=self.admin).json()['event']
        self.assertNotIn('suspected_duplicate_tag',legacy)
        tag=self.tag(a)
        self.assertEqual(tag['candidate_count'],1)
        self.assertEqual(tag['tag'],'suspected_duplicate')
        page=self.client.get('/api/events?include_suspected_duplicates=true&ppe_type=helmet&limit=100',headers=self.admin).json()
        found={x['event_id']:x for x in page['items']}
        self.assertEqual(found[a]['suspected_duplicate_tag'],tag)
        self.assertEqual(self.tag(b)['candidate_count'],1)
        self.assertFalse(tag['identity_confirmed']); self.assertFalse(tag['persisted'])

    def test_reads_never_save_or_change_event_statistics(self):
        a=self.event(); self.event('CAM_002')
        with get_connection() as conn:
            before=[dict(x) for x in conn.execute('SELECT * FROM candidate_event ORDER BY event_id')]
        self.tag(a)
        with get_connection() as conn:
            self.assertEqual(before,[dict(x) for x in conn.execute('SELECT * FROM candidate_event ORDER BY event_id')])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM saved_event_relation').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_merge').fetchone()[0],0)

    def test_release_is_shown_not_automatically_recreated_or_suppressed(self):
        a=self.event(); b=self.event('CAM_002')
        base='/api/events/'+a
        evidence=self.client.get(base+'/suspected-duplicates/evidence/'+b,headers=self.admin).json()
        body={'expected_fingerprint':evidence['fingerprint']}
        saved=self.client.post(base+'/saved-suspected-relations/'+b,headers=self.admin,json=body).json()
        self.assertEqual(self.tag(a)['saved_record_count'],1)
        self.client.post('/api/events/saved-suspected-relations/'+saved['relation_id']+'/release',headers=self.admin,json=body).raise_for_status()
        tag=self.tag(a)
        self.assertEqual(tag['released_record_count'],1); self.assertEqual(tag['saved_record_count'],0)
        self.assertEqual(tag['candidate_count'],1)
        self.assertFalse(tag['release_suppresses_computed_tag'])
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM saved_event_relation').fetchone()[0],1)

    def test_candidate_disappears_after_merge_or_delete(self):
        a=self.event(); b=self.event('CAM_002')
        with get_connection() as conn:
            conn.execute("INSERT INTO event_merge VALUES(?,?,'admin02','now','test','[]','[]')",(b,a))
        self.assertIsNone(self.tag(a)['tag'])
        self.assertEqual(self.tag(b)['reason'],'event_was_merged')
        with get_connection() as conn:
            conn.execute('DELETE FROM event_merge WHERE source_event_id=?',(b,))
            conn.execute('DELETE FROM candidate_event WHERE event_id=?',(b,))
        self.assertEqual(self.tag(a)['candidate_count'],0)

    def test_unavailable_is_distinguished_from_no_matches(self):
        legacy=self.tag('EVT_0001')
        self.assertFalse(legacy['eligible'])
        self.assertEqual(legacy['reason'],'registration_group_snapshot_unavailable')
        empty=self.tag(self.event())
        self.assertTrue(empty['eligible']); self.assertEqual(empty['candidate_count'],0)

    def test_auth_and_missing(self):
        a=self.event(); path='/api/events/'+a+'?include_suspected_duplicates=true'
        self.assertEqual(self.client.get(path).status_code,401)
        self.assertEqual(self.client.get(path,headers=login(self.client,'worker01')).status_code,403)
        self.assertEqual(self.client.get('/api/events/missing?include_suspected_duplicates=true',headers=self.admin).status_code,404)
