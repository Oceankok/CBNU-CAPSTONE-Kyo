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
