import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi.testclient import TestClient


@contextmanager
def database_environment(directory):
    url = os.environ.get("PPE_TEST_POSTGRES_URL", "")
    if not url:
        with patch.dict(os.environ, {"PPE_DATABASE_URL": "", "PPE_DB_PATH": str(Path(directory) / "test.db")}):
            yield
        return
    if not urlsplit(url).path.removeprefix('/').startswith('kyo_test'):
        raise RuntimeError("PostgreSQL tests require a dedicated kyo_test database")
    import psycopg
    from psycopg import sql
    schema = 'test_' + uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    try:
        with patch.dict(os.environ, {"PPE_DATABASE_URL": url, "PPE_DB_SCHEMA": schema, "PPE_DB_SEED": "true"}):
            yield
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


@contextmanager
def isolated_api():
    with TemporaryDirectory(prefix="ppe-test-") as directory:
        with database_environment(directory), patch.dict(os.environ, {
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


@contextmanager
def ready_media(event_id="EVT_0001"):
    """Authentication/review tests use a synthetic published fixture, not a GPU model."""
    from backend.db.event_repository import get_connection
    from backend.media.paths import owned_path
    media_id = str(uuid4())
    key = f"storage/test-{media_id}.jpg"
    with TemporaryDirectory() as directory:
        file = Path(directory) / "redacted.jpg"
        file.write_bytes(b"synthetic-redacted-fixture")
        with get_connection() as conn:
            camera = conn.execute("SELECT camera_id FROM candidate_event WHERE event_id=?", (event_id,)).fetchone()[0]
            conn.execute("INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,created_at) VALUES(?,?,?,'image','reference',?,'ready','complete',CURRENT_TIMESTAMP)", (media_id,event_id,camera,key))
        try:
            with patch("backend.media.service.owned_path", side_effect=lambda path: file if path == key else owned_path(path)):
                yield media_id
        finally:
            with get_connection() as conn:
                conn.execute("DELETE FROM event_media WHERE media_id=?", (media_id,))
