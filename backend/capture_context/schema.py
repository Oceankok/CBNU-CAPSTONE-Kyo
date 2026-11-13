from .registry import prepare_registry, POLICY


def migrate(conn, *, postgres=False):
    prepare_registry(conn, policy=POLICY)
    conn.execute('''CREATE TABLE IF NOT EXISTS capture_context_owner (
        context_id TEXT PRIMARY KEY REFERENCES capture_context_registry(context_id),
        node_id TEXT NOT NULL REFERENCES field_node(node_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS capture_context_stream (
        stream_id TEXT PRIMARY KEY,
        context_id TEXT NOT NULL REFERENCES capture_context_registry(context_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS event_capture_context (
        event_id TEXT PRIMARY KEY REFERENCES candidate_event(event_id) ON DELETE CASCADE,
        context_id TEXT NOT NULL REFERENCES capture_context_registry(context_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS capture_chunk_receipt (
        stream_id TEXT NOT NULL REFERENCES capture_context_stream(stream_id),
        sequence INTEGER NOT NULL, chunk_id TEXT NOT NULL, sha256 TEXT NOT NULL,
        capture_start_at TEXT NOT NULL, duration_ms INTEGER NOT NULL,
        PRIMARY KEY(stream_id,sequence))''')
    if postgres:
        conn.execute('ALTER TABLE event_zone_snapshot DROP CONSTRAINT IF EXISTS event_zone_snapshot_source_check')
        conn.execute("ALTER TABLE event_zone_snapshot ADD CONSTRAINT event_zone_snapshot_source_check CHECK(source IN ('migration_current','registered_current','capture_context'))")
    else:
        # Rebuild only this leaf snapshot table, retaining all attribution rows.
        conn.execute('''CREATE TABLE event_zone_snapshot_new (
            event_id TEXT PRIMARY KEY REFERENCES candidate_event(event_id) ON DELETE CASCADE,
            zone_name TEXT, captured_at TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('migration_current','registered_current','capture_context')))''')
        conn.execute('INSERT INTO event_zone_snapshot_new SELECT * FROM event_zone_snapshot')
        conn.execute('DROP TRIGGER IF EXISTS candidate_event_capture_zone')
        conn.execute('DROP TABLE event_zone_snapshot')
        conn.execute('ALTER TABLE event_zone_snapshot_new RENAME TO event_zone_snapshot')
        conn.execute('CREATE INDEX idx_event_zone_snapshot_zone ON event_zone_snapshot(zone_name,event_id)')

    from backend.db.event_zones import migrate_event_zones
    migrate_event_zones(conn, postgres=postgres)
