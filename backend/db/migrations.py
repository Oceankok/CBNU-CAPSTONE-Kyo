"""Small, sequential SQLite migrations for databases created by older releases."""

import sqlite3
import uuid
from datetime import datetime, timezone


LATEST_VERSION = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Upgrade an initialized database without dropping existing application data."""
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version > LATEST_VERSION:
        raise RuntimeError(
            f"Database version {version} is newer than this application supports "
            f"(latest {LATEST_VERSION})."
        )

    if version < 1:
        conn.execute("BEGIN IMMEDIATE")
        try:
            now = _now()
            legacy = conn.execute(
                "SELECT event_id, camera_id, thumbnail_path, video_clip_path "
                "FROM candidate_event"
            ).fetchall()
            for row in legacy:
                paths = (
                    ("image", "thumbnail", row["thumbnail_path"]),
                    ("video", "clip", row["video_clip_path"]),
                )
                for kind, role, path in paths:
                    if not path:
                        continue
                    exists = conn.execute(
                        "SELECT 1 FROM event_media WHERE event_id=? AND role=? AND storage_path=?",
                        (row["event_id"], role, path),
                    ).fetchone()
                    if exists:
                        continue
                    conn.execute(
                        """INSERT INTO event_media (
                               media_id,event_id,camera_id,kind,role,storage_path,
                               status,redaction_status,created_at
                               ) VALUES (?,?,?,?,?,?,'registered','legacy_unverified',?)""",
                        (str(uuid.uuid4()), row["event_id"], row["camera_id"], kind, role, path, now),
                    )
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if version < 2:
        # Version 1's CHECK constraint cannot be expanded using ADD COLUMN.
        conn.execute("BEGIN IMMEDIATE")
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(event_media)")}
            if "processing_started_at" not in columns:
                conn.execute("ALTER TABLE event_media RENAME TO event_media_v1")
                conn.execute("""CREATE TABLE event_media (
                    media_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES candidate_event(event_id) ON DELETE CASCADE,
                    camera_id TEXT NOT NULL REFERENCES camera_info(camera_id),
                    kind TEXT NOT NULL CHECK(kind IN ('image','video')),
                    role TEXT NOT NULL CHECK(role IN ('thumbnail','clip','reference')),
                    storage_path TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'registered'
                        CHECK(status IN ('registered','processing','ready','failed','missing','delete_pending','deleted')),
                    redaction_status TEXT NOT NULL DEFAULT 'unprocessed'
                        CHECK(redaction_status IN ('unprocessed','complete','failed','legacy_unverified')),
                    capture_start_at TEXT, capture_end_at TEXT,
                    source_node_id TEXT REFERENCES field_node(node_id), request_id TEXT,
                    created_at TEXT NOT NULL, deleted_at TEXT, error_code TEXT,
                    processing_started_at REAL, faces_detected INTEGER, processed_frames INTEGER
                )""")
                conn.execute("""INSERT INTO event_media (
                    media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,
                    capture_start_at,capture_end_at,source_node_id,request_id,created_at,deleted_at)
                    SELECT media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,
                    capture_start_at,capture_end_at,source_node_id,request_id,created_at,deleted_at
                    FROM event_media_v1""")
                conn.execute("DROP TABLE event_media_v1")
                conn.execute("CREATE INDEX idx_event_media_event ON event_media(event_id,status)")
                conn.execute("CREATE INDEX idx_event_media_camera_capture ON event_media(camera_id,capture_start_at)")
                conn.execute("CREATE UNIQUE INDEX idx_event_media_request ON event_media(request_id) WHERE request_id IS NOT NULL")
            conn.execute("INSERT OR IGNORE INTO event_retention(event_id) SELECT event_id FROM candidate_event")
            conn.execute("PRAGMA user_version = 2")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if version < 3:
        conn.execute("BEGIN IMMEDIATE")
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(event_media)")}
            for name, sql_type in (("redaction_mode", "TEXT"), ("inference_backend", "TEXT"), ("processing_seconds", "REAL")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE event_media ADD COLUMN {name} {sql_type}")
            # Historical outputs have no trustworthy mode/timing information.
            conn.execute("PRAGMA user_version = 3")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
