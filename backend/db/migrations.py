"""Small, sequential SQLite migrations for databases created by older releases."""

import sqlite3
import uuid
from datetime import datetime, timezone


LATEST_VERSION = 11


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

    if version < 4:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS event_clip_request (
                request_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL REFERENCES candidate_event(event_id) ON DELETE CASCADE,
                camera_id TEXT NOT NULL REFERENCES camera_info(camera_id),
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','received','failed','unavailable')),
                error_code TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_clip_request_event ON event_clip_request(event_id,status)")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(event_media)")}
            for name, declaration in (("source_path", "TEXT NOT NULL DEFAULT ''"), ("source_expires_at", "REAL")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE event_media ADD COLUMN {name} {declaration}")
            if "unreviewable_count" not in {row[1] for row in conn.execute("PRAGMA table_info(quarterly_summary)")}:
                conn.execute("ALTER TABLE quarterly_summary ADD COLUMN unreviewable_count INTEGER NOT NULL DEFAULT 0")
            conn.execute("PRAGMA user_version = 4")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if version < 5:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS equipment (
                equipment_id TEXT PRIMARY KEY, name TEXT NOT NULL, equipment_type TEXT,
                zone_name TEXT NOT NULL REFERENCES zone_rule(zone_name),
                required_ppe TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_equipment_zone ON equipment(zone_name,status)")
            conn.execute("""CREATE TABLE IF NOT EXISTS camera_equipment_config (
                camera_id TEXT PRIMARY KEY REFERENCES camera_info(camera_id) ON DELETE CASCADE,
                updated_at TEXT NOT NULL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS camera_equipment (
                camera_id TEXT NOT NULL REFERENCES camera_info(camera_id) ON DELETE CASCADE,
                equipment_id TEXT NOT NULL REFERENCES equipment(equipment_id),
                sort_order INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(camera_id,equipment_id)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_camera_equipment_equipment ON camera_equipment(equipment_id)")
            conn.execute("PRAGMA user_version = 5")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if version < 6:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS equipment_recognition (
                recognition_id TEXT PRIMARY KEY,
                camera_id TEXT REFERENCES camera_info(camera_id),
                status TEXT NOT NULL CHECK(status IN ('analyzed','failed','confirmed','dismissed')),
                candidates_json TEXT NOT NULL DEFAULT '[]',
                evidence_path TEXT NOT NULL DEFAULT '', evidence_expires_at REAL,
                equipment_id TEXT REFERENCES equipment(equipment_id), error_code TEXT,
                created_at TEXT NOT NULL, created_by TEXT NOT NULL,
                decided_at TEXT, decided_by TEXT
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_equipment_recognition_status ON equipment_recognition(status,created_at)")
            conn.execute("PRAGMA user_version = 6")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if version < 7:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("""SELECT 1 FROM app_user u WHERE u.role='worker' AND u.zone_name IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM zone_rule z WHERE z.zone_name=u.zone_name) LIMIT 1""").fetchone():
                raise RuntimeError("Worker references an unknown zone; repair assignment before migration")
            conn.execute("""CREATE TABLE IF NOT EXISTS user_zone (
                user_id TEXT NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
                zone_name TEXT NOT NULL REFERENCES zone_rule(zone_name),
                PRIMARY KEY(user_id,zone_name)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_zone_zone ON user_zone(zone_name)")
            conn.execute("""INSERT OR IGNORE INTO user_zone(user_id,zone_name)
                SELECT u.user_id,u.zone_name FROM app_user u JOIN zone_rule z ON z.zone_name=u.zone_name
                WHERE u.role='worker'""")
            conn.execute("""UPDATE app_user SET zone_name=(SELECT MIN(zone_name) FROM user_zone
                WHERE user_zone.user_id=app_user.user_id)""")
            conn.execute("PRAGMA user_version = 7")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if version < 8:
        from pathlib import Path
        conn.execute("BEGIN IMMEDIATE")
        try:
            if 'processing_generation' not in {row[1] for row in conn.execute('PRAGMA table_info(event_media)')}:
                conn.execute('ALTER TABLE event_media ADD COLUMN processing_generation INTEGER NOT NULL DEFAULT 0')
            script = (Path(__file__).resolve().parents[1] / 'processing' / 'schema.sql').read_text(encoding='utf-8')
            for statement in script.split(';'):
                if statement.strip():
                    conn.execute(statement)
            conn.execute('PRAGMA user_version=8')
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if version < 9:
        from pathlib import Path
        conn.execute('BEGIN IMMEDIATE')
        try:
            script = (Path(__file__).resolve().parents[1] / 'processing' / 'inference_schema.sql').read_text(encoding='utf-8')
            for statement in script.split(';'):
                if statement.strip():
                    conn.execute(statement)
            conn.execute('PRAGMA user_version=9')
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if version < 10:
        from pathlib import Path
        conn.execute('BEGIN IMMEDIATE')
        try:
            script = (Path(__file__).resolve().parents[1] / 'video' / 'schema.sql').read_text(encoding='utf-8')
            for statement in script.split(';'):
                if statement.strip():
                    conn.execute(statement)
            conn.execute('PRAGMA user_version=10')
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if version < 11:
        from pathlib import Path
        conn.execute('BEGIN IMMEDIATE')
        try:
            script = (Path(__file__).resolve().parents[1] / 'consents' / 'schema.sql').read_text(encoding='utf-8')
            for statement in script.split(';'):
                if statement.strip():
                    conn.execute(statement)
            conn.execute('PRAGMA user_version=11')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
