import unittest

from backend.db.connection import is_postgres, schema_version
from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login
from backend.work_areas.geometry import validate_points
from inference_worker.work_area_policy import resolve_person_policy

POLYGON = [[0.1, 0.1], [0.6, 0.1], [0.6, 0.8], [0.1, 0.8]]


class WorkAreaTests(unittest.TestCase):
    def setUp(self):
        context = isolated_api()
        self.client = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.admin = login(self.client)
        self.worker = login(self.client, 'worker01')
        self.root = '/api/cameras/CAM_001'
        self.client.put('/api/zones/프레스 구역', headers=self.admin, json={'required_ppe': ['helmet'], 'rules': []}).raise_for_status()
        response = self.client.post('/api/equipment', headers=self.admin,
            json={'name': '프레스', 'zone_name': '프레스 구역', 'required_ppe': ['vest']})
        response.raise_for_status()
        self.equipment = response.json()['equipment_id']
        self.client.put(self.root+'/equipment', headers=self.admin, json={'equipment_ids': [self.equipment]}).raise_for_status()

    def create(self, **changes):
        body = {'name': '작업 위치', 'equipment_id': self.equipment, 'points': POLYGON}
        response = self.client.post(self.root+'/work-areas', headers=self.admin, json={**body, **changes})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def policy(self):
        result = self.client.get(self.root+'/work-area-policy', headers=self.admin)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def mode(self, enabled=True):
        version = self.client.get(self.root+'/work-areas', headers=self.admin).json()['version']
        response = self.client.put(self.root+'/work-area-config', headers=self.admin,
                                   json={'enabled': enabled, 'expected_version': version})
        response.raise_for_status()
        return response.json()

    def test_crud_versions_and_last_delete_keeps_invalid_area_mode(self):
        initial = self.client.get(self.root+'/work-areas', headers=self.admin).json()
        self.assertEqual(initial['version'], 0)
        self.assertFalse(initial['enabled'])
        area = self.create()
        enabled = self.mode()
        path = self.root+'/work-areas/'+area['area_id']
        body = {'name': '위치 변경', 'equipment_id': self.equipment, 'points': POLYGON, 'expected_version': 1}
        result = self.client.patch(path, headers=self.admin, json=body)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['version'], 2)
        self.assertEqual(self.client.patch(path, headers=self.admin, json=body).status_code, 409)
        self.assertEqual(self.client.delete(path, headers=self.admin, params={'expected_version': 1}).status_code, 409)
        self.assertEqual(self.client.delete(path, headers=self.admin, params={'expected_version': 2}).status_code, 200)
        self.assertIn('work_areas_empty', self.policy()['configuration_issues'])
        stale = self.client.put(self.root+'/work-area-config', headers=self.admin,
            json={'enabled': False, 'expected_version': enabled['version']})
        self.assertEqual(stale.status_code, 409)
        self.mode(False)
        self.assertEqual(self.policy()['scope'], 'camera')

    def test_polygon_validation_and_api_atomicity(self):
        invalid = [POLYGON+[POLYGON[0]], [[0,0],[1,1],[0,1],[1,0]],
                   [[0,0],[0.5,0.5],[1,1]], [[0,0],[True,1],[1,0]],
                   [[0,0],[1.1,0],[1,1]], [[0,0],[0.5,0],[1,0],[1,1]]]
        for points in invalid:
            with self.subTest(points=points):
                with self.assertRaises(ValueError):
                    validate_points(points)
                response = self.client.post(self.root+'/work-areas', headers=self.admin,
                    json={'name': '영역', 'equipment_id': self.equipment, 'points': points})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get(self.root+'/work-areas', headers=self.admin).json()['version'], 0)
        self.assertEqual(validate_points(list(reversed(POLYGON))), list(reversed(POLYGON)))

    def test_person_inside_outside_boundary_and_overlapping_rules(self):
        first = self.create()
        second = self.client.post('/api/equipment', headers=self.admin,
            json={'name': '프레스 2', 'zone_name': '프레스 구역', 'required_ppe': ['gloves']}).json()['equipment_id']
        self.client.put(self.root+'/equipment', headers=self.admin, json={'equipment_ids': [self.equipment, second]}).raise_for_status()
        other = self.create(equipment_id=second)
        self.mode()
        policy = self.policy()
        inside = resolve_person_policy(policy, [10, 10, 30, 70], 100, 100)
        self.assertEqual(set(inside['required_ppe']), {'helmet', 'vest', 'gloves'})
        self.assertEqual(set(inside['area_ids']), {first['area_id'], other['area_id']})
        self.assertEqual(resolve_person_policy(policy, [70, 10, 90, 70], 100, 100)['required_ppe'], ['helmet'])
        self.assertEqual(len(resolve_person_policy(policy, [0, 0, 20, 80], 100, 100)['area_ids']), 2)
        with self.assertRaises(ValueError):
            resolve_person_policy(policy, [0, 0, 200, 80], 100, 100)

    def test_missing_links_zone_status_and_no_silent_policy_fallback(self):
        self.create()
        response = self.client.put(self.root+'/equipment', headers=self.admin, json={'equipment_ids': []})
        self.assertEqual(response.json()['detail'], 'equipment_has_work_areas')
        mismatch = self.client.post('/api/cameras/CAM_002/work-areas', headers=self.admin,
            json={'name': '다른 구역', 'equipment_id': self.equipment, 'points': POLYGON})
        self.assertEqual(mismatch.status_code, 409)
        self.mode()
        before = self.policy()['policy_version']
        self.client.patch('/api/equipment/'+self.equipment, headers=self.admin, json={'required_ppe': ['gloves']}).raise_for_status()
        self.assertNotEqual(before, self.policy()['policy_version'])
        self.client.delete('/api/equipment/'+self.equipment, headers=self.admin).raise_for_status()
        policy = self.policy()
        self.assertFalse(policy['configured'])
        self.assertIsNone(policy['required_ppe'])
        with self.assertRaises(ValueError):
            resolve_person_policy(policy, [10,10,20,30], 100,100)

    def test_admin_and_processor_contract_and_legacy_guard(self):
        node = self.client.post('/api/nodes', headers=self.admin, json={'node_id': 'roi_gpu', 'name': '처리 PC'}).json()
        headers = {'Authorization': 'Bearer '+node['token']}
        path = '/api/processing/cameras/CAM_001/work-area-policy'
        self.assertEqual(self.client.get(path, headers=headers).status_code, 403)
        self.client.put('/api/processing/workers/roi_gpu', headers=self.admin, json={'enabled': True}).raise_for_status()
        self.assertEqual(self.client.get(path, headers=headers).json()['scope'], 'camera')
        self.create()
        self.mode()
        self.assertEqual(self.client.get(path, headers=headers).json(), self.policy())
        legacy = self.client.get('/api/processing/cameras/CAM_001/ppe-policy', headers=headers).json()
        self.assertEqual(legacy['configuration_issues'], ['work_area_policy_required'])
        self.assertFalse(legacy['configured'])
        self.assertTrue(self.client.get(self.root+'/ppe-policy', headers=self.admin).json()['configured'])
        for path in (self.root+'/work-areas', self.root+'/work-area-policy'):
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers=self.worker).status_code, 403)
        self.assertEqual(self.client.post(self.root+'/work-areas', headers=self.worker, json={}).status_code, 403)
        self.assertEqual(self.client.put(self.root+'/work-area-config', headers=self.worker, json={'enabled': False, 'expected_version': 0}).status_code, 403)

    def test_v16_upgrade_preserves_records_and_is_repeatable(self):
        from backend.db.migrations import LATEST_VERSION, apply_migrations
        from backend.db.postgres import initialize
        with get_connection() as conn:
            before = [dict(row) for row in conn.execute('SELECT * FROM camera_info ORDER BY camera_id')]
            for name in ('camera_snapshot', 'camera_work_area', 'camera_area_config'):
                conn.execute('DROP TABLE '+name)
            conn.execute('UPDATE app_schema_version SET version=16 WHERE singleton=1' if is_postgres() else 'PRAGMA user_version=16')
        for _ in range(2):
            if is_postgres():
                initialize()
            else:
                with get_connection() as conn:
                    apply_migrations(conn)
        with get_connection() as conn:
            self.assertEqual(schema_version(conn), LATEST_VERSION)
            self.assertEqual([dict(row) for row in conn.execute('SELECT * FROM camera_info ORDER BY camera_id')], before)
        self.assertEqual(self.policy()['scope'], 'camera')
