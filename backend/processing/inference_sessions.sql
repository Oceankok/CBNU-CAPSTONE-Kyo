CREATE TABLE IF NOT EXISTS processing_inference_session (
    node_id TEXT NOT NULL REFERENCES processing_worker(node_id),
    session_id TEXT NOT NULL,
    busy_until DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(node_id,session_id)
);
