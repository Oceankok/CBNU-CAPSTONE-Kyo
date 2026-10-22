import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from backend.db.connection import is_postgres, schema_version
from backend.db.event_repository import get_connection, insert_candidate_event
from backend.db.migrations import LATEST_VERSION
from backend.media.service import register_media
from backend.tests.support import isolated_api, login


class CameraGroupTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        response = self.client.post('/api/nodes', headers=self.admin, json={'node_id': 'group-processor', 'name': '처리 node'})
        self.node = {'Authorization': 'Bearer '+response.json()['token']}
        self.client.put('/api/processing/workers/group-processor', headers=self.admin, json={'enabled': True}).raise_for_status()
        self.client.post('/api/field/heartbeat', headers=self.node).raise_for_status()
        self.client.post('/api/cameras', headers=self.admin, json={'camera_id': 'GROUP_CAM_3', 'status': 'active'}).raise_for_status()

    def group(self, cameras=None):
        return self.client.post('/api/camera-groups', headers=self.admin,
                                json={'name': '프레스 겹치는 촬영 구역', 'camera_ids': cameras or ['CAM_001', 'CAM_002']})

    def event(self, camera='CAM_001'):
        ident = 'GROUP_EVT_'+uuid4().hex
        insert_candidate_event(dict(event_id=ident, camera_id=camera, tracking_id='local-track', ppe_type='helmet',
                                    timestamp_start='2026-10-23 10:00:00', timestamp_end='2026-10-23 10:00:03',
                                    duration_sec=3, frame_sample_count=45, thumbnail_path='', video_clip_path='',
                                    ai_confidence=0.9, person_detected=1, ppe_detected=0, model_version='test-only', event_status='pending'))
        return ident

    def requests(self, event_id):
        return self.client.get('/api/processing/events/'+event_id+'/clip-requests', headers=self.node)

    def test_admin_crud_permissions_and_atomic_member_validation(self):
        self.assertEqual(self.client.get('/api/camera-groups').status_code, 401)
        self.assertEqual(self.client.get('/api/camera-groups', headers=login(self.client, 'worker01')).status_code, 403)
        self.assertEqual(self.group(['CAM_001', 'MISSING']).status_code, 404)
        self.assertEqual(self.group(['CAM_001', 'CAM_001']).status_code, 422)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM camera_group').fetchone()[0], 0)
        response = self.group()
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()
        self.assertEqual(row['created_by'], 'admin02')
        self.assertEqual(row['camera_ids'], ['CAM_001', 'CAM_002'])
        self.assertIn('source_node_id', row['cameras'][0])
        url = '/api/camera-groups/'+row['group_id']
        body = {'name': '수정 그룹', 'camera_ids': ['CAM_001', 'GROUP_CAM_3'], 'expected_version': 0}
        self.assertEqual(self.client.put(url, headers=self.admin, json=body).status_code, 200)
        self.assertEqual(self.client.put(url, headers=self.admin, json=body).status_code, 409)
        self.assertEqual(self.client.delete(url+'?expected_version=0', headers=self.admin).status_code, 409)
        self.assertEqual(self.client.delete(url+'?expected_version=1', headers=self.admin).status_code, 200)

    def test_event_creation_registers_only_active_peers_without_event_merging(self):
        self.group().raise_for_status()
        self.group(['CAM_001', 'CAM_002', 'GROUP_CAM_3']).raise_for_status()
        with get_connection() as conn:
            conn.execute("UPDATE camera_info SET status='inactive' WHERE camera_id='GROUP_CAM_3'")
        first = self.event()
        result = self.requests(first)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['timestamp_start'], '2026-10-23 10:00:00')
        items = result.json()['items']
        self.assertEqual([row['camera_id'] for row in items], ['CAM_002'])
        self.assertEqual(items[0]['role'], 'reference')
        self.assertEqual(items[0]['status'], 'pending')
        second = self.event('CAM_002')
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM candidate_event WHERE event_id IN (?,?)', (first, second)).fetchone()[0], 2)
        self.assertEqual([row['camera_id'] for row in self.requests(second).json()['items']], ['CAM_001'])

    def test_group_changes_keep_historical_requests_and_ungrouped_events_unchanged(self):
        self.assertEqual(self.requests(self.event()).json()['items'], [])
        row = self.group().json()
        event_id = self.event()
        self.client.delete('/api/camera-groups/'+row['group_id']+'?expected_version=0', headers=self.admin).raise_for_status()
        self.assertEqual(len(self.requests(event_id).json()['items']), 1)
        self.assertEqual(self.requests(self.event()).json()['items'], [])

    def test_processor_access_and_deletion_guard(self):
        self.group().raise_for_status()
        event_id = self.event()
        url = '/api/processing/events/'+event_id+'/clip-requests'
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.admin).status_code, 401)
        ordinary = self.client.post('/api/nodes', headers=self.admin, json={'node_id': 'ordinary-group-node', 'name': '현장 node'}).json()
        self.assertEqual(self.client.get(url, headers={'Authorization': 'Bearer '+ordinary['token']}).status_code, 403)
        with get_connection() as conn:
            conn.execute("UPDATE event_retention SET decision='delete' WHERE event_id=?", (event_id,))
        self.assertEqual(self.requests(event_id).status_code, 409)

    def test_reference_clip_role_is_preserved_and_wrong_role_rejected(self):
        self.group().raise_for_status()
        event_id = self.event()
        request = self.requests(event_id).json()['items'][0]
        with self.assertRaisesRegex(ValueError, 'invalid_clip_request_media'):
            register_media(event_id, request['camera_id'], 'video', 'clip', request_id=request['request_id'])
        mocked = AsyncMock(return_value={'status': 'queued'})
        with patch('backend.media.routes.upload_media', mocked):
            response = self.client.post('/api/processing/events/'+event_id+'/clip?request_id='+request['request_id'],
                                        headers=self.node, content=b'synthetic-contract-fixture')
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(mocked.call_args.kwargs['role'], 'reference')
        self.assertEqual(mocked.call_args.kwargs['camera_id'], 'CAM_002')
        ident = register_media(event_id, request['camera_id'], 'video', 'reference', request_id=request['request_id'])
        with get_connection() as conn:
            row = conn.execute('SELECT camera_id,role FROM event_media WHERE media_id=?', (ident,)).fetchone()
            self.assertEqual(row['role'], 'reference')
            self.assertEqual(row['camera_id'], 'CAM_002')

    def test_processor_reports_missing_clip_without_admin_credentials(self):
        self.group().raise_for_status()
        event_id = self.event()
        request = self.requests(event_id).json()['items'][0]
        url = '/api/processing/events/'+event_id+'/clip-requests/'+request['request_id']
        body = {'status': 'failed', 'error_code': 'buffer_read_failed'}
        self.assertEqual(self.client.put(url, headers=self.node, json=body).status_code, 200)
        self.assertEqual(self.requests(event_id).json()['items'][0]['status'], 'failed')
        self.assertEqual(self.client.put(url, headers=self.node, json=body).status_code, 409)
        body = {'status': 'unavailable', 'error_code': 'buffer_window_expired'}
        self.assertEqual(self.client.put(url, headers=self.node, json=body).status_code, 200)
        row = self.requests(event_id).json()['items'][0]
        self.assertEqual(row['status'], 'unavailable')
        self.assertEqual(row['error_code'], 'buffer_window_expired')
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_media WHERE event_id=?', (event_id,)).fetchone()[0], 0)

    def test_clip_failure_reporting_permissions_and_validation(self):
        self.group().raise_for_status()
        event_id = self.event()
        request = self.requests(event_id).json()['items'][0]
        url = '/api/processing/events/'+event_id+'/clip-requests/'+request['request_id']
        body = {'status': 'unavailable', 'error_code': 'buffer_missing'}
        self.assertEqual(self.client.put(url, json=body).status_code, 401)
        self.assertEqual(self.client.put(url, headers=self.admin, json=body).status_code, 401)
        ordinary = self.client.post('/api/nodes', headers=self.admin, json={'node_id': 'clip-sender', 'name': '송출 PC'}).json()
        self.assertEqual(self.client.put(url, headers={'Authorization': 'Bearer '+ordinary['token']}, json=body).status_code, 403)
        self.assertEqual(self.client.put(url, headers=self.node, json={'status': 'received', 'error_code': 'fake'}).status_code, 422)
        self.assertEqual(self.client.put(url, headers=self.node, json={'status': 'failed', 'error_code': '   '}).status_code, 422)
        self.assertEqual(self.client.put(url+'missing', headers=self.node, json=body).status_code, 404)
        self.assertEqual(self.requests(event_id).json()['items'][0]['status'], 'pending')
        self.client.put('/api/processing/workers/group-processor', headers=self.admin, json={'enabled': False}).raise_for_status()
        self.assertEqual(self.client.put(url, headers=self.node, json=body).status_code, 403)

    def test_v12_upgrade_adds_reference_role_and_preserves_events(self):
        from backend.db.init_db import init_database
        with get_connection() as conn:
            events = conn.execute('SELECT COUNT(*) FROM candidate_event').fetchone()[0]
            conn.execute('DROP TABLE camera_group_member')
            conn.execute('DROP TABLE camera_group')
            conn.execute('ALTER TABLE event_clip_request DROP COLUMN role')
            conn.execute('UPDATE app_schema_version SET version=12 WHERE singleton=1' if is_postgres() else 'PRAGMA user_version=12')
        init_database()
        init_database()
        with get_connection() as conn:
            self.assertEqual(schema_version(conn), LATEST_VERSION)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM candidate_event').fetchone()[0], events)
            conn.execute("SELECT role FROM event_clip_request").fetchall()
