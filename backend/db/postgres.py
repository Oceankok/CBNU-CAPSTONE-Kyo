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
        if row is not None and row[0] != LATEST_VERSION:
            raise RuntimeError("Unsupported PostgreSQL schema version; explicit migration required")
        if row is None:
            if conn.execute("SELECT to_regclass('camera_info')").fetchone()[0] is not None:
                raise RuntimeError("Existing unversioned schema; explicit migration required")
            conn.execute((root / "postgres_schema.sql").read_text(encoding="utf-8"))
            conn.execute("INSERT INTO app_schema_version(singleton,version) VALUES(1,?)", (LATEST_VERSION,))
        if seed:
            conn.execute((root / "postgres_seed.sql").read_text(encoding="utf-8"))
            conn.execute("INSERT INTO event_retention(event_id) SELECT event_id FROM candidate_event ON CONFLICT DO NOTHING")
