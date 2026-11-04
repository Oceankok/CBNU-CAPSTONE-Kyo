CREATE TABLE IF NOT EXISTS camera_area_config (
    camera_id TEXT PRIMARY KEY REFERENCES camera_info(camera_id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS camera_work_area (
    area_id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL REFERENCES camera_info(camera_id) ON DELETE CASCADE,
    equipment_id TEXT NOT NULL REFERENCES equipment(equipment_id),
    name TEXT NOT NULL,
    points_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_area_camera ON camera_work_area(camera_id);
CREATE TABLE IF NOT EXISTS camera_snapshot (
    snapshot_id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL REFERENCES camera_info(camera_id),
    source_node_id TEXT NOT NULL REFERENCES field_node(node_id),
    capture_start_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','ready','failed','expired')),
    worker_node_id TEXT REFERENCES field_node(node_id),
    lease_hash TEXT,
    lease_until DOUBLE PRECISION,
    expires_at DOUBLE PRECISION NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    error_code TEXT,
    result_sha256 TEXT,
    local_cleanup_status TEXT NOT NULL DEFAULT 'not_required'
        CHECK(local_cleanup_status IN ('not_required','pending','confirmed'))
);
CREATE INDEX IF NOT EXISTS idx_camera_snapshot_queue ON camera_snapshot(status,created_at);
