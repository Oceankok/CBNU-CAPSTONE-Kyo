CREATE TABLE IF NOT EXISTS processing_inference_load (
    node_id TEXT PRIMARY KEY REFERENCES processing_worker(node_id),
    busy_until DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at DOUBLE PRECISION NOT NULL
);
