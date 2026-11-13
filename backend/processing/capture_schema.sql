CREATE TABLE IF NOT EXISTS processing_camera_capture (
    event_id TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(event_id,camera_id,sequence),
    FOREIGN KEY(event_id,camera_id) REFERENCES processing_event_camera(event_id,camera_id) ON DELETE CASCADE
);
