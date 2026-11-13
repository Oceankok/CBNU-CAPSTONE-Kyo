-- Fresh database recommendation fixtures only. Never replay after initialization.
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
