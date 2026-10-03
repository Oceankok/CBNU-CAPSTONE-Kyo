import json
import time
import unittest
from unittest.mock import patch

from backend.db.event_repository import get_connection, get_broadcast_settings, save_broadcast_settings
from backend.field.service import enqueue_event_broadcast
from backend.tests.support import isolated_api, login
from field_agent.main import execute


class FieldBroadcastTests(unittest.TestCase):
    def setUp(self):
        context = isolated_api()
        self.client = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.admin = login(self.client)
        response = self.client.post("/api/nodes", headers=self.admin, json={"node_id": "pc1", "name": "Field PC"})
        self.assertEqual(response.status_code, 201)
        self.node = {"Authorization": "Bearer "+response.json()["token"]}
        self.client.put("/api/cameras/CAM_001/node", headers=self.admin,
                        json={"source_node_id": "pc1", "output_node_id": "pc1"}).raise_for_status()
        settings = get_broadcast_settings()
        settings.update(enabled=True, default_language="ko", cooldown_sec=30,
                        templates=[{"ppe_type": "helmet", "zone_name": "", "language": "ko", "message": "안전모를 착용하세요."}])
        save_broadcast_settings(settings)

    def heartbeat(self):
        self.assertEqual(self.client.post("/api/field/heartbeat", headers=self.node).status_code, 200)

    def test_node_auth_and_offline(self):
        self.assertEqual(self.client.post("/api/field/heartbeat", headers=self.admin).status_code, 401)
        self.assertEqual(self.client.get("/api/events", headers=self.node).status_code, 401)
        self.assertEqual(enqueue_event_broadcast("EVT_0001")["reason"], "node_offline")
        self.heartbeat()
        self.assertTrue(self.client.get("/api/nodes", headers=self.admin).json()["items"][0]["online"])

    def test_command_delivery_duplicate_result_and_ownership(self):
        self.heartbeat()
        queued = enqueue_event_broadcast("EVT_0001")
        self.assertTrue(queued["queued"])
        self.assertEqual(enqueue_event_broadcast("EVT_0001")["reason"], "already_requested")
        command = self.client.post("/api/field/commands/claim", headers=self.node).json()["command"]
        self.assertEqual(command["command_id"], queued["command_id"])
        self.assertIsNone(self.client.post("/api/field/commands/claim", headers=self.node).json()["command"])
        with patch("field_agent.main.speak_message", return_value={"spoken": True}) as speak:
            result = execute(command)
            speak.assert_called_once()
        other = self.client.post("/api/nodes", headers=self.admin, json={"node_id": "pc2", "name": "Other"}).json()
        url = f'/api/field/commands/{command["command_id"]}/result'
        self.assertEqual(self.client.put(url, headers={"Authorization": "Bearer "+other["token"]}, json=result).status_code, 404)
        for _ in range(2):
            self.assertEqual(self.client.put(url, headers=self.node, json=result).status_code, 200)
        rows = self.client.get("/api/nodes/pc1/commands", headers=self.admin).json()["items"]
        self.assertEqual(rows[0]["status"], "completed")

    def test_expiry_and_tts_failure(self):
        self.heartbeat()
        queued = enqueue_event_broadcast("EVT_0001")
        with get_connection() as conn:
            conn.execute("UPDATE field_command SET expires_at=0 WHERE command_id=?", (queued["command_id"],))
        self.assertIsNone(self.client.post("/api/field/commands/claim", headers=self.node).json()["command"])
        command = {"kind": "broadcast", "expires_at": time.time()+30, "payload": {"message": "test", "language": "ko"}}
        with patch("field_agent.main.speak_message", return_value={"spoken": False, "reason": "voice_not_found"}):
            self.assertEqual(execute(command)["status"], "failed")
        command["expires_at"] = 0
        with patch("field_agent.main.speak_message") as speak:
            self.assertEqual(execute(command)["status"], "skipped")
            speak.assert_not_called()

    def test_off_switch_and_cooldown(self):
        self.heartbeat()
        self.assertTrue(enqueue_event_broadcast("EVT_0001")["queued"])
        with get_connection() as conn:
            conn.execute("UPDATE candidate_event SET camera_id='CAM_001',ppe_type='helmet' WHERE event_id='EVT_0002'")
        self.assertEqual(enqueue_event_broadcast("EVT_0002")["reason"], "cooldown_active")
        settings = get_broadcast_settings()
        settings["enabled"] = False
        save_broadcast_settings(settings)
        self.assertEqual(enqueue_event_broadcast("EVT_0002")["reason"], "broadcast_disabled")

    def test_candidate_creation_queues_without_server_audio(self):
        from backend.services.candidate_event_service import create_no_helmet_candidate_event
        self.heartbeat()
        with patch("backend.services.tts_service.speak_message") as speak:
            result = create_no_helmet_candidate_event(camera_id="CAM_001", confidence=0.9, enable_tts=True)
            self.assertTrue(result["broadcast"]["queued"])
            speak.assert_not_called()
        command = self.client.post("/api/field/commands/claim", headers=self.node).json()["command"]
        self.assertEqual(command["kind"], "broadcast")


if __name__ == "__main__":
    unittest.main()
