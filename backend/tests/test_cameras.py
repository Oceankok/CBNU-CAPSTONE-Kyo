import unittest

from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login


class CameraListingTests(unittest.TestCase):
    def setUp(self):
        self.context = isolated_api()
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.admin = login(self.client)

    def test_admin_only(self):
        self.assertEqual(self.client.get("/api/cameras").status_code, 401)
        worker = login(self.client, "worker01")
        self.assertEqual(self.client.get("/api/cameras", headers=worker).status_code, 403)
        self.assertEqual(self.client.get("/api/cameras", headers=self.admin).status_code, 200)

    def test_camera_without_events_is_listed_with_nullable_connections(self):
        with get_connection() as conn:
            conn.execute("INSERT INTO camera_info(camera_id,status) VALUES('CAM_NEW',NULL)")
        response = self.client.get("/api/cameras", headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        self.assertEqual([item["camera_id"] for item in items], sorted(item["camera_id"] for item in items))
        camera = next(item for item in items if item["camera_id"] == "CAM_NEW")
        self.assertEqual(camera, {"camera_id": "CAM_NEW", "camera_location": None,
                                 "zone_name": None, "status": "unknown",
                                 "process_type": None, "camera_angle_type": None,
                                 "source_node_id": None, "output_node_id": None})

    def test_separate_nodes_and_updated_connections_are_visible(self):
        for node_id in ("sourcepc", "broadcastpc", "replacementpc"):
            response = self.client.post("/api/nodes", headers=self.admin,
                                        json={"node_id": node_id, "name": node_id})
            self.assertEqual(response.status_code, 201, response.text)
        for output in ("broadcastpc", "replacementpc"):
            response = self.client.put("/api/cameras/CAM_001/node", headers=self.admin,
                                       json={"source_node_id": "sourcepc", "output_node_id": output})
            self.assertEqual(response.status_code, 200, response.text)
            camera = next(item for item in self.client.get("/api/cameras", headers=self.admin).json()["items"]
                          if item["camera_id"] == "CAM_001")
            self.assertEqual(camera["source_node_id"], "sourcepc")
            self.assertEqual(camera["output_node_id"], output)
            self.assertEqual(set(camera), {"camera_id", "camera_location", "zone_name", "status",
                                           "process_type", "camera_angle_type",
                                           "source_node_id", "output_node_id"})

    def test_zone_filter_and_status_normalization(self):
        with get_connection() as conn:
            conn.execute("UPDATE camera_info SET status='inactive' WHERE camera_id='CAM_001'")
            conn.execute("UPDATE camera_info SET status='legacy_status' WHERE camera_id='CAM_002'")
        response = self.client.get("/api/cameras", headers=self.admin, params={"zone_name": "프레스 구역"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["camera_id"] for item in response.json()["items"]], ["CAM_001"])
        self.assertEqual(response.json()["items"][0]["status"], "inactive")
        items = self.client.get("/api/cameras", headers=self.admin).json()["items"]
        self.assertEqual(next(item for item in items if item["camera_id"] == "CAM_002")["status"], "unknown")
        self.assertEqual(self.client.get("/api/cameras", headers=self.admin,
                                        params={"zone_name": "없는 구역"}).json(), {"items": []})

    def test_create_patch_and_permissions(self):
        body = {'camera_id':'CAM_NEW','camera_location':'입구','process_type':'press','camera_angle_type':'front'}
        self.assertEqual(self.client.post('/api/cameras',json=body).status_code,401)
        self.assertEqual(self.client.post('/api/cameras',headers=login(self.client,'worker01'),json=body).status_code,403)
        created = self.client.post('/api/cameras',headers=self.admin,json=body)
        self.assertEqual(created.status_code,201,created.text)
        item = created.json()
        self.assertIsNone(item['zone_name'])
        self.assertEqual(item['process_type'],'press')
        self.assertEqual(self.client.post('/api/cameras',headers=self.admin,json=body).status_code,409)
        path = '/api/cameras/CAM_NEW'
        self.assertEqual(self.client.patch(path,headers=self.admin,json={'zone_name':'missing'}).status_code,422)
        self.assertEqual(self.client.patch(path,headers=self.admin,json={'status':None}).status_code,422)
        update = self.client.patch(path,headers=self.admin,json={'zone_name':'프레스 구역','camera_location':'입구2'})
        self.assertEqual(update.status_code,200,update.text)
        listed = self.client.get('/api/cameras',headers=self.admin).json()['items']
        self.assertEqual(next(c for c in listed if c['camera_id']=='CAM_NEW'),update.json())

    def test_linked_zone_change_and_soft_delete_preserve_history(self):
        equipment = self.client.post('/api/equipment',headers=self.admin,json={
            'name':'프레스','zone_name':'프레스 구역','required_ppe':['helmet']})
        self.assertEqual(equipment.status_code,201,equipment.text)
        self.client.put('/api/cameras/CAM_001/equipment',headers=self.admin,json={'equipment_ids':[equipment.json()['equipment_id']]}).raise_for_status()
        self.assertEqual(self.client.patch('/api/cameras/CAM_001',headers=self.admin,json={'zone_name':None}).status_code,409)
        for _ in range(2):
            self.assertEqual(self.client.delete('/api/cameras/CAM_001',headers=self.admin).json()['status'],'inactive')
        with get_connection() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM candidate_event WHERE event_id='EVT_0001'").fetchone())

    def test_new_events_reject_unknown_or_inactive_camera(self):
        from backend.services.candidate_event_service import create_no_helmet_candidate_event
        from backend.cameras.service import CameraStateError
        for camera, code in [('missing','camera_not_found'),('CAM_001','camera_inactive')]:
            if camera == 'CAM_001':
                self.client.delete('/api/cameras/CAM_001',headers=self.admin).raise_for_status()
            with self.assertRaisesRegex(CameraStateError,code):
                create_no_helmet_candidate_event(camera_id=camera,confidence=.9)
