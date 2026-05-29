"""
test_event_rereview_api.py

재검토 API의 정상 처리 흐름을 확인하는 수동 테스트 스크립트.

확인 항목:
1. hold 상태 이벤트를 confirmed로 재검토할 수 있는지 확인
2. second_review_needed=True인 confirmed 이벤트를 재검토할 수 있는지 확인
3. 재검토 이후 candidate_event 상태와 event_review 값이 갱신되는지 확인

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
    get_review_by_event_id,
    insert_candidate_event,
)


client = TestClient(app)

TEST_EVENT_IDS = [
    "EVT_TEST_REREVIEW_HOLD",
    "EVT_TEST_REREVIEW_SECOND",
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


def main() -> None:
    cleanup_test_events()

    try:
        print_section("hold 이벤트 confirmed 재검토")
        test_hold_event_can_be_confirmed_after_rereview()

        print_section("2차 검토 필요 이벤트 재검토")
        test_second_review_needed_event_can_be_rereviewed()

        print("\n[OK] event re-review success case tests passed.")

    finally:
        cleanup_test_events()


if __name__ == "__main__":
    main()