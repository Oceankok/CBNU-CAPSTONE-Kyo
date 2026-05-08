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
        - review 저장 후 candidate_event.event_status도 review_result 값으로 함께 갱신한다.
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