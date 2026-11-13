CREATE TABLE IF NOT EXISTS processing_event_lifecycle (
    event_id TEXT PRIMARY KEY REFERENCES candidate_event(event_id) ON DELETE CASCADE,
    owner_node_id TEXT NOT NULL REFERENCES field_node(node_id),
    session_id TEXT NOT NULL,
    policy_key TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('open','closed','interrupted')),
    revision INTEGER NOT NULL DEFAULT 0,
    evidence_revision INTEGER NOT NULL DEFAULT 0,
    start_payload TEXT NOT NULL,
    observed_seconds DOUBLE PRECISION NOT NULL,
    gap_seconds DOUBLE PRECISION NOT NULL,
    gap_count INTEGER NOT NULL,
    final_segment_count INTEGER,
    close_reason TEXT,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS processing_event_operation (
    event_id TEXT NOT NULL REFERENCES processing_event_lifecycle(event_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(event_id,sequence)
);
CREATE TABLE IF NOT EXISTS processing_event_camera (
    event_id TEXT NOT NULL REFERENCES processing_event_lifecycle(event_id) ON DELETE CASCADE,
    camera_id TEXT NOT NULL REFERENCES camera_info(camera_id),
    role TEXT NOT NULL CHECK(role IN ('clip','reference')),
    PRIMARY KEY(event_id,camera_id)
);
CREATE TABLE IF NOT EXISTS processing_event_segment (
    event_id TEXT NOT NULL REFERENCES processing_event_lifecycle(event_id) ON DELETE CASCADE,
    camera_id TEXT NOT NULL REFERENCES camera_info(camera_id),
    sequence INTEGER NOT NULL,
    request_id TEXT NOT NULL UNIQUE REFERENCES event_clip_request(request_id) ON DELETE CASCADE,
    capture_start_at TEXT NOT NULL,
    capture_end_at TEXT NOT NULL,
    deadline_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(event_id,camera_id,sequence)
);
CREATE TABLE IF NOT EXISTS processing_segment_validation (
    media_id TEXT PRIMARY KEY REFERENCES event_media(media_id) ON DELETE CASCADE,
    generation INTEGER NOT NULL,
    frame_count INTEGER NOT NULL,
    fps DOUBLE PRECISION NOT NULL,
    duration_seconds DOUBLE PRECISION NOT NULL,
    result_sha256 TEXT NOT NULL
);
