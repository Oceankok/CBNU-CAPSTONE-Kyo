import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient


@contextmanager
def isolated_api():
    with TemporaryDirectory(prefix="ppe-test-") as directory:
        with patch.dict(os.environ, {"PPE_DB_PATH": str(Path(directory) / "test.db"),
                                    "PPE_JWT_SECRET": "test-only-key-with-at-least-32-bytes"}):
            from backend.db.init_db import init_database
            from backend.auth.service import create_user
            from backend.api.main import app
            init_database()
            create_user("admin02", "test-password", "관리자", "admin")
            create_user("worker01", "test-password", "작업자", "worker")
            with TestClient(app) as client:
                yield client


def login(client, user="admin02"):
    response = client.post("/api/auth/login", json={"user_id": user, "password": "test-password"})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}
