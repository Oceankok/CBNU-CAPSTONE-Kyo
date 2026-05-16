"""
event_repository.py

PPE 후보 이벤트(candidate_event)와 담당자 검토 결과(event_review)를
SQLite DB에서 조회/삽입하기 위한 함수들을 모아둔 파일이다.

이 파일은 FastAPI 또는 Flask API 구현 시 DB 접근 계층으로 사용할 수 있다.

주요 역할:
- 후보 이벤트 전체 조회
- 후보 이벤트 단건 조회
- 후보 이벤트 삽입
- 담당자 검토 결과 삽입
- 특정 이벤트의 검토 결과 조회

주의:
- SQLite에서는 BOOLEAN 타입을 INTEGER로 저장한다.
  - True  -> 1
  - False -> 0
- ppe_system.db 파일은 로컬 실행 시 생성되는 파일이며 GitHub에는 업로드하지 않는다.
"""

import sqlite3
from pathlib import Path
from typing import Any
from datetime import datetime


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "ppe_system.db"


def get_connection() -> sqlite3.Connection:
    """
    SQLite DB 연결 객체를 생성한다.

    Returns:
        sqlite3.Connection:
            ppe_system.db에 연결된 SQLite connection 객체.

    Notes:
        - row_factory를 sqlite3.Row로 설정하여 조회 결과를 dict처럼 다룰 수 있게 한다.
        - SQLite는 기본적으로 외래키 제약 조건이 비활성화되어 있으므로
          PRAGMA foreign_keys = ON을 실행한다.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """
    sqlite3.Row 객체를 Python dict로 변환한다.

    Args:
        row (sqlite3.Row | None):
            SQLite 조회 결과 한 행.

    Returns:
        dict[str, Any] | None:
            조회 결과가 있으면 dict로 변환하여 반환하고,
            조회 결과가 없으면 None을 반환한다.
    """
    if row is None:
        return None
    return dict(row)


def get_all_candidate_events() -> list[dict[str, Any]]:
    """
    전체 후보 이벤트 목록을 조회한다.

    Returns:
        list[dict[str, Any]]:
            후보 이벤트 목록.
            각 항목은 candidate_event 정보와 camera_info의 zone_name,
            process_type을 함께 포함한다.

    Example:
        events = get_all_candidate_events()
        for event in events:
            print(event["event_id"], event["ppe_type"])
    """
    query = """
        SELECT
            ce.event_id,
            ce.camera_id,
            ci.zone_name,
            ci.process_type,
            ce.tracking_id,
            ce.ppe_type,
            ce.timestamp_start,
            ce.timestamp_end,
            ce.duration_sec,
            ce.frame_sample_count,
            ce.thumbnail_path,
            ce.video_clip_path,
            ce.ai_confidence,
            ce.person_detected,
            ce.ppe_detected,
            ce.model_version,
            ce.event_status
        FROM candidate_event ce
        LEFT JOIN camera_info ci
            ON ce.camera_id = ci.camera_id
        ORDER BY ce.timestamp_start DESC;
    """

    with get_connection() as conn:
        rows = conn.execute(query).fetchall()
        return [dict(row) for row in rows]


def get_candidate_event_by_id(event_id: str) -> dict[str, Any] | None:
    """
    event_id를 기준으로 후보 이벤트 1건을 조회한다.

    Args:
        event_id (str):
            조회할 후보 이벤트 ID.
            예: "EVT_0001"

    Returns:
        dict[str, Any] | None:
            해당 이벤트가 존재하면 이벤트 정보를 dict로 반환한다.
            존재하지 않으면 None을 반환한다.

    Example:
        event = get_candidate_event_by_id("EVT_0001")
        if event is not None:
            print(event["event_status"])
    """
    query = """
        SELECT
            ce.event_id,
            ce.camera_id,
            ci.zone_name,
            ci.process_type,
            ce.tracking_id,
            ce.ppe_type,
            ce.timestamp_start,
            ce.timestamp_end,
            ce.duration_sec,
            ce.frame_sample_count,
            ce.thumbnail_path,
            ce.video_clip_path,
            ce.ai_confidence,
            ce.person_detected,
            ce.ppe_detected,
            ce.model_version,
            ce.event_status
        FROM candidate_event ce
        LEFT JOIN camera_info ci
            ON ce.camera_id = ci.camera_id
        WHERE ce.event_id = ?;
    """

    with get_connection() as conn:
        row = conn.execute(query, (event_id,)).fetchone()
        return row_to_dict(row)


def insert_candidate_event(event: dict[str, Any]) -> None:
    """
    새로운 후보 이벤트를 candidate_event 테이블에 저장한다.

    Args:
        event (dict[str, Any]):
            저장할 후보 이벤트 정보.

    Required keys:
        - event_id
        - camera_id
        - tracking_id
        - ppe_type
        - timestamp_start
        - timestamp_end
        - duration_sec
        - frame_sample_count
        - thumbnail_path
        - video_clip_path
        - ai_confidence
        - person_detected
        - ppe_detected
        - model_version
        - event_status

    Notes:
        - camera_id는 camera_info 테이블에 이미 존재해야 한다.
        - person_detected, ppe_detected는 1 또는 0으로 저장한다.
        - event_status의 초기값은 보통 "pending"을 사용한다.

    Example:
        insert_candidate_event({
            "event_id": "EVT_0004",
            "camera_id": "CAM_001",
            "tracking_id": "TRK_004",
            "ppe_type": "helmet",
            "timestamp_start": "2026-04-28 13:00:00",
            "timestamp_end": "2026-04-28 13:00:03",
            "duration_sec": 3,
            "frame_sample_count": 72,
            "thumbnail_path": "/thumbs/evt_0004.jpg",
            "video_clip_path": "/clips/evt_0004.mp4",
            "ai_confidence": 0.91,
            "person_detected": 1,
            "ppe_detected": 0,
            "model_version": "yolov8n_v1",
            "event_status": "pending",
        })
    """
    query = """
        INSERT INTO candidate_event (
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
        ) VALUES (
            :event_id,
            :camera_id,
            :tracking_id,
            :ppe_type,
            :timestamp_start,
            :timestamp_end,
            :duration_sec,
            :frame_sample_count,
            :thumbnail_path,
            :video_clip_path,
            :ai_confidence,
            :person_detected,
            :ppe_detected,
            :model_version,
            :event_status
        );
    """

    with get_connection() as conn:
        conn.execute(query, event)
        conn.commit()

def delete_candidate_event(event_id: str) -> None:
    """
    event_id를 기준으로 후보 이벤트를 삭제함.

    Args:
        event_id (str):
            삭제할 후보 이벤트 ID.
    """
    query = """
        DELETE FROM candidate_event
        WHERE event_id = ?;
    """

    with get_connection() as conn:
        conn.execute(query, (event_id,))
        conn.commit()


def _upsert_false_positive_aggregate(
    conn: sqlite3.Connection,
    event: sqlite3.Row,
) -> None:
    """
    오탐 이벤트를 비식별 집계 테이블에 반영함.

    Args:
        conn (sqlite3.Connection):
            기존 DB connection.
        event (sqlite3.Row):
            오탐으로 판단된 후보 이벤트 정보.
    """
    quarter = _get_quarter_from_timestamp(event["timestamp_start"])
    aggregate_id = (
        f"FP_{quarter.replace('-', '')}_"
        f"{event['zone_name'].replace(' ', '_')}_"
        f"{event['ppe_type']}"
    )
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    existing = conn.execute(
        """
        SELECT false_positive_count
        FROM false_positive_aggregate
        WHERE aggregate_id = ?;
        """,
        (aggregate_id,),
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO false_positive_aggregate (
                aggregate_id,
                quarter,
                zone_name,
                ppe_type,
                false_positive_count,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                aggregate_id,
                quarter,
                event["zone_name"],
                event["ppe_type"],
                1,
                updated_at,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE false_positive_aggregate
            SET
                false_positive_count = false_positive_count + 1,
                updated_at = ?
            WHERE aggregate_id = ?;
            """,
            (updated_at, aggregate_id),
        )


def insert_event_review(review: dict[str, Any]) -> None:
    """
    후보 이벤트에 대한 담당자 검토 결과를 저장한다.

    Args:
        review (dict[str, Any]):
            저장할 검토 결과 정보.

    Required keys:
        - review_id
        - event_id
        - reviewer_id
        - review_result
        - review_reason_code
        - review_time
        - review_comment
        - confirmed_violation
        - second_review_needed

    review_result values:
        - "confirmed": 확정 위반
        - "false_positive": 오탐
        - "hold": 판단 보류

    Notes:
        - confirmed, hold는 candidate_event.event_status를 review_result 값으로 갱신한다.
        - false_positive는 비식별 오탐 집계에 반영한 뒤 candidate_event에서 삭제한다.
        - event_id는 candidate_event 테이블에 이미 존재해야 한다.
        - confirmed_violation, second_review_needed는 1 또는 0으로 저장한다.

    Example:
        insert_event_review({
            "review_id": "RV_0001",
            "event_id": "EVT_0001",
            "reviewer_id": "admin01",
            "review_result": "confirmed",
            "review_reason_code": "confirmed_no_helmet",
            "review_time": "2026-04-28 14:00:00",
            "review_comment": "실제 안전모 미착용",
            "confirmed_violation": 1,
            "second_review_needed": 0,
        })
    """
    review_query = """
        INSERT INTO event_review (
            review_id,
            event_id,
            reviewer_id,
            review_result,
            review_reason_code,
            review_time,
            review_comment,
            confirmed_violation,
            second_review_needed
        ) VALUES (
            :review_id,
            :event_id,
            :reviewer_id,
            :review_result,
            :review_reason_code,
            :review_time,
            :review_comment,
            :confirmed_violation,
            :second_review_needed
        );
    """

    status_query = """
        UPDATE candidate_event
        SET event_status = :event_status
        WHERE event_id = :event_id;
    """

    with get_connection() as conn:
        conn.execute(review_query, review)

        if review["review_result"] == "false_positive":
            event = conn.execute(
                """
                SELECT
                    ce.event_id,
                    ci.zone_name,
                    ce.ppe_type,
                    ce.timestamp_start
                FROM candidate_event ce
                LEFT JOIN camera_info ci
                    ON ce.camera_id = ci.camera_id
                WHERE ce.event_id = ?;
                """,
                (review["event_id"],),
            ).fetchone()

            if event is not None:
                _upsert_false_positive_aggregate(conn, event)

            conn.execute(
                """
                DELETE FROM candidate_event
                WHERE event_id = ?;
                """,
                (review["event_id"],),
            )
        else:
            conn.execute(
                status_query,
                {
                    "event_status": review["review_result"],
                    "event_id": review["event_id"],
                },
            )

        conn.commit()


def get_review_by_event_id(event_id: str) -> dict[str, Any] | None:
    """
    event_id를 기준으로 담당자 검토 결과를 조회한다.

    Args:
        event_id (str):
            검토 결과를 조회할 후보 이벤트 ID.

    Returns:
        dict[str, Any] | None:
            검토 결과가 존재하면 dict로 반환한다.
            아직 검토 결과가 없으면 None을 반환한다.

    Example:
        review = get_review_by_event_id("EVT_0001")
        if review is not None:
            print(review["review_result"])
    """
    query = """
        SELECT
            review_id,
            event_id,
            reviewer_id,
            review_result,
            review_reason_code,
            review_time,
            review_comment,
            confirmed_violation,
            second_review_needed
        FROM event_review
        WHERE event_id = ?;
    """

    with get_connection() as conn:
        row = conn.execute(query, (event_id,)).fetchone()
        return row_to_dict(row)


def get_quarterly_stats(quarter: str) -> dict | None:
    """
    분기별 통계 데이터를 조회함.

    Args:
        quarter (str):
            조회할 분기. 예: "2026-Q2"

    Returns:
        dict | None:
            프론트엔드 통계 화면에서 사용할 수 있는 형태의 통계 데이터.
    """
    with get_connection() as conn:
        summary_row = conn.execute(
            """
            SELECT
                quarter,
                candidate_count,
                confirmed_count,
                false_positive_count,
                hold_count
            FROM quarterly_summary
            WHERE quarter = ?;
            """,
            (quarter,),
        ).fetchone()

        if summary_row is None:
            return None

        ppe_rows = conn.execute(
            """
            SELECT
                ppe_type,
                confirmed_count,
                priority_score
            FROM quarterly_ppe_stats
            WHERE quarter = ?
            ORDER BY priority_score DESC;
            """,
            (quarter,),
        ).fetchall()

        zone_rows = conn.execute(
            """
            SELECT
                zone_name,
                confirmed_count,
                priority_score
            FROM quarterly_zone_stats
            WHERE quarter = ?
            ORDER BY priority_score DESC;
            """,
            (quarter,),
        ).fetchall()

        trend_rows = conn.execute(
            """
            SELECT
                quarter,
                helmet,
                vest
            FROM quarterly_trend_stats
            WHERE target_quarter = ?
            ORDER BY quarter ASC;
            """,
            (quarter,),
        ).fetchall()

    return {
        "quarter": summary_row["quarter"],
        "summary": {
            "quarter": summary_row["quarter"],
            "candidate_count": summary_row["candidate_count"],
            "confirmed_count": summary_row["confirmed_count"],
            "false_positive_count": summary_row["false_positive_count"],
            "hold_count": summary_row["hold_count"],
        },
        "by_ppe_type": [dict(row) for row in ppe_rows],
        "by_zone": [dict(row) for row in zone_rows],
        "trend": [dict(row) for row in trend_rows],
    }


def get_education_recommendations(quarter: str) -> dict:
    """
    분기별 교육 추천 데이터를 조회함.

    Args:
        quarter (str):
            조회할 분기. 예: "2026-Q2"

    Returns:
        dict:
            프론트엔드 교육 추천 화면에서 사용할 수 있는 형태의 추천 데이터.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                recommendation_id,
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
            FROM education_recommendation
            WHERE quarter = ?
            ORDER BY recommendation_rank ASC;
            """,
            (quarter,),
        ).fetchall()

    items = []
    generated_at = None

    for row in rows:
        if generated_at is None:
            generated_at = row["generated_at"]

        items.append(
            {
                "recommendation_id": row["recommendation_id"],
                "recommendation_rank": row["recommendation_rank"],
                "ppe_type": row["ppe_type"],
                "zone_name": row["zone_name"],
                "education_topic": row["education_topic"],
                "priority_score": row["priority_score"],
                "score_breakdown": {
                    "confirmed_count": row["confirmed_count"],
                    "repeat_weeks": row["repeat_weeks"],
                    "zone_concentration": row["zone_concentration"],
                    "process_risk_weight": row["process_risk_weight"],
                },
                "generated_at": row["generated_at"],
            }
        )

    return {
        "quarter": quarter,
        "generated_at": generated_at,
        "items": items,
    }


def _get_quarter_date_range(quarter: str) -> tuple[str, str]:
    """
    분기 문자열을 시작일과 종료일로 변환함.

    Args:
        quarter (str):
            예: "2026-Q2"

    Returns:
        tuple[str, str]:
            시작일, 종료일. 종료일은 다음 분기 시작일 기준.
    """
    try:
        year_text, quarter_text = quarter.split("-Q")
        year = int(year_text)
        quarter_number = int(quarter_text)
    except ValueError as exc:
        raise ValueError("quarter must be formatted like 2026-Q2") from exc

    if quarter_number not in {1, 2, 3, 4}:
        raise ValueError("quarter number must be one of 1, 2, 3, 4")

    start_month = (quarter_number - 1) * 3 + 1

    if quarter_number == 4:
        end_year = year + 1
        end_month = 1
    else:
        end_year = year
        end_month = start_month + 3

    start_date = f"{year:04d}-{start_month:02d}-01 00:00:00"
    end_date = f"{end_year:04d}-{end_month:02d}-01 00:00:00"

    return start_date, end_date


def _get_quarter_from_timestamp(timestamp: str) -> str:
    """
    timestamp 문자열을 분기 문자열로 변환함.

    Args:
        timestamp (str):
            예: "2026-04-28 10:15:00"

    Returns:
        str:
            예: "2026-Q2"
    """
    year = int(timestamp[:4])
    month = int(timestamp[5:7])
    quarter_number = ((month - 1) // 3) + 1

    return f"{year}-Q{quarter_number}"


def generate_quarterly_stats(quarter: str) -> dict:
    """
    후보 이벤트와 담당자 검토 결과를 기반으로 분기별 통계를 생성함.

    Args:
        quarter (str):
            생성할 분기. 예: "2026-Q2"

    Returns:
        dict:
            생성된 분기별 통계 데이터.
    """
    start_date, end_date = _get_quarter_date_range(quarter)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        candidate_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM candidate_event
            WHERE timestamp_start >= ?
              AND timestamp_start < ?;
            """,
            (start_date, end_date),
        ).fetchone()["count"]

        confirmed_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM candidate_event
            WHERE timestamp_start >= ?
              AND timestamp_start < ?
              AND event_status = 'confirmed';
            """,
            (start_date, end_date),
        ).fetchone()["count"]

        hold_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM candidate_event
            WHERE timestamp_start >= ?
              AND timestamp_start < ?
              AND event_status = 'hold';
            """,
            (start_date, end_date),
        ).fetchone()["count"]

        false_positive_count = conn.execute(
            """
            SELECT COALESCE(SUM(false_positive_count), 0) AS count
            FROM false_positive_aggregate
            WHERE quarter = ?;
            """,
            (quarter,),
        ).fetchone()["count"]

        conn.execute(
            "DELETE FROM education_recommendation WHERE quarter = ?;",
            (quarter,),
        )
        conn.execute(
            "DELETE FROM quarterly_ppe_stats WHERE quarter = ?;",
            (quarter,),
        )
        conn.execute(
            "DELETE FROM quarterly_zone_stats WHERE quarter = ?;",
            (quarter,),
        )
        conn.execute(
            "DELETE FROM quarterly_trend_stats WHERE target_quarter = ?;",
            (quarter,),
        )
        conn.execute(
            "DELETE FROM quarterly_summary WHERE quarter = ?;",
            (quarter,),
        )

        conn.execute(
            """
            INSERT INTO quarterly_summary (
                quarter,
                candidate_count,
                confirmed_count,
                false_positive_count,
                hold_count,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                quarter,
                candidate_count,
                confirmed_count,
                false_positive_count,
                hold_count,
                created_at,
            ),
        )

        ppe_rows = conn.execute(
            """
            SELECT
                ppe_type,
                COUNT(*) AS confirmed_count,
                COUNT(DISTINCT strftime('%W', timestamp_start)) AS repeat_weeks
            FROM candidate_event
            WHERE timestamp_start >= ?
              AND timestamp_start < ?
              AND event_status = 'confirmed'
            GROUP BY ppe_type
            ORDER BY confirmed_count DESC;
            """,
            (start_date, end_date),
        ).fetchall()

        for index, row in enumerate(ppe_rows, start=1):
            zone_concentration = (
                row["confirmed_count"] / confirmed_count
                if confirmed_count > 0
                else 0
            )
            priority_score = (
                row["confirmed_count"] * 0.5
                + row["repeat_weeks"] * 0.2
                + zone_concentration * 0.2
                + 1.0 * 0.1
            )

            conn.execute(
                """
                INSERT INTO quarterly_ppe_stats (
                    stat_id,
                    quarter,
                    ppe_type,
                    confirmed_count,
                    priority_score
                )
                VALUES (?, ?, ?, ?, ?);
                """,
                (
                    f"STAT_PPE_{quarter.replace('-', '')}_{index:02d}",
                    quarter,
                    row["ppe_type"],
                    row["confirmed_count"],
                    round(priority_score, 2),
                ),
            )

        zone_rows = conn.execute(
            """
            SELECT
                zone_name,
                COUNT(*) AS confirmed_count,
                COUNT(DISTINCT strftime('%W', timestamp_start)) AS repeat_weeks
            FROM candidate_event
            WHERE timestamp_start >= ?
              AND timestamp_start < ?
              AND event_status = 'confirmed'
            GROUP BY zone_name
            ORDER BY confirmed_count DESC;
            """,
            (start_date, end_date),
        ).fetchall()

        for index, row in enumerate(zone_rows, start=1):
            zone_concentration = (
                row["confirmed_count"] / confirmed_count
                if confirmed_count > 0
                else 0
            )
            priority_score = (
                row["confirmed_count"] * 0.5
                + row["repeat_weeks"] * 0.2
                + zone_concentration * 0.2
                + 1.0 * 0.1
            )

            conn.execute(
                """
                INSERT INTO quarterly_zone_stats (
                    stat_id,
                    quarter,
                    zone_name,
                    confirmed_count,
                    priority_score
                )
                VALUES (?, ?, ?, ?, ?);
                """,
                (
                    f"STAT_ZONE_{quarter.replace('-', '')}_{index:02d}",
                    quarter,
                    row["zone_name"],
                    row["confirmed_count"],
                    round(priority_score, 2),
                ),
            )

        trend_rows = conn.execute(
            """
            SELECT
                substr(timestamp_start, 1, 4) ||
                '-Q' ||
                ((CAST(substr(timestamp_start, 6, 2) AS INTEGER) - 1) / 3 + 1)
                AS quarter,
                SUM(CASE WHEN ppe_type = 'helmet' THEN 1 ELSE 0 END) AS helmet,
                SUM(CASE WHEN ppe_type = 'vest' THEN 1 ELSE 0 END) AS vest
            FROM candidate_event
            WHERE event_status = 'confirmed'
            GROUP BY quarter
            ORDER BY quarter ASC;
            """
        ).fetchall()

        for index, row in enumerate(trend_rows, start=1):
            conn.execute(
                """
                INSERT INTO quarterly_trend_stats (
                    trend_id,
                    target_quarter,
                    quarter,
                    helmet,
                    vest
                )
                VALUES (?, ?, ?, ?, ?);
                """,
                (
                    f"TREND_{quarter.replace('-', '')}_{index:02d}",
                    quarter,
                    row["quarter"],
                    row["helmet"] or 0,
                    row["vest"] or 0,
                ),
            )

        conn.commit()

    stats = get_quarterly_stats(quarter)

    if stats is None:
        raise RuntimeError("Failed to generate quarterly stats")

    return stats

def _get_education_topic(ppe_type: str, zone_name: str) -> str:
    """
    PPE 유형과 구역을 기반으로 교육 주제를 생성함.
    """
    if ppe_type == "helmet":
        return f"{zone_name} 안전모 착용 기준 및 착용 전 점검 절차 교육"

    if ppe_type == "vest":
        return f"{zone_name} 안전조끼 착용 필요성과 작업 구역 내 시인성 확보 교육"

    return f"{zone_name} PPE 착용 기준 및 작업 전 점검 교육"


def generate_education_recommendations(quarter: str) -> dict:
    """
    확정 위반 데이터를 기반으로 교육 추천 결과를 생성함.

    Args:
        quarter (str):
            생성할 분기. 예: "2026-Q2"

    Returns:
        dict:
            생성된 교육 추천 데이터.
    """
    start_date, end_date = _get_quarter_date_range(quarter)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        total_confirmed = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM candidate_event
            WHERE timestamp_start >= ?
              AND timestamp_start < ?
              AND event_status = 'confirmed';
            """,
            (start_date, end_date),
        ).fetchone()["count"]

        rows = conn.execute(
            """
            SELECT
                ppe_type,
                zone_name,
                COUNT(*) AS confirmed_count,
                COUNT(DISTINCT strftime('%W', timestamp_start)) AS repeat_weeks
            FROM candidate_event
            WHERE timestamp_start >= ?
              AND timestamp_start < ?
              AND event_status = 'confirmed'
            GROUP BY ppe_type, zone_name
            ORDER BY confirmed_count DESC;
            """,
            (start_date, end_date),
        ).fetchall()

        conn.execute(
            "DELETE FROM education_recommendation WHERE quarter = ?;",
            (quarter,),
        )

        scored_items = []

        for row in rows:
            zone_concentration = (
                row["confirmed_count"] / total_confirmed
                if total_confirmed > 0
                else 0
            )

            process_risk_weight = 1.0

            priority_score = (
                row["confirmed_count"] * 0.5
                + row["repeat_weeks"] * 0.2
                + zone_concentration * 0.2
                + process_risk_weight * 0.1
            )

            scored_items.append(
                {
                    "ppe_type": row["ppe_type"],
                    "zone_name": row["zone_name"],
                    "confirmed_count": row["confirmed_count"],
                    "repeat_weeks": row["repeat_weeks"],
                    "zone_concentration": zone_concentration,
                    "process_risk_weight": process_risk_weight,
                    "priority_score": round(priority_score, 2),
                }
            )

        scored_items.sort(
            key=lambda item: item["priority_score"],
            reverse=True,
        )

        for rank, item in enumerate(scored_items[:3], start=1):
            recommendation_id = f"EDU_{quarter.replace('-', '')}_{rank:02d}"

            conn.execute(
                """
                INSERT INTO education_recommendation (
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
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    recommendation_id,
                    quarter,
                    rank,
                    item["ppe_type"],
                    item["zone_name"],
                    _get_education_topic(item["ppe_type"], item["zone_name"]),
                    item["priority_score"],
                    item["confirmed_count"],
                    item["repeat_weeks"],
                    round(item["zone_concentration"], 2),
                    item["process_risk_weight"],
                    generated_at,
                ),
            )

        conn.commit()

    return get_education_recommendations(quarter)