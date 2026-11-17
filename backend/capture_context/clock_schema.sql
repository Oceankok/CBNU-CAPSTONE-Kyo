CREATE TABLE IF NOT EXISTS capture_clock_stream (
 stream_id TEXT PRIMARY KEY REFERENCES capture_context_stream(stream_id),
 run_id TEXT NOT NULL, epoch TEXT NOT NULL, version INTEGER NOT NULL CHECK(version=1)
);
CREATE TABLE IF NOT EXISTS capture_clock_sample (
 proof_id TEXT PRIMARY KEY, stream_id TEXT NOT NULL REFERENCES capture_clock_stream(stream_id),
 sample_sequence INTEGER NOT NULL CHECK(sample_sequence>0),
 server_sample DOUBLE PRECISION NOT NULL, server_epoch TEXT NOT NULL, server_boot_id TEXT NOT NULL,
 policy TEXT NOT NULL,
 source_sent_at DOUBLE PRECISION NOT NULL, monotonic_sent DOUBLE PRECISION NOT NULL,
 publication TEXT, proof TEXT,
 UNIQUE(stream_id,sample_sequence)
);
CREATE TABLE IF NOT EXISTS capture_clock_chunk (
 stream_id TEXT NOT NULL REFERENCES capture_clock_stream(stream_id), sequence INTEGER NOT NULL,
 proof_id TEXT NOT NULL REFERENCES capture_clock_sample(proof_id),
 PRIMARY KEY(stream_id,sequence)
);
