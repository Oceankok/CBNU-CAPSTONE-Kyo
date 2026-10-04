"""Small, sequential SQLite migrations for databases created by older releases."""

import sqlite3
import uuid
from datetime import datetime, timezone


LATEST_VERSION = 1


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
