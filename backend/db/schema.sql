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

-- 분기별 요약 통계
CREATE TABLE IF NOT EXISTS quarterly_summary (
    quarter TEXT PRIMARY KEY,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    confirmed_count INTEGER NOT NULL DEFAULT 0,
    false_positive_count INTEGER NOT NULL DEFAULT 0,
    hold_count INTEGER NOT NULL DEFAULT 0,
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