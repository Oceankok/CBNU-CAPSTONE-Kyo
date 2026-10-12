import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import cv2
import numpy as np

from backend.db.event_repository import get_connection
from backend.equipment import provider, recognition
from backend.tests.support import isolated_api, login


class EquipmentRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.context = isolated_api()
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.directory = TemporaryDirectory(prefix="equipment-evidence-test-")
        self.addCleanup(self.directory.cleanup)
        self.root_patch = patch.object(recognition, "EVIDENCE_ROOT", Path(self.directory.name))
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.admin = login(self.client)
        self.image = cv2.imencode(".png", np.zeros((16, 16, 3), dtype=np.uint8))[1].tobytes()

    def upload(self, headers=None, content=None, mime="image/png"):
        return self.client.post("/api/equipment/recognitions?camera_id=CAM_001",
                                headers={**(headers or self.admin), "Content-Type": mime},
                                content=self.image if content is None else content)

    def analyzed(self):
        adapter = Mock()
        adapter.analyze.return_value = provider.ProviderResult(candidates=[provider.Candidate(label="프레스", equipment_type="press")])
        def redact(frame, output, mode):
            output.write_bytes(b"synthetic-redacted-jpeg")
        with patch.object(provider, "get_provider", return_value=adapter), patch.object(recognition, "redact_frame", side_effect=redact):
            response = self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        adapter.analyze.assert_called_once_with(b"synthetic-redacted-jpeg", timeout_seconds=30)
        self.assertNotEqual(adapter.analyze.call_args.args[0], self.image)
        return response.json()

    def test_unconfigured_provider_records_failure_without_redaction(self):
        with patch.object(provider, "get_provider", return_value=None), patch.object(recognition, "redact_frame") as redact:
            response = self.upload()
        self.assertEqual(response.status_code, 201)
        row = response.json()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error_code"], "provider_not_configured")
        self.assertIsNone(row["evidence_url"])
        self.assertEqual(row["candidates"], [])
        redact.assert_not_called()
        self.assertEqual(self.client.get("/api/equipment/recognitions", headers=self.admin).json()["items"][0]["recognition_id"], row["recognition_id"])

    def test_invalid_input_and_role_do_not_call_provider(self):
        with patch.object(provider, "get_provider") as resolve:
            self.assertEqual(self.upload(content=b"corrupt").status_code, 422)
            self.assertEqual(self.upload(mime="video/mp4").status_code, 415)
            self.assertEqual(self.upload(content=b"x" * (recognition.MAX_UPLOAD + 1)).status_code, 413)
            self.assertEqual(self.upload(headers=login(self.client, "worker01")).status_code, 403)
            self.assertEqual(self.client.post("/api/equipment/recognitions", headers={"Content-Type":"image/png"}, content=self.image).status_code, 401)
            resolve.assert_not_called()

    def test_redaction_failure_prevents_external_call(self):
        adapter = Mock()
        with patch.object(provider, "get_provider", return_value=adapter), patch.object(recognition, "redact_frame", side_effect=RuntimeError("private internal error")):
            row = self.upload().json()
        self.assertEqual(row["error_code"], "redaction_failed")
        self.assertNotIn("private internal error", str(row))
        adapter.analyze.assert_not_called()

    def test_private_evidence_and_atomic_confirmation(self):
        row = self.analyzed()
        url = "/api/equipment/recognitions/" + row["recognition_id"]
        self.assertEqual(self.client.get(url + "/image").status_code, 401)
        self.assertEqual(self.client.get(url + "/image", headers=login(self.client,"worker01")).status_code, 403)
        self.assertEqual(self.client.get(url + "/image", headers=self.admin).content, b"synthetic-redacted-jpeg")
        body = {"candidate_id":"wrong", "name":"확정 장비", "zone_name":"프레스 구역", "required_ppe":["vest"]}
        self.assertEqual(self.client.post(url + "/confirm", headers=self.admin, json=body).status_code, 422)
        self.assertEqual(self.client.get("/api/equipment", headers=self.admin).json()["items"], [])
        body["candidate_id"] = row["candidates"][0]["candidate_id"]
        response = self.client.post(url + "/confirm", headers=self.admin, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(url,headers=self.admin).json()["equipment_id"], response.json()["equipment_id"])
        self.assertEqual(self.client.post(url + "/confirm", headers=self.admin, json=body).status_code, 409)
        self.assertEqual(self.client.post(url + "/dismiss", headers=self.admin).status_code, 409)

    def test_expiry_hides_evidence_blocks_confirmation_and_retries_cleanup(self):
        row = self.analyzed()
        url = "/api/equipment/recognitions/" + row["recognition_id"]
        with get_connection() as conn:
            stored = recognition.find(conn, row["recognition_id"])
            file = recognition.evidence_path(stored["evidence_path"])
            conn.execute("UPDATE equipment_recognition SET evidence_expires_at=? WHERE recognition_id=?", (time.time()-1,row["recognition_id"]))
        self.assertIsNone(self.client.get(url,headers=self.admin).json()["evidence_url"])
        self.assertEqual(self.client.get(url+"/image",headers=self.admin).status_code,404)
        body={"name":"장비", "zone_name":"프레스 구역", "required_ppe":[]}
        self.assertEqual(self.client.post(url+"/confirm",headers=self.admin,json=body).json()["detail"],"recognition_evidence_expired")
        with patch.object(Path, "unlink", side_effect=PermissionError("locked")):
            self.assertEqual(recognition.cleanup(),0)
        self.assertTrue(file.exists())
        self.assertEqual(recognition.cleanup(),1)
        self.assertFalse(file.exists())
        self.assertEqual(self.client.get(url,headers=self.admin).json()["status"],"analyzed")

    def test_dismiss_is_idempotent_and_provider_response_is_validated(self):
        adapter=Mock()
        adapter.analyze.return_value={"candidates":[{"label":"device", "confidence":2}]}
        with patch.object(provider,"get_provider",return_value=adapter), patch.object(recognition,"redact_frame",side_effect=lambda frame,path,mode: path.write_bytes(b"redacted")):
            row=self.upload().json()
        self.assertEqual(row["error_code"],"provider_response_invalid")
        url="/api/equipment/recognitions/"+row["recognition_id"]
        for _ in range(2):
            self.assertEqual(self.client.post(url+"/dismiss",headers=self.admin).json()["status"],"dismissed")

    def test_v5_migration_preserves_equipment(self):
        from backend.db.migrations import apply_migrations, LATEST_VERSION
        self.client.post("/api/equipment",headers=self.admin,json={"name":"기존 장비","zone_name":"프레스 구역","required_ppe":[]}).raise_for_status()
        with get_connection() as conn:
            before=[dict(row) for row in conn.execute("SELECT * FROM equipment")]
            conn.execute("DROP TABLE equipment_recognition")
            conn.execute("PRAGMA user_version=5")
            conn.commit()
            apply_migrations(conn)
            apply_migrations(conn)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],LATEST_VERSION)
            self.assertEqual([dict(row) for row in conn.execute("SELECT * FROM equipment")],before)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM equipment_recognition").fetchone()[0],0)
