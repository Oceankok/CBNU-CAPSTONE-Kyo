import subprocess
import unittest
from unittest.mock import patch, Mock

from backend.tests.support import isolated_api, login
from field_agent.main import execute
from field_agent.languages import install_language


class VoiceManagementTests(unittest.TestCase):
    def test_scan_select_and_test_command(self):
        with isolated_api() as client:
            admin = login(client)
            registered = client.post("/api/nodes", headers=admin, json={"node_id": "voicepc", "name": "Voice PC"}).json()
            node = {"Authorization": "Bearer "+registered["token"]}
            client.post("/api/field/heartbeat", headers=node)
            client.post("/api/nodes/voicepc/voices/refresh", headers=admin).raise_for_status()
            command = client.post("/api/field/commands/claim", headers=node).json()["command"]
            inventory = {"voice_scan_ok": True, "voices": [{"id": "voice-ko", "name": "Korean", "languages": ["ko-KR"]}], "installed_languages": ["ko-KR"]}
            with patch("field_agent.main.scan_voices", return_value=inventory):
                result = execute(command)
            client.put(f'/api/field/commands/{command["command_id"]}/result', headers=node, json=result).raise_for_status()
            self.assertEqual(client.get("/api/nodes/voicepc/voices", headers=admin).json()["items"], inventory["voices"])
            self.assertEqual(client.put("/api/nodes/voicepc/voice", headers=admin, json={"language": "ko", "voice_id": "missing"}).status_code, 400)
            client.put("/api/nodes/voicepc/voice", headers=admin, json={"language": "ko", "voice_id": "voice-ko"}).raise_for_status()
            response = client.post("/api/nodes/voicepc/test-broadcast", headers=admin, json={})
            self.assertEqual(response.status_code, 202)
            worker = login(client, "worker01")
            self.assertEqual(client.post("/api/nodes/voicepc/languages/install", headers=worker, json={"language": "ko-KR"}).status_code, 403)
            self.assertEqual(client.post("/api/nodes/voicepc/languages/install", headers=admin, json={"language": "ko-KR;whoami"}).status_code, 400)

    def test_install_gate_permissions_and_failure(self):
        with patch("field_agent.languages.powershell") as run:
            self.assertEqual(install_language("ko-KR")["reason"], "installation_not_enabled")
            with patch("field_agent.languages.is_admin", return_value=False):
                self.assertEqual(install_language("ko-KR", True)["reason"], "administrator_required")
            run.assert_not_called()
        with patch("field_agent.languages.is_admin", return_value=True), patch("field_agent.languages.scan_voices", return_value={"voice_scan_ok": True, "voices": []}) as scan:
            with patch("field_agent.languages.powershell", return_value=Mock(returncode=0)):
                result = install_language("ko-KR", True)
                self.assertTrue(result["installed"])
                self.assertEqual(result["voices"], [])  # pack success is not voice availability
                scan.assert_called_once()
            with patch("field_agent.languages.powershell", side_effect=subprocess.TimeoutExpired("powershell", 1200)):
                result = install_language("ko-KR", True)
                self.assertFalse(result["installed"])
                self.assertIn("guidance", result)


if __name__ == "__main__":
    unittest.main()
