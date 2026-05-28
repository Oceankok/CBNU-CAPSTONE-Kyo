"""후보 이벤트 생성과 TTS 경고 방송 연동 수동 테스트 스크립트."""

from typing import Optional

from backend.db.event_repository import (
    delete_candidate_event,
    get_broadcast_settings,
    save_broadcast_settings,
)
from backend.services.candidate_event_service import create_no_helmet_candidate_event
from backend.services.warning_broadcast_service import reset_broadcast_cooldown


TEST_SETTINGS = {
    "enabled": True,
    "default_language": "ko",
    "cooldown_sec": 0,
    "templates": [
        {
            "ppe_type": "helmet",
            "zone_name": "프레스 구역",
            "language": "ko",
            "message": "프레스 구역 작업자는 안전모 착용 상태를 확인해 주세요.",
        },
        {
            "ppe_type": "helmet",
            "zone_name": "",
            "language": "ko",
            "message": "해당 작업 구역의 작업자는 안전모 착용 상태를 확인해 주세요.",
        },
    ],
}


def run_test() -> None:
    original_settings = get_broadcast_settings()
    created_event_id: Optional[str] = None

    try:
        save_broadcast_settings(TEST_SETTINGS)
        reset_broadcast_cooldown()

        print("\n[test] candidate event 저장 후 실제 TTS 경고 방송 출력")

        saved_event = create_no_helmet_candidate_event(
            camera_id="CAM_001",
            confidence=0.88,
            source_path="test_videos/test_video1.avi",
            frame_image=None,
            model_version="helmet_yolov8n",
            enable_tts=True,
        )

        created_event_id = saved_event["event_id"]
        broadcast_result = saved_event["broadcast"]

        assert broadcast_result["executed"] is True
        assert broadcast_result["reason"] == "broadcast_printed"
        assert broadcast_result["zone_name"] == "프레스 구역"
        assert broadcast_result["language"] == "ko"
        assert broadcast_result["tts"]["spoken"] is True
        assert broadcast_result["tts"]["reason"] == "tts_completed"

        print("\n[OK] candidate event TTS integration test passed.")

    finally:
        if created_event_id is not None:
            delete_candidate_event(created_event_id)

        save_broadcast_settings(original_settings)
        reset_broadcast_cooldown()


if __name__ == "__main__":
    run_test()