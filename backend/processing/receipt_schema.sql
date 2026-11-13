CREATE TABLE IF NOT EXISTS processing_clip_receipt (
    request_id TEXT PRIMARY KEY REFERENCES event_clip_request(request_id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES processing_event_lifecycle(event_id) ON DELETE CASCADE,
    camera_id TEXT NOT NULL REFERENCES camera_info(camera_id),
    role TEXT NOT NULL CHECK(role IN ('clip','reference')),
    media_id TEXT NOT NULL UNIQUE REFERENCES event_media(media_id) ON DELETE CASCADE,
    source_sha256 TEXT NOT NULL,
    source_bytes BIGINT NOT NULL CHECK(source_bytes>0),
    accepted_at DOUBLE PRECISION NOT NULL
);
