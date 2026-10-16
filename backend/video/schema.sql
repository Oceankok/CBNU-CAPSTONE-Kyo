CREATE TABLE IF NOT EXISTS video_stream (
    stream_id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL REFERENCES camera_info(camera_id),
    node_id TEXT NOT NULL REFERENCES field_node(node_id),
    status TEXT NOT NULL CHECK(status IN ('active','closed','expired')),
    created_at DOUBLE PRECISION NOT NULL,
    last_seen_at DOUBLE PRECISION NOT NULL,
    last_sequence INTEGER NOT NULL DEFAULT -1,
    sequence_gap_count INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS video_stream_active_camera ON video_stream(camera_id) WHERE status='active';
CREATE TABLE IF NOT EXISTS video_chunk (
    chunk_id TEXT PRIMARY KEY,
    stream_id TEXT NOT NULL REFERENCES video_stream(stream_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK(sequence>=0),
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    capture_start_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    received_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    UNIQUE(stream_id,sequence)
);
CREATE INDEX IF NOT EXISTS video_chunk_expiry ON video_chunk(expires_at);
