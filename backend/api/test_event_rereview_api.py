"""
test_event_rereview_api.py

재검토 API의 성공·거부·오탐 처리 흐름을 확인하는 수동 테스트 스크립트.

확인 항목:
1. hold 상태 이벤트를 confirmed로 재검토할 수 있는지 확인
2. second_review_needed=True인 confirmed 이벤트를 재검토할 수 있는지 확인
3. 기존 review가 없는 이벤트의 재검토 요청이 거부되는지 확인
4. 재검토 대상이 아닌 이벤트의 재검토 요청이 거부되는지 확인
5. hold 이벤트를 false_positive로 재검토하면 이벤트가 삭제되고
   오탐 집계가 증가하는지 확인

실행 전:
    python backend/db/init_db.py

실행:
    python -m backend.api.test_event_rereview_api
"""

from typing import Any

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.db.event_repository import (
    delete_candidate_event,
    get_candidate_event_by_id,
    get_connection,
    get_review_by_event_id,
    insert_candidate_event,
)


client = TestClient(app)

TEST_EVENT_IDS = [
    "EVT_TEST_REREVIEW_HOLD",
    "EVT_TEST_REREVIEW_SECOND",
    "EVT_TEST_REREVIEW_NO_REVIEW",
    "EVT_TEST_REREVIEW_NOT_ELIGIBLE",
    "EVT_TEST_REREVIEW_FALSE_POSITIVE",
]


def print_section(title: str) -> None:
    """터미널 출력 구분선을 표시함."""
    print(f"\n=== {title} ===")


def make_candidate_event(
    event_id: str,
    tracking_id: str,
    timestamp_start: str,
) -> dict[str, Any]:
    """재검토 API 테스트용 후보 이벤트 데이터를 생성함."""
    return {
        "event_id": event_id,
        "camera_id": "CAM_001",
        "tracking_id": tracking_id,
        "ppe_type": "helmet",
        "timestamp_start": timestamp_start,
        "timestamp_end": timestamp_start,
        "duration_sec": 3,
        "frame_sample_count": 72,
        "thumbnail_path": f"/thumbs/{event_id.lower()}.jpg",
        "video_clip_path": f"/clips/{event_id.lower()}.mp4",
        "ai_confidence": 0.91,
        "person_detected": 1,
        "ppe_detected": 0,
        "model_version": "yolov8n_v1",
        "event_status": "pending",
    }


def cleanup_test_events() -> None:
    """이전 테스트 실행 과정에서 남은 테스트 이벤트를 삭제함."""
    for event_id in TEST_EVENT_IDS:
        if get_candidate_event_by_id(event_id) is not None:
            delete_candidate_event(event_id)


def get_false_positive_count(
    quarter: str,
    zone_name: str,
    ppe_type: str,
) -> int:
    """지정 조건의 오탐 집계 건수를 조회함."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT false_positive_count
            FROM false_positive_aggregate
            WHERE quarter = ?
              AND zone_name = ?
              AND ppe_type = ?;
            """,
            (quarter, zone_name, ppe_type),
        ).fetchone()

    if row is None:
        return 0

    return int(row["false_positive_count"])


def test_hold_event_can_be_confirmed_after_rereview() -> None:
    """hold 상태 이벤트를 confirmed로 재검토할 수 있는지 확인함."""
    event_id = "EVT_TEST_REREVIEW_HOLD"

    insert_candidate_event(
        make_candidate_event(
            event_id=event_id,
            tracking_id="TRK_TEST_REREVIEW_HOLD",
            timestamp_start="2026-05-28 10:00:00",
        )
    )

    first_review_response = client.post(
        f"/api/events/{event_id}/review",
        json={
            "reviewer_id": "admin01",
            "review_result": "hold",
            "review_reason_code": "needs_additional_check",
            "review_comment": "영상 확인 필요",
            "second_review_needed": False,
        },
    )

    assert first_review_response.status_code == 200
    assert first_review_response.json()["event"]["event_status"] == "hold"
    assert first_review_response.json()["review"]["review_result"] == "hold"

    rereview_response = client.put(
        f"/api/events/{event_id}/review",
        json={
            "reviewer_id": "admin02",
            "review_result": "confirmed",
            "review_reason_code": "confirmed_no_helmet",
            "review_comment": "재검토 결과 실제 위반 확인",
            "second_review_needed": False,
        },
    )

    assert rereview_response.status_code == 200

    response_body = rereview_response.json()
    assert response_body["event"]["event_status"] == "confirmed"
    assert response_body["review"]["reviewer_id"] == "admin02"
    assert response_body["review"]["review_result"] == "confirmed"
    assert response_body["review"]["confirmed_violation"] == 1
    assert response_body["review"]["second_review_needed"] == 0

    saved_event = get_candidate_event_by_id(event_id)
    saved_review = get_review_by_event_id(event_id)

    assert saved_event is not None
    assert saved_event["event_status"] == "confirmed"
    assert saved_review is not None
    assert saved_review["review_result"] == "confirmed"

    print("[PASS] hold 이벤트를 confirmed로 재검토함")


def test_second_review_needed_event_can_be_rereviewed() -> None:
    """2차 검토 필요 이벤트를 다시 검토할 수 있는지 확인함."""
    event_id = "EVT_TEST_REREVIEW_SECOND"

    insert_candidate_event(
        make_candidate_event(
            event_id=event_id,
            tracking_id="TRK_TEST_REREVIEW_SECOND",
            timestamp_start="2026-05-28 10:10:00",
        )
    )

    first_review_response = client.post(
        f"/api/events/{event_id}/review",
        json={
            "reviewer_id": "admin01",
            "review_result": "confirmed",
            "review_reason_code": "confirmed_no_helmet",
            "review_comment": "2차 확인 필요",
            "second_review_needed": True,
        },
    )

    assert first_review_response.status_code == 200
    assert first_review_response.json()["event"]["event_status"] == "confirmed"
    assert first_review_response.json()["review"]["second_review_needed"] == 1

    rereview_response = client.put(
        f"/api/events/{event_id}/review",
        json={
            "reviewer_id": "admin02",
            "review_result": "hold",
            "review_reason_code": "needs_additional_check",
            "review_comment": "추가 영상 확인 보류",
            "second_review_needed": False,
        },
    )

    assert rereview_response.status_code == 200

    response_body = rereview_response.json()
    assert response_body["event"]["event_status"] == "hold"
    assert response_body["review"]["reviewer_id"] == "admin02"
    assert response_body["review"]["review_result"] == "hold"
    assert response_body["review"]["second_review_needed"] == 0

    saved_event = get_candidate_event_by_id(event_id)
    saved_review = get_review_by_event_id(event_id)

    assert saved_event is not None
    assert saved_event["event_status"] == "hold"
    assert saved_review is not None
    assert saved_review["review_result"] == "hold"

    print("[PASS] second_review_needed 이벤트를 재검토함")


def test_event_without_review_cannot_be_rereviewed() -> None:
    """기존 review가 없는 이벤트의 재검토 요청이 거부되는지 확인함."""
    event_id = "EVT_TEST_REREVIEW_NO_REVIEW"

    insert_candidate_event(
        make_candidate_event(
            event_id=event_id,
            tracking_id="TRK_TEST_REREVIEW_NO_REVIEW",
            timestamp_start="2026-05-28 10:20:00",
        )
    )

    response = client.put(
        f"/api/events/{event_id}/review",
        json={
            "reviewer_id": "admin02",
            "review_result": "confirmed",
            "review_reason_code": "confirmed_no_helmet",
            "review_comment": "재검토 요청",
            "second_review_needed": False,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Review does not exist"

    saved_event = get_candidate_event_by_id(event_id)
    saved_review = get_review_by_event_id(event_id)

    assert saved_event is not None
    assert saved_event["event_status"] == "pending"
    assert saved_review is None

    print("[PASS] 기존 review가 없는 이벤트의 재검토 요청을 거부함")


def test_confirmed_event_without_second_review_cannot_be_rereviewed() -> None:
    """재검토 대상이 아닌 confirmed 이벤트 요청이 거부되는지 확인함."""
    event_id = "EVT_TEST_REREVIEW_NOT_ELIGIBLE"

    insert_candidate_event(
        make_candidate_event(
            event_id=event_id,
            tracking_id="TRK_TEST_REREVIEW_NOT_ELIGIBLE",
            timestamp_start="2026-05-28 10:30:00",
        )
    )

    first_review_response = client.post(
        f"/api/events/{event_id}/review",
        json={
            "reviewer_id": "admin01",
            "review_result": "confirmed",
            "review_reason_code": "confirmed_no_helmet",
            "review_comment": "위반 확정",
            "second_review_needed": False,
        },
    )

    assert first_review_response.status_code == 200

    rereview_response = client.put(
        f"/api/events/{event_id}/review",
        json={
            "reviewer_id": "admin02",
            "review_result": "hold",
            "review_reason_code": "needs_additional_check",
            "review_comment": "재검토 요청",
            "second_review_needed": False,
        },
    )

    assert rereview_response.status_code == 409
    assert rereview_response.json()["detail"] == "Event is not eligible for re-review"

    saved_event = get_candidate_event_by_id(event_id)
    saved_review = get_review_by_event_id(event_id)

    assert saved_event is not None
    assert saved_event["event_status"] == "confirmed"
    assert saved_review is not None
    assert saved_review["reviewer_id"] == "admin01"
    assert saved_review["review_result"] == "confirmed"

    print("[PASS] 재검토 대상이 아닌 confirmed 이벤트 요청을 거부함")


def test_false_positive_rereview_deletes_event_and_updates_aggregate() -> None:
    """false_positive 재검토 시 후보 이벤트 삭제 및 오탐 집계를 확인함."""
    event_id = "EVT_TEST_REREVIEW_FALSE_POSITIVE"

    insert_candidate_event(
        make_candidate_event(
            event_id=event_id,
            tracking_id="TRK_TEST_REREVIEW_FALSE_POSITIVE",
            timestamp_start="2026-05-28 10:40:00",
        )
    )

    saved_event = get_candidate_event_by_id(event_id)

    assert saved_event is not None

    quarter = "2026-Q2"
    zone_name = saved_event["zone_name"]
    ppe_type = saved_event["ppe_type"]

    count_before = get_false_positive_count(
        quarter=quarter,
        zone_name=zone_name,
        ppe_type=ppe_type,
    )

    first_review_response = client.post(
        f"/api/events/{event_id}/review",
        json={
            "reviewer_id": "admin01",
            "review_result": "hold",
            "review_reason_code": "needs_additional_check",
            "review_comment": "오탐 가능성 확인 필요",
            "second_review_needed": False,
        },
    )

    assert first_review_response.status_code == 200
    assert first_review_response.json()["event"]["event_status"] == "hold"

    rereview_response = client.put(
        f"/api/events/{event_id}/review",
        json={
            "reviewer_id": "admin02",
            "review_result": "false_positive",
            "review_reason_code": "false_detection",
            "review_comment": "재검토 결과 오탐",
            "second_review_needed": False,
        },
    )

    assert rereview_response.status_code == 200
    assert rereview_response.json()["review_result"] == "false_positive"

    deleted_event = get_candidate_event_by_id(event_id)
    deleted_review = get_review_by_event_id(event_id)

    assert deleted_event is None
    assert deleted_review is None

    count_after = get_false_positive_count(
        quarter=quarter,
        zone_name=zone_name,
        ppe_type=ppe_type,
    )

    assert count_after == count_before + 1

    get_response = client.get(f"/api/events/{event_id}")
    assert get_response.status_code == 404
    assert get_response.json()["detail"] == "Event not found"

    print("[PASS] false_positive 재검토 시 이벤트 삭제 및 오탐 집계를 반영함")


def main() -> None:
    cleanup_test_events()

    try:
        print_section("hold 이벤트 confirmed 재검토")
        test_hold_event_can_be_confirmed_after_rereview()

        print_section("2차 검토 필요 이벤트 재검토")
        test_second_review_needed_event_can_be_rereviewed()

        print_section("기존 review 없는 이벤트 재검토 거부")
        test_event_without_review_cannot_be_rereviewed()

        print_section("재검토 대상이 아닌 이벤트 요청 거부")
        test_confirmed_event_without_second_review_cannot_be_rereviewed()

        print_section("false_positive 재검토 처리")
        test_false_positive_rereview_deletes_event_and_updates_aggregate()

        print("\n[OK] event re-review regression tests passed.")

    finally:
        cleanup_test_events()


if __name__ == "__main__":
    main()