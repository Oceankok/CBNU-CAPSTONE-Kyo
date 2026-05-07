CREATE TABLE IF NOT EXISTS camera_info (
    camera_id TEXT PRIMARY KEY,
    camera_location TEXT,
    zone_name TEXT,
    process_type TEXT,
    install_date TEXT,
    camera_angle_type TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS candidate_event (
    event_id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL,
    tracking_id TEXT,
    ppe_type TEXT NOT NULL,
    timestamp_start TEXT,
    timestamp_end TEXT,
    duration_sec INTEGER,
    frame_sample_count INTEGER,
    thumbnail_path TEXT,
    video_clip_path TEXT,
    ai_confidence REAL,
    person_detected INTEGER,
    ppe_detected INTEGER,
    model_version TEXT,
    event_status TEXT DEFAULT 'pending',
    FOREIGN KEY (camera_id) REFERENCES camera_info(camera_id)
);

CREATE TABLE IF NOT EXISTS event_review (
    review_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    reviewer_id TEXT,
    review_result TEXT,
    review_reason_code TEXT,
    review_time TEXT,
    review_comment TEXT,
    confirmed_violation INTEGER,
    second_review_needed INTEGER,
    FOREIGN KEY (event_id) REFERENCES candidate_event(event_id) ON DELETE CASCADE
);