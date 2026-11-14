"""Versioned PostgreSQL baseline; SQLite historical migrations remain separate."""
from pathlib import Path

from backend.db.connection import connect, begin_write
from backend.db.migrations import LATEST_VERSION


def initialize(*, seed=False):
    root = Path(__file__).resolve().parent
    with connect() as conn:
        begin_write(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS app_schema_version (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL)""")
        row = conn.execute("SELECT version FROM app_schema_version WHERE singleton=1").fetchone()
        if row is not None and row[0] not in (7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, LATEST_VERSION):
            raise RuntimeError("Unsupported PostgreSQL schema version; explicit migration required")
        if row is not None and row[0] >= 33 and conn.execute(
            "SELECT to_regclass('auto_decision_settings')").fetchone()[0] is None:
            raise RuntimeError('Incompatible schema33 lineage: explicit reconciliation required')
        if row is None:
            if conn.execute("SELECT to_regclass('camera_info')").fetchone()[0] is not None:
                raise RuntimeError("Existing unversioned schema; explicit migration required")
            conn.execute((root / "postgres_schema.sql").read_text(encoding="utf-8"))
            conn.execute("INSERT INTO app_schema_version(singleton,version) VALUES(1,?)", (LATEST_VERSION,))
        if row is None or row[0] == 7:
            conn.execute('ALTER TABLE event_media ADD COLUMN IF NOT EXISTS processing_generation INTEGER NOT NULL DEFAULT 0')
            conn.execute((root.parent / 'processing' / 'schema.sql').read_text(encoding='utf-8'))
        if row is None or row[0] in (7, 8):
            conn.execute((root.parent / 'processing' / 'inference_schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
        if row is None or row[0] in (7, 8, 9):
            conn.execute((root.parent / 'video' / 'schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
        if row is None or row[0] < 11:
            conn.execute((root.parent / 'consents' / 'schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
        if row is None or row[0] < 12:
            conn.execute((root.parent / 'education' / 'schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
        if row is None or row[0] < 13:
            conn.execute("ALTER TABLE event_clip_request ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'clip' CHECK(role IN ('clip','reference'))")
            conn.execute((root.parent / 'camera_groups' / 'schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
        if row is None or row[0] < 14:
            conn.execute((root.parent / 'event_merge' / 'schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
        if row is None or row[0] < 15:
            conn.execute((root.parent / 'processing' / 'ai_schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
        if row is None or row[0] < 16:
            conn.execute((root.parent / 'processing' / 'inference_sessions.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
        if row is None or row[0] < 17:
            conn.execute((root.parent / 'work_areas' / 'schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
        if row is None or row[0] < 18:
            conn.execute("""CREATE TABLE IF NOT EXISTS processing_review_only_event (
                event_id TEXT PRIMARY KEY REFERENCES candidate_event(event_id) ON DELETE CASCADE,
                review_only INTEGER NOT NULL CHECK (review_only IN (0, 1))
            )""")
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
        if row is None or row[0] < 19:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_candidate_event_status_time ON candidate_event(event_status,timestamp_start,event_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_candidate_event_ppe_time ON candidate_event(ppe_type,timestamp_start,event_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_camera_info_zone ON camera_info(zone_name,camera_id)")
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
        if row is None or row[0] < 20:
            conn.execute("ALTER TABLE event_media ADD COLUMN IF NOT EXISTS verification_json TEXT NOT NULL DEFAULT ''")
            conn.execute('ALTER TABLE event_media ADD COLUMN IF NOT EXISTS source_deleted_at DOUBLE PRECISION')
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
        if row is None or row[0] < 21:
            from backend.db.event_zones import migrate_event_zones
            migrate_event_zones(conn, postgres=True)
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
        if seed:
            conn.execute((root / "postgres_seed.sql").read_text(encoding="utf-8"))
            if row is None:
                conn.execute((root / "postgres_education_seed.sql").read_text(encoding="utf-8"))
            conn.execute("INSERT INTO event_retention(event_id) SELECT event_id FROM candidate_event ON CONFLICT DO NOTHING")
        # Seeded summary counts must also be reconciled on the first v22
        # upgrade; a current-version reinitialization never repeats the reset.
        if row is None or row[0] < 22:
            from backend.db.false_positives import rebuild_false_positive_aggregates
            rebuild_false_positive_aggregates(conn)
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))

        if row is None or row[0] < 23:
            conn.execute((root.parent / 'processing' / 'lifecycle_schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))

        if row is None or row[0] < 24:
            conn.execute((root.parent / 'processing' / 'capture_schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))

        if row is None or row[0] < 25:
            conn.execute((root.parent / 'processing' / 'receipt_schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))

        if row is None or row[0] < 26:
            conn.execute((root.parent / 'event_relations' / 'schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))

        if row is None or row[0] < 27:
            conn.execute((root.parent / 'auto_decision' / 'shadow_schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))

        if row is None or row[0] < 28:
            conn.execute((root.parent / 'event_relations' / 'persistent_schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))

        if row is None or row[0] < 29:
            conn.execute('ALTER TABLE zone_rule ADD COLUMN IF NOT EXISTS risk_level INTEGER CHECK (risk_level BETWEEN 1 AND 5)')
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))

        if row is None or row[0] < 30:
            conn.execute('ALTER TABLE education_recommendation ADD COLUMN IF NOT EXISTS score_version TEXT')
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))

        if row is None or row[0] < 31:
            from backend.capture_context.schema import migrate
            migrate(conn, postgres=True)
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))

        if row is None or row[0] < 32:
            conn.execute((root.parent / 'education_bundles' / 'schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))

        if row is None or row[0] < 33:
            conn.execute((root.parent / 'auto_decision' / 'settings_schema.sql').read_text(encoding='utf-8'))
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,))
