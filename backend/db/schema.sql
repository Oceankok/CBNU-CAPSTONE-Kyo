-- 카메라 정보 테이블
CREATE TABLE IF NOT EXISTS camera_info (
    camera_id TEXT PRIMARY KEY,
    camera_location TEXT,
    zone_name TEXT,
    process_type TEXT,
    install_date TEXT,
    camera_angle_type TEXT,
    status TEXT
);

-- 후보 이벤트 테이블
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

-- An event can contain its trigger camera media and reference media from peers.
-- Legacy path columns remain temporarily for API/UI compatibility.
CREATE TABLE IF NOT EXISTS event_media (
    media_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('image','video')),
    role TEXT NOT NULL CHECK(role IN ('thumbnail','clip','reference')),
    storage_path TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    source_expires_at REAL,
    status TEXT NOT NULL DEFAULT 'registered'
        CHECK(status IN ('registered','processing','ready','failed','missing','delete_pending','deleted')),
    redaction_status TEXT NOT NULL DEFAULT 'unprocessed'
        CHECK(redaction_status IN ('unprocessed','complete','failed','legacy_unverified')),
    capture_start_at TEXT,
    capture_end_at TEXT,
    source_node_id TEXT,
    request_id TEXT,
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    error_code TEXT,
    processing_started_at REAL,
    faces_detected INTEGER,
    processed_frames INTEGER,
    redaction_mode TEXT,
    inference_backend TEXT,
    processing_seconds REAL,
    FOREIGN KEY (event_id) REFERENCES candidate_event(event_id) ON DELETE CASCADE,
    FOREIGN KEY (camera_id) REFERENCES camera_info(camera_id),
    FOREIGN KEY (source_node_id) REFERENCES field_node(node_id)
);
CREATE INDEX IF NOT EXISTS idx_event_media_event ON event_media(event_id,status);
CREATE INDEX IF NOT EXISTS idx_event_media_camera_capture ON event_media(camera_id,capture_start_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_event_media_request ON event_media(request_id) WHERE request_id IS NOT NULL;

-- Delivery state is separate from redaction state; pending is not permanent loss.
CREATE TABLE IF NOT EXISTS event_clip_request (
    request_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES candidate_event(event_id) ON DELETE CASCADE,
    camera_id TEXT NOT NULL REFERENCES camera_info(camera_id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','received','failed','unavailable')),
    error_code TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clip_request_event ON event_clip_request(event_id,status);

CREATE TABLE IF NOT EXISTS event_retention (
    event_id TEXT PRIMARY KEY REFERENCES candidate_event(event_id) ON DELETE CASCADE,
    decision TEXT NOT NULL DEFAULT 'pending' CHECK(decision IN ('pending','retain','delete')),
    consent_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(consent_confirmed IN (0,1)),
    consent_reference TEXT,
    decided_by TEXT,
    decided_at TEXT,
    retain_until REAL,
    deleted_at TEXT,
    version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS event_retention_history (
    history_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES candidate_event(event_id) ON DELETE CASCADE,
    decision TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    consent_reference TEXT
);

-- 이벤트 리뷰 테이블
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

-- Prior decisions are retained when a hold/second review updates event_review.
CREATE TABLE IF NOT EXISTS event_review_history (
    history_id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    reviewer_id TEXT,
    review_result TEXT NOT NULL,
    review_reason_code TEXT,
    review_time TEXT,
    review_comment TEXT,
    confirmed_violation INTEGER,
    second_review_needed INTEGER,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES candidate_event(event_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_event_review_history_event ON event_review_history(event_id,recorded_at);

-- 분기별 요약 통계
CREATE TABLE IF NOT EXISTS quarterly_summary (
    quarter TEXT PRIMARY KEY,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    confirmed_count INTEGER NOT NULL DEFAULT 0,
    false_positive_count INTEGER NOT NULL DEFAULT 0,
    hold_count INTEGER NOT NULL DEFAULT 0,
    unreviewable_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- PPE 유형별 분기 통계
CREATE TABLE IF NOT EXISTS quarterly_ppe_stats (
    stat_id TEXT PRIMARY KEY,
    quarter TEXT NOT NULL,
    ppe_type TEXT NOT NULL,
    confirmed_count INTEGER NOT NULL DEFAULT 0,
    priority_score REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (quarter) REFERENCES quarterly_summary(quarter) ON DELETE CASCADE
);

-- 구역별 분기 통계
CREATE TABLE IF NOT EXISTS quarterly_zone_stats (
    stat_id TEXT PRIMARY KEY,
    quarter TEXT NOT NULL,
    zone_name TEXT NOT NULL,
    confirmed_count INTEGER NOT NULL DEFAULT 0,
    priority_score REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (quarter) REFERENCES quarterly_summary(quarter) ON DELETE CASCADE
);

-- 분기별 PPE 유형 추이
CREATE TABLE IF NOT EXISTS quarterly_trend_stats (
    trend_id TEXT PRIMARY KEY,
    target_quarter TEXT NOT NULL,
    quarter TEXT NOT NULL,
    helmet INTEGER NOT NULL DEFAULT 0,
    vest INTEGER NOT NULL DEFAULT 0
);

-- 교육 추천 결과
CREATE TABLE IF NOT EXISTS education_recommendation (
    recommendation_id TEXT PRIMARY KEY,
    quarter TEXT NOT NULL,
    recommendation_rank INTEGER NOT NULL,
    ppe_type TEXT NOT NULL,
    zone_name TEXT NOT NULL,
    education_topic TEXT NOT NULL,
    priority_score REAL NOT NULL DEFAULT 0,
    confirmed_count INTEGER NOT NULL DEFAULT 0,
    repeat_weeks INTEGER NOT NULL DEFAULT 0,
    zone_concentration REAL NOT NULL DEFAULT 0,
    process_risk_weight REAL NOT NULL DEFAULT 1.0,
    generated_at TEXT NOT NULL,
    FOREIGN KEY (quarter) REFERENCES quarterly_summary(quarter) ON DELETE CASCADE
);

-- 오탐 이벤트 비식별 집계
CREATE TABLE IF NOT EXISTS false_positive_aggregate (
    aggregate_id TEXT PRIMARY KEY,
    quarter TEXT NOT NULL,
    zone_name TEXT NOT NULL,
    ppe_type TEXT NOT NULL,
    false_positive_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- 경고 방송 기본 설정
CREATE TABLE IF NOT EXISTS broadcast_setting (
    setting_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    default_language TEXT NOT NULL DEFAULT 'ko',
    cooldown_sec INTEGER NOT NULL DEFAULT 30,
    updated_at TEXT NOT NULL
);

-- PPE 유형/구역/언어별 경고 방송 메시지 템플릿
CREATE TABLE IF NOT EXISTS broadcast_message_template (
    template_id TEXT PRIMARY KEY,
    setting_id TEXT NOT NULL,
    ppe_type TEXT NOT NULL,
    zone_name TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (setting_id) REFERENCES broadcast_setting(setting_id) ON DELETE CASCADE
);
-- Dashboard users are not linked to detected people.
CREATE TABLE IF NOT EXISTS app_user (
    user_id TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','worker')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    zone_name TEXT,
    auth_version INTEGER NOT NULL DEFAULT 0
);

-- Zone-wide baseline PPE and worker safety guidance.
-- Equipment-specific additions are stored separately when equipment registration is implemented.
-- Dashboard users are not linked to detections.
CREATE TABLE IF NOT EXISTS zone_rule (
    zone_name TEXT PRIMARY KEY,
    required_ppe TEXT NOT NULL DEFAULT '[]',
    rules TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT NOT NULL DEFAULT 'system'
);

CREATE TABLE IF NOT EXISTS field_node (
    node_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1,
    last_seen_at REAL,
    language TEXT,
    voice_id TEXT,
    voices_json TEXT NOT NULL DEFAULT '[]',
    voices_checked_at REAL
);
CREATE TABLE IF NOT EXISTS camera_node (
    camera_id TEXT PRIMARY KEY REFERENCES camera_info(camera_id),
    source_node_id TEXT NOT NULL REFERENCES field_node(node_id),
    output_node_id TEXT NOT NULL REFERENCES field_node(node_id)
);
CREATE TABLE IF NOT EXISTS field_command (
    command_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES field_node(node_id),
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    event_id TEXT REFERENCES candidate_event(event_id) ON DELETE SET NULL,
    cooldown_key TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    claimed_at REAL,
    finished_at REAL,
    result TEXT
);
CREATE INDEX IF NOT EXISTS idx_field_command_poll ON field_command(node_id,status,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_broadcast_event ON field_command(node_id,event_id) WHERE kind='broadcast' AND event_id IS NOT NULL;
