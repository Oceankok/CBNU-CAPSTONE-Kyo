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