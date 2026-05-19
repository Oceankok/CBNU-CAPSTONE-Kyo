INSERT OR IGNORE INTO camera_info (
    camera_id,
    camera_location,
    zone_name,
    process_type,
    install_date,
    camera_angle_type,
    status
) VALUES
(
    'CAM_001',
    '1공장 동쪽 벽면',
    '프레스 구역',
    '유압 성형',
    '2026-04-28',
    'wide',
    'active'
),
(
    'CAM_002',
    '1공장 중앙 통로',
    '자재 이동 구역',
    '자재 운반',
    '2026-04-28',
    'front',
    'active'
);

INSERT OR IGNORE INTO candidate_event (
    event_id,
    camera_id,
    tracking_id,
    ppe_type,
    timestamp_start,
    timestamp_end,
    duration_sec,
    frame_sample_count,
    thumbnail_path,
    video_clip_path,
    ai_confidence,
    person_detected,
    ppe_detected,
    model_version,
    event_status
) VALUES
(
    'EVT_0001',
    'CAM_001',
    'TRK_001',
    'helmet',
    '2026-04-28 10:02:15',
    '2026-04-28 10:02:18',
    3,
    74,
    '/thumbs/evt_0001.jpg',
    '/clips/evt_0001.mp4',
    0.87,
    1,
    0,
    'yolov8n_v1',
    'pending'
),
(
    'EVT_0002',
    'CAM_001',
    'TRK_002',
    'vest',
    '2026-04-28 10:05:20',
    '2026-04-28 10:05:24',
    4,
    96,
    '/thumbs/evt_0002.jpg',
    '/clips/evt_0002.mp4',
    0.82,
    1,
    0,
    'yolov8n_v1',
    'pending'
),
(
    'EVT_0003',
    'CAM_002',
    'TRK_003',
    'helmet',
    '2026-04-28 11:10:01',
    '2026-04-28 11:10:03',
    2,
    48,
    '/thumbs/evt_0003.jpg',
    '/clips/evt_0003.mp4',
    0.79,
    1,
    0,
    'yolov8n_v1',
    'pending'
);

-- 분기별 요약 통계 더미 데이터
INSERT OR IGNORE INTO quarterly_summary (
    quarter,
    candidate_count,
    confirmed_count,
    false_positive_count,
    hold_count,
    created_at
) VALUES
(
    '2026-Q2',
    145,
    98,
    32,
    15,
    '2026-06-30 18:00:00'
);

-- PPE 유형별 통계 더미 데이터
INSERT OR IGNORE INTO quarterly_ppe_stats (
    stat_id,
    quarter,
    ppe_type,
    confirmed_count,
    priority_score
) VALUES
('STAT_PPE_2026Q2_01', '2026-Q2', 'helmet', 60, 8.6),
('STAT_PPE_2026Q2_02', '2026-Q2', 'vest', 38, 5.2);

-- 구역별 통계 더미 데이터
INSERT OR IGNORE INTO quarterly_zone_stats (
    stat_id,
    quarter,
    zone_name,
    confirmed_count,
    priority_score
) VALUES
('STAT_ZONE_2026Q2_01', '2026-Q2', '프레스 구역', 45, 8.6),
('STAT_ZONE_2026Q2_02', '2026-Q2', '절삭 가공 구역', 31, 6.4),
('STAT_ZONE_2026Q2_03', '2026-Q2', '자재 이동 구역', 22, 4.8);

-- 분기별 추이 더미 데이터
INSERT OR IGNORE INTO quarterly_trend_stats (
    trend_id,
    target_quarter,
    quarter,
    helmet,
    vest
) VALUES
('TREND_2026Q2_01', '2026-Q2', '2025-Q3', 40, 25),
('TREND_2026Q2_02', '2026-Q2', '2025-Q4', 52, 30),
('TREND_2026Q2_03', '2026-Q2', '2026-Q1', 48, 34),
('TREND_2026Q2_04', '2026-Q2', '2026-Q2', 60, 38);

-- 교육 추천 더미 데이터
INSERT OR IGNORE INTO education_recommendation (
    recommendation_id,
    quarter,
    recommendation_rank,
    ppe_type,
    zone_name,
    education_topic,
    priority_score,
    confirmed_count,
    repeat_weeks,
    zone_concentration,
    process_risk_weight,
    generated_at
) VALUES
(
    'EDU_2026Q2_01',
    '2026-Q2',
    1,
    'helmet',
    '프레스 구역',
    '안전모 착용 기준 및 착용 전 점검 절차 교육',
    8.6,
    60,
    5,
    0.61,
    1.0,
    '2026-06-30 18:00:00'
),
(
    'EDU_2026Q2_02',
    '2026-Q2',
    2,
    'vest',
    '절삭 가공 구역',
    '안전조끼 착용 필요성과 작업 구역 내 시인성 확보 교육',
    6.4,
    38,
    4,
    0.39,
    1.0,
    '2026-06-30 18:00:00'
),
(
    'EDU_2026Q2_03',
    '2026-Q2',
    3,
    'helmet',
    '자재 이동 구역',
    '자재 이동 작업 전 PPE 착용 상태 확인 교육',
    4.8,
    22,
    3,
    0.22,
    0.8,
    '2026-06-30 18:00:00'
);

-- 경고 방송 기본 설정
INSERT OR IGNORE INTO broadcast_setting (
    setting_id,
    enabled,
    default_language,
    cooldown_sec,
    updated_at
) VALUES (
    'DEFAULT',
    1,
    'ko',
    30,
    '2026-05-19 00:00:00'
);

-- 경고 방송 메시지 기본 템플릿
INSERT OR IGNORE INTO broadcast_message_template (
    template_id,
    setting_id,
    ppe_type,
    zone_name,
    language,
    message
) VALUES
(
    'BCAST_TEMPLATE_001',
    'DEFAULT',
    'helmet',
    '',
    'ko',
    '해당 작업 구역의 작업자는 안전모 착용 상태를 확인해 주세요.'
),
(
    'BCAST_TEMPLATE_002',
    'DEFAULT',
    'helmet',
    '',
    'en',
    'Workers in this area, please check your helmet.'
),
(
    'BCAST_TEMPLATE_003',
    'DEFAULT',
    'vest',
    '',
    'ko',
    '해당 작업 구역의 작업자는 안전조끼 착용 상태를 확인해 주세요.'
),
(
    'BCAST_TEMPLATE_004',
    'DEFAULT',
    'vest',
    '',
    'en',
    'Workers in this area, please check your safety vest.'
);