import unittest
from uuid import uuid4

from backend.db.connection import is_postgres, schema_version
from backend.db.event_repository import get_connection, insert_candidate_event, generate_quarterly_stats
from backend.db.migrations import LATEST_VERSION
from backend.media.service import register_media
from backend.tests.support import isolated_api, login, ready_media


class EventMergeTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        self.target = self.event('CAM_001')
        self.source = self.event('CAM_002')

    def event(self, camera, ppe='helmet'):
        ident = 'MERGE_'+uuid4().hex
        insert_candidate_event(dict(event_id=ident, camera_id=camera, tracking_id='camera-local', ppe_type=ppe,
                                    timestamp_start='2026-10-23 10:00:00', timestamp_end='2026-10-23 10:00:03',
                                    duration_sec=3, frame_sample_count=45, thumbnail_path='', video_clip_path='',
                                    ai_confidence=0.9, person_detected=1, ppe_detected=0, model_version='fixture', event_status='pending'))
        return ident

    def merge(self, sources=None, version=0, headers=None):
        return self.client.post('/api/events/'+self.target+'/merge', headers=headers or self.admin,
                                json={'source_event_ids': sources or [self.source], 'expected_version': version,
                                      'reason': '두 카메라 영상을 확인한 동일 사건'})

    def test_merge_moves_media_preserves_origin_and_excludes_duplicate_list_and_statistics(self):
        before = generate_quarterly_stats('2026-Q4')['summary']['candidate_count']
        with ready_media(self.source) as media_id:
            result = self.merge()
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['merge_version'], 1)
            self.assertEqual(result.json()['merge_history'][0]['merged_by'], 'admin02')
            self.assertEqual(result.json()['merge_history'][0]['media_ids'], [media_id])
            root = self.client.get('/api/events/'+self.target, headers=self.admin).json()['event']
            self.assertEqual(root['merged_event_ids'], [self.source])
            media = next(item for item in root['media'] if item['media_id'] == media_id)
            self.assertEqual(media['camera_id'], 'CAM_002')
            self.assertIsNotNone(media['url'])
            source = self.client.get('/api/events/'+self.source, headers=self.admin).json()['event']
            self.assertEqual(source['event_status'], 'merged')
            self.assertEqual(source['merged_into_event_id'], self.target)
            self.assertEqual(source['media'], [])
            ids = [item['event_id'] for item in self.client.get('/api/events', headers=self.admin).json()['items']]
            self.assertIn(self.target, ids)
            self.assertNotIn(self.source, ids)
            with get_connection() as conn:
                self.assertEqual(conn.execute('SELECT candidate_count FROM quarterly_summary WHERE quarter=?', ('2026-Q4',)).fetchone()[0], before-1)
                self.assertEqual(conn.execute('SELECT event_id FROM event_media WHERE media_id=?', (media_id,)).fetchone()[0], self.target)
            self.assertEqual(generate_quarterly_stats('2026-Q4')['summary']['candidate_count'], before-1)

    def test_role_validation_stale_version_and_conflicting_sources_are_atomic(self):
        url = '/api/events/'+self.target+'/merge'
        body = {'source_event_ids': [self.source], 'expected_version': 0, 'reason': '확인'}
        self.assertEqual(self.client.post(url, json=body).status_code, 401)
        self.assertEqual(self.merge(headers=login(self.client, 'worker01')).status_code, 403)
        self.assertEqual(self.merge([self.source, self.source]).status_code, 422)
        self.assertEqual(self.merge([self.target]).status_code, 422)
        other = self.event('CAM_002', 'vest')
        self.assertEqual(self.merge([self.source, other]).status_code, 409)
        self.assertEqual(self.merge([self.source, 'missing']).status_code, 404)
        self.assertEqual(self.merge(version=99).status_code, 409)
        self.assertEqual(self.client.post(url, headers=self.admin, json={**body, 'reason': '   '}).status_code, 422)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_merge').fetchone()[0], 0)
        self.assertEqual(self.merge().status_code, 200)
        self.assertEqual(self.merge().status_code, 409)
        third = self.event('CAM_002')
        self.assertEqual(self.merge([third]).status_code, 409)
        self.assertEqual(self.merge([third], version=1).status_code, 200)

    def test_reviewed_and_retention_decided_events_cannot_be_merged(self):
        response = self.client.post('/api/events/'+self.source+'/review', headers=self.admin,
                                    json={'review_result': 'hold', 'review_reason_code': 'unclear'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.merge().status_code, 409)
        source = self.event('CAM_002')
        with get_connection() as conn:
            conn.execute("UPDATE event_retention SET decision='delete',version=1 WHERE event_id=?", (source,))
        self.assertEqual(self.merge([source]).status_code, 409)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_merge').fetchone()[0], 0)

    def test_pending_delivery_and_processing_block_merge_then_terminal_requests_move(self):
        response = self.client.post('/api/events/'+self.source+'/clip-requests', headers=self.admin, json={'camera_id': 'CAM_002'})
        request_id = response.json()['request_id']
        self.assertEqual(self.merge().json()['detail'], 'clip_delivery_pending')
        url = '/api/events/'+self.source+'/clip-requests/'+request_id
        self.client.put(url, headers=self.admin, json={'status': 'failed', 'error_code': 'buffer_error'}).raise_for_status()
        self.assertEqual(self.merge().status_code, 409)
        self.client.put(url, headers=self.admin, json={'status': 'unavailable', 'error_code': 'buffer_expired'}).raise_for_status()
        media_id = register_media(self.source, 'CAM_002', 'image', 'reference')
        self.assertEqual(self.merge().json()['detail'], 'media_processing_pending')
        with get_connection() as conn:
            conn.execute("UPDATE event_media SET status='failed',processing_started_at=NULL WHERE media_id=?", (media_id,))
        result = self.merge()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['merge_history'][0]['request_ids'], [request_id])
        with get_connection() as conn:
            row = conn.execute('SELECT event_id,role,status FROM event_clip_request WHERE request_id=?', (request_id,)).fetchone()
            self.assertEqual((row['event_id'], row['role'], row['status']), (self.target, 'reference', 'unavailable'))

    def test_merged_source_rejects_new_review_upload_and_clip_request(self):
        self.merge().raise_for_status()
        response = self.client.post('/api/events/'+self.source+'/review', headers=self.admin,
                                    json={'review_result': 'hold', 'review_reason_code': 'unclear'})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail'], 'event_merged')
        with self.assertRaisesRegex(ValueError, 'event_merged'):
            register_media(self.source, 'CAM_002', 'image', 'reference')
        self.assertEqual(self.client.post('/api/events/'+self.source+'/clip-requests', headers=self.admin,
                                         json={'camera_id': 'CAM_002'}).status_code, 409)
        from unittest.mock import patch
        from backend.field.service import enqueue_event_broadcast
        with patch('backend.field.service.get_broadcast_settings', return_value={'enabled': True}):
            self.assertEqual(enqueue_event_broadcast(self.source), {'queued': False, 'reason': 'event_merged'})
        node = self.client.post('/api/nodes', headers=self.admin, json={'node_id': 'merge-reader', 'name': '처리 PC'}).json()
        self.client.put('/api/processing/workers/merge-reader', headers=self.admin, json={'enabled': True}).raise_for_status()
        response = self.client.get('/api/processing/events/'+self.source+'/clip-requests', headers={'Authorization': 'Bearer '+node['token']})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['merged_into_event_id'], self.target)

    def test_concurrent_merges_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from fastapi import HTTPException
        from backend.event_merge.service import merge

        def attempt():
            try:
                merge(self.target, [self.source], 0, '동일 사건 확인', 'admin02')
                return 200
            except HTTPException as exc:
                return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(results), [200, 409])
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_merge').fetchone()[0], 1)

    def test_root_review_and_delete_apply_to_moved_media(self):
        from backend.media.service import decide_retention
        with ready_media(self.source) as media_id:
            self.merge().raise_for_status()
            response = self.client.post('/api/events/'+self.target+'/review', headers=self.admin,
                                        json={'review_result': 'confirmed', 'review_reason_code': 'confirmed_no_helmet'})
            self.assertEqual(response.status_code, 200, response.text)
            deleted = decide_retention(self.target, 'delete', 'admin02', 0)
            self.assertEqual(deleted['decision'], 'delete')
            with get_connection() as conn:
                self.assertEqual(conn.execute('SELECT status FROM event_media WHERE media_id=?', (media_id,)).fetchone()[0], 'deleted')
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_merge').fetchone()[0], 1)

    def test_root_cannot_be_reparented_and_v13_migration_preserves_data(self):
        self.merge().raise_for_status()
        third = self.event('CAM_001')
        response = self.client.post('/api/events/'+third+'/merge', headers=self.admin,
                                    json={'source_event_ids': [self.target], 'expected_version': 0, 'reason': '재병합'})
        self.assertEqual(response.status_code, 409)
        with get_connection() as conn:
            conn.execute('DELETE FROM event_merge')
            conn.execute('DROP TABLE event_merge')
            conn.execute('UPDATE app_schema_version SET version=13 WHERE singleton=1' if is_postgres() else 'PRAGMA user_version=13')
        from backend.db.init_db import init_database
        init_database()
        init_database()
        with get_connection() as conn:
            self.assertEqual(schema_version(conn), LATEST_VERSION)
            self.assertIsNotNone(conn.execute('SELECT event_id FROM candidate_event WHERE event_id=?', (self.target,)).fetchone())


if __name__ == '__main__':
    unittest.main()
