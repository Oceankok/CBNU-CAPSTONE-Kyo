"""Immutable ingestion-time zones; legacy rows retain migration-time attribution."""
from datetime import datetime, timezone


def migrate_event_zones(conn, *, postgres=False):
    conn.execute('''CREATE TABLE IF NOT EXISTS event_zone_snapshot (
        event_id TEXT PRIMARY KEY REFERENCES candidate_event(event_id) ON DELETE CASCADE,
        zone_name TEXT,
        captured_at TEXT NOT NULL,
        source TEXT NOT NULL CHECK(source IN ('migration_current', 'registered_current'))
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_event_zone_snapshot_zone ON event_zone_snapshot(zone_name,event_id)')
    # The owner approved freezing current attribution, not reconstructing an
    # unknowable capture-time zone. Never overwrite an existing snapshot.
    conn.execute('''INSERT INTO event_zone_snapshot(event_id,zone_name,captured_at,source)
        SELECT e.event_id,c.zone_name,?,'migration_current'
        FROM candidate_event e LEFT JOIN camera_info c ON c.camera_id=e.camera_id
        WHERE 1=1 ON CONFLICT(event_id) DO NOTHING''',
        (datetime.now(timezone.utc).isoformat(timespec='seconds'),))
    if postgres:
        conn.execute('''CREATE OR REPLACE FUNCTION capture_event_zone() RETURNS trigger AS $$
            BEGIN
                INSERT INTO event_zone_snapshot(event_id,zone_name,captured_at,source)
                SELECT NEW.event_id,c.zone_name,CURRENT_TIMESTAMP::text,'registered_current'
                FROM camera_info c WHERE c.camera_id=NEW.camera_id;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql''')
        conn.execute('DROP TRIGGER IF EXISTS candidate_event_capture_zone ON candidate_event')
        conn.execute('''CREATE TRIGGER candidate_event_capture_zone AFTER INSERT ON candidate_event
            FOR EACH ROW EXECUTE FUNCTION capture_event_zone()''')
    else:
        conn.execute('''CREATE TRIGGER IF NOT EXISTS candidate_event_capture_zone
            AFTER INSERT ON candidate_event BEGIN
                INSERT INTO event_zone_snapshot(event_id,zone_name,captured_at,source)
                SELECT NEW.event_id,c.zone_name,strftime('%Y-%m-%dT%H:%M:%SZ','now'),'registered_current'
                FROM camera_info c WHERE c.camera_id=NEW.camera_id;
            END''')
