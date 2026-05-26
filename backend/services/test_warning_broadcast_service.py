"""경고 방송 실행 서비스 수동 테스트 스크립트."""

from typing import Optional

from backend.db.event_repository import (
    delete_candidate_event,
    get_broadcast_settings,
    save_broadcast_settings,
)
from backend.services.candidate_event_service import create_no_helmet_candidate_event
from backend.services.warning_broadcast_service import (
    execute_warning_broadcast,
    reset_broadcast_cooldown,
)


TEST_SETTINGS_KO = {
    "enabled": True,
    "default_language": "ko",
    "cooldown_sec": 30,
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
        {
            "ppe_type": "vest",
            "zone_name": "",
            "language": "en",
            "message": "Workers in this area, please check your safety vest.",
        },
    ],
}

TEST_SETTINGS_EN = {
    "enabled": True,
    "default_language": "en",
    "cooldown_sec": 30,
    "templates": TEST_SETTINGS_KO["templates"],
}

TEST_SETTINGS_DISABLED = {
    "enabled": False,
    "default_language": "ko",
    "cooldown_sec": 30,
    "templates": TEST_SETTINGS_KO["templates"],
}


def run_test() -> None:
    original_settings = get_broadcast_settings()
    created_event_id: Optional[str] = None

    try:
        print("\n[test 1] candidate event 저장 후 구역별 한국어 경고 방송 출력")
        save_broadcast_settings(TEST_SETTINGS_KO)
        reset_broadcast_cooldown()

        saved_event = create_no_helmet_candidate_event(
            camera_id="CAM_001",
            confidence=0.88,
            source_path="test_videos/test_video1.avi",
            frame_image=None,
            model_version="helmet_yolov8n",
            enable_tts=False,
        )

        created_event_id = saved_event["event_id"]
        first_broadcast = saved_event["broadcast"]

        assert first_broadcast["executed"] is True
        assert first_broadcast["reason"] == "broadcast_printed"
        assert first_broadcast["zone_name"] == "프레스 구역"
        assert (
            first_broadcast["message"]
            == "프레스 구역 작업자는 안전모 착용 상태를 확인해 주세요."
        )

        print("\n[test 2] 동일 상황 재방송 cooldown 적용")
        duplicate_broadcast = execute_warning_broadcast(
            event_id=created_event_id,
            ppe_type="helmet",
            zone_name="프레스 구역",
        )

        assert duplicate_broadcast["executed"] is False
        assert duplicate_broadcast["reason"] == "cooldown_active"

        print("\n[test 3] 기본 언어 English 및 전체 구역 템플릿 적용")
        save_broadcast_settings(TEST_SETTINGS_EN)
        reset_broadcast_cooldown()

        english_broadcast = execute_warning_broadcast(
            event_id="TEST_VEST_EVENT",
            ppe_type="vest",
            zone_name="자재 이동 구역",
        )

        assert english_broadcast["executed"] is True
        assert english_broadcast["language"] == "en"
        assert (
            english_broadcast["message"]
            == "Workers in this area, please check your safety vest."
        )

        print("\n[test 4] 방송 OFF 설정 적용")
        save_broadcast_settings(TEST_SETTINGS_DISABLED)
        reset_broadcast_cooldown()

        disabled_broadcast = execute_warning_broadcast(
            event_id="TEST_DISABLED_EVENT",
            ppe_type="helmet",
            zone_name="프레스 구역",
        )

        assert disabled_broadcast["executed"] is False
        assert disabled_broadcast["reason"] == "broadcast_disabled"

        print("\n[OK] warning broadcast service test passed.")

    finally:
        if created_event_id is not None:
            delete_candidate_event(created_event_id)

        save_broadcast_settings(original_settings)
        reset_broadcast_cooldown()


if __name__ == "__main__":
    run_test()