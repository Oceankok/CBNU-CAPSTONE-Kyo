CREATE TABLE IF NOT EXISTS capture_observation_stop (
 stream_id TEXT PRIMARY KEY REFERENCES capture_context_stream(stream_id),
 payload TEXT NOT NULL
);
