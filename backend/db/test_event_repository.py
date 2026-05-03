"""
test_event_repository.py

event_repository.py에 작성한 DB 접근 함수들이 정상적으로 작동하는지 확인하는 실행 파일이다.

확인 항목:
1. 후보 이벤트 전체 조회
2. 후보 이벤트 단건 조회
3. 새 후보 이벤트 삽입
4. 담당자 검토 결과 삽입
5. 검토 결과 저장 후 candidate_event.event_status 갱신 확인

실행 전:
    python backend/db/init_db.py

실행:
    python backend/db/test_event_repository.py
"""

from datetime import datetime

from event_repository import (
    get_all_candidate_events,
    get_candidate_event_by_id,
    insert_candidate_event,
    insert_event_review,
    get_review_by_event_id,
)


def print_section(title: str) -> None:
    """터미널 출력 구분선을 표시한다."""
    print(f"\n=== {title} ===")


def main() -> None:
    print_section("전체 후보 이벤트 조회")
    events = get_all_candidate_events()
    for event in events:
        print(event)

    print_section("후보 이벤트 단건 조회")
    event = get_candidate_event_by_id("EVT_0001")
    print(event)

    print_section("새 후보 이벤트 삽입")
    new_event = {
        "event_id": "EVT_TEST_0001",
        "camera_id": "CAM_001",
        "tracking_id": "TRK_TEST",
        "ppe_type": "helmet",
        "timestamp_start": "2026-04-28 13:00:00",
        "timestamp_end": "2026-04-28 13:00:03",
        "duration_sec": 3,
        "frame_sample_count": 72,
        "thumbnail_path": "/thumbs/evt_test_0001.jpg",
        "video_clip_path": "/clips/evt_test_0001.mp4",
        "ai_confidence": 0.91,
        "person_detected": 1,
        "ppe_detected": 0,
        "model_version": "yolov8n_v1",
        "event_status": "pending",
    }

    try:
        insert_candidate_event(new_event)
        print("후보 이벤트 삽입 성공")
    except Exception as error:
        print(f"후보 이벤트 삽입 생략 또는 실패: {error}")

    print_section("삽입된 후보 이벤트 조회")
    inserted_event = get_candidate_event_by_id("EVT_TEST_0001")
    print(inserted_event)

    print_section("검토 결과 삽입")
    review = {
        "review_id": "RV_TEST_0001",
        "event_id": "EVT_TEST_0001",
        "reviewer_id": "admin01",
        "review_result": "confirmed",
        "review_reason_code": "confirmed_no_helmet",
        "review_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "review_comment": "테스트용 확정 위반 데이터",
        "confirmed_violation": 1,
        "second_review_needed": 0,
    }

    try:
        insert_event_review(review)
        print("검토 결과 삽입 성공")
    except Exception as error:
        print(f"검토 결과 삽입 생략 또는 실패: {error}")

    print_section("검토 결과 조회")
    inserted_review = get_review_by_event_id("EVT_TEST_0001")
    print(inserted_review)

    print_section("검토 후 후보 이벤트 상태 확인")
    updated_event = get_candidate_event_by_id("EVT_TEST_0001")
    print(updated_event)


if __name__ == "__main__":
    main()