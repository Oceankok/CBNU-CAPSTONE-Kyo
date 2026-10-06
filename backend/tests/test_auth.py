import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import jwt

from backend.auth.service import secret
from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login, ready_media


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.context = isolated_api()
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def test_contract_password_and_roles(self):
        bad = self.client.post("/api/auth/login", json={"user_id": "admin02", "password": "bad"})
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(bad.json()["detail"], "아이디 또는 비밀번호가 올바르지 않습니다.")
        missing = self.client.post("/api/auth/login", json={"user_id": "missing", "password": "bad"})
        self.assertEqual(missing.json(), bad.json())
        headers = login(self.client)
        self.assertEqual(self.client.get("/api/events").status_code, 401)
        self.assertEqual(self.client.get("/api/events", headers=headers).status_code, 200)
        worker = login(self.client, "worker01")
        self.assertEqual(self.client.get("/api/auth/me", headers=worker).json()["role"], "worker")
        for url in ["/api/events", "/api/stats", "/api/recommendations", "/api/broadcast/settings"]:
            self.assertEqual(self.client.get(url, headers=worker).status_code, 403)
        self.assertEqual(self.client.post("/api/stats/generate", headers=worker).status_code, 403)

    def test_reviewer_is_authenticated_user(self):
        headers = login(self.client)
        body = {"reviewer_id": "forged-admin", "review_result": "hold", "review_reason_code": "hold_unclear"}
        response = self.client.post("/api/events/EVT_0001/review", headers=headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["review"]["reviewer_id"], "admin02")
        body["review_result"] = "confirmed"
        with ready_media():
            response = self.client.put("/api/events/EVT_0001/review", headers=headers, json=body)
        self.assertEqual(response.json()["review"]["reviewer_id"], "admin02")

    def test_expired_disabled_and_logout(self):
        headers = login(self.client)
        claims = {"sub": "admin02", "ver": 0, "iss": "ppe-server", "aud": "ppe-api",
                  "iat": datetime.now(timezone.utc)-timedelta(hours=2),
                  "exp": datetime.now(timezone.utc)-timedelta(hours=1)}
        expired = jwt.encode(claims, secret(), algorithm="HS256")
        self.assertEqual(self.client.get("/api/auth/me", headers={"Authorization": "Bearer " + expired}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).status_code, 401)
        headers = login(self.client)
        with get_connection() as conn:
            conn.execute("UPDATE app_user SET is_active=0 WHERE user_id='admin02'")
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).status_code, 401)

    def test_media_cookie_roles_range_and_revocation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "clip.mp4").write_bytes(b"0123456789")
            # /storage only serves files registered as redacted, ready event media (#108)
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,"
                    "redaction_status,created_at) VALUES('M_TEST','EVT_0001','CAM_001','video','clip',?,"
                    "'ready','complete',datetime('now'))",
                    (f"{root.name}/clip.mp4",),
                )
            with patch("backend.api.main._STORAGE_DIR", root):
                self.assertEqual(self.client.get("/storage/clip.mp4").status_code, 401)
                login(self.client, "worker01")
                self.assertEqual(self.client.get("/storage/clip.mp4").status_code, 403)
                headers = login(self.client)
                response = self.client.get("/storage/clip.mp4", headers={"Range": "bytes=0-3"})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, b"0123")
                cookie = self.client.cookies.get("ppe_media")
                self.assertEqual(self.client.get("/api/auth/me", headers={"Authorization": "Bearer " + cookie}).status_code, 401)
                self.client.post("/api/auth/logout", headers=headers)
                self.assertEqual(self.client.get("/storage/clip.mp4").status_code, 401)


if __name__ == "__main__":
    unittest.main()
