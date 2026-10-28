CREATE TABLE IF NOT EXISTS host_ai_worker (
    node_id TEXT NOT NULL REFERENCES processing_worker(node_id),
    kind TEXT NOT NULL CHECK(kind IN ('equipment','education')),
    provider TEXT NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(node_id,kind)
);
CREATE TABLE IF NOT EXISTS host_ai_job (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('equipment','education')),
    provider TEXT NOT NULL,
    input_json TEXT NOT NULL,
    result_json TEXT,
    error_code TEXT,
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
    worker_node_id TEXT REFERENCES processing_worker(node_id),
    lease_hash TEXT,
    deadline DOUBLE PRECISION NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    cleanup_pending INTEGER NOT NULL DEFAULT 0 CHECK(cleanup_pending IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_host_ai_queue ON host_ai_job(status,created_at);
