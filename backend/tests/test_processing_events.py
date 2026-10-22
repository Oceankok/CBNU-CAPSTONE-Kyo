from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login


class ProcessingEventTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        token = self.client.post('/api/nodes', headers=self.admin, json={'node_id': 'event-processor', 'name': '추론 서버'}).json()['token']
        self.node = {'Authorization': 'Bearer '+token}
        self.client.put('/api/processing/workers/event-processor', headers=self.admin, json={'enabled': True}).raise_for_status()
        self.client.post('/api/camera-groups', headers=self.admin, json={'name': '이벤트 테스트', 'camera_ids': ['CAM_001', 'CAM_002']}).raise_for_status()
        stamp = datetime.now(timezone.utc).isoformat()
        self.body = {'event_id': 'EVT_'+uuid4().hex, 'camera_id': 'CAM_001', 'tracking_id': 'camera-local', 'ppe_type': 'helmet',
                     'timestamp_start': stamp, 'timestamp_end': stamp, 'confidence': 0.9, 'frame_sample_count': 1, 'model_version': 'test-model'}

    def submit(self, **changes):
        return self.client.post('/api/processing/events', headers=self.node, json={**self.body, **changes})

    def test_idempotency_and_group_requests(self):
        first = self.submit()
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual({(row['camera_id'], row['role']) for row in first.json()['clip_requests']}, {('CAM_001', 'clip'), ('CAM_002', 'reference')})
        second = self.submit()
        self.assertEqual(second.status_code, 200, second.text)
        self.assertFalse(second.json()['created'])
        self.assertEqual(first.json()['clip_requests'], second.json()['clip_requests'])
        self.assertEqual(self.submit(confidence=0.8).status_code, 409)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM candidate_event WHERE event_id=?', (self.body['event_id'],)).fetchone()[0], 1)

    def test_permissions_camera_and_payload_validation(self):
        for headers, status in (({}, 401), (self.admin, 401)):
            self.assertEqual(self.client.post('/api/processing/events', headers=headers, json=self.body).status_code, status)
        self.client.put('/api/processing/workers/event-processor', headers=self.admin, json={'enabled': False}).raise_for_status()
        self.assertEqual(self.submit().status_code, 403)
        self.client.put('/api/processing/workers/event-processor', headers=self.admin, json={'enabled': True}).raise_for_status()
        self.assertEqual(self.submit(camera_id='missing').status_code, 404)
        self.client.delete('/api/cameras/CAM_001', headers=self.admin).raise_for_status()
        self.assertEqual(self.submit().status_code, 409)
        self.client.patch('/api/cameras/CAM_001', headers=self.admin, json={'status': 'active'}).raise_for_status()
        for changes in ({'confidence': 2}, {'ppe_type': 'unknown'}, {'timestamp_start': '2026-10-23T00:00:00'},
                        {'timestamp_end': '2020-01-01T00:00:00Z'}, {'event_id': 'arbitrary'}, {'source_path': 'C:/private.mp4'}):
            self.assertEqual(self.submit(**changes).status_code, 422)

    def test_group_failure_rolls_back_event_and_retention(self):
        with patch('backend.camera_groups.service.register_references', side_effect=RuntimeError('synthetic DB failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic DB failure'):
                self.submit()
        with get_connection() as conn:
            for table in ('candidate_event', 'event_retention', 'event_clip_request'):
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table} WHERE event_id=?', (self.body['event_id'],)).fetchone()[0], 0)

    def test_processing_policy_matches_administrator_contract(self):
        url = '/api/processing/cameras/CAM_001/ppe-policy'
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.admin).status_code, 401)
        response = self.client.get(url, headers=self.node)
        self.assertEqual(response.status_code, 200, response.text)
        expected = self.client.get('/api/cameras/CAM_001/ppe-policy', headers=self.admin).json()
        self.assertEqual(response.json(), expected)
        self.assertEqual(self.client.get('/api/processing/cameras/missing/ppe-policy', headers=self.node).status_code, 404)
        self.client.put('/api/processing/workers/event-processor', headers=self.admin, json={'enabled': False}).raise_for_status()
        self.assertEqual(self.client.get(url, headers=self.node).status_code, 403)

    def test_broadcast_failure_can_be_retried_without_duplicate_event(self):
        with patch('backend.processing.events.enqueue_event_broadcast', side_effect=HTTPException(503, 'test-only')):
            self.assertEqual(self.submit().status_code, 503)
        retry = self.submit()
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertFalse(retry.json()['created'])
        self.assertEqual(len(retry.json()['clip_requests']), 2)

    def test_concurrent_submissions_create_one_event_and_reviewed_retry_never_broadcasts(self):
        from concurrent.futures import ThreadPoolExecutor
        from backend.processing.events import EventSubmission, submit
        body = EventSubmission.model_validate(self.body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(body), range(2)))
        self.assertEqual(sorted(row['created'] for row in results), [False, True])
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_clip_request WHERE event_id=?', (body.event_id,)).fetchone()[0], 2)
            conn.execute("UPDATE candidate_event SET event_status='hold' WHERE event_id=?", (body.event_id,))
        with patch('backend.processing.events.enqueue_event_broadcast') as broadcast:
            result = self.submit()
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['broadcast']['reason'], 'event_no_longer_pending')
            broadcast.assert_not_called()
