CREATE TABLE IF NOT EXISTS processing_worker (
    node_id TEXT PRIMARY KEY REFERENCES field_node(node_id),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
);
CREATE TABLE IF NOT EXISTS processing_job (
    job_id TEXT PRIMARY KEY,
    media_id TEXT NOT NULL REFERENCES event_media(media_id),
    generation INTEGER NOT NULL,
    parent_job_id TEXT REFERENCES processing_job(job_id),
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
    redaction_mode TEXT NOT NULL CHECK(redaction_mode IN ('enhanced','scrfd')),
    kind TEXT NOT NULL CHECK(kind IN ('image','video')),
    worker_node_id TEXT REFERENCES field_node(node_id),
    lease_token_hash TEXT,
    lease_until DOUBLE PRECISION,
    started_at DOUBLE PRECISION,
    finished_at DOUBLE PRECISION,
    error_code TEXT,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    local_cleanup_status TEXT NOT NULL DEFAULT 'not_required'
        CHECK(local_cleanup_status IN ('not_required','pending','confirmed')),
    result_sha256 TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_processing_job_active ON processing_job(media_id)
    WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS idx_processing_job_queue ON processing_job(status,created_at);
