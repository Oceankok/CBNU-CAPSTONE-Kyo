"""경고 방송 실행 서비스 수동 테스트 스크립트."""

from typing import Any

import backend.services.warning_broadcast_service as broadcast_service
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


def mock_failed_tts(message: str, language: str) -> dict[str, Any]:
    """
    TTS 출력 실패 상황을 검증하기 위한 mock 함수.

    Args:
        message (str):
            출력 대상 경고 메시지.
        language (str):
            방송 언어.

    Returns:
        dict[str, Any]:
            TTS 실패 결과.
    """
    return {
        "spoken": False,
        "reason": "tts_error",
        "language": language,
        "message": message,
        "error": "mock tts failure",
    }


def mock_raised_tts(message: str, language: str) -> dict[str, Any]:
    """
    TTS 실행 중 예외가 발생하는 상황을 검증하기 위한 mock 함수.
    """
    raise RuntimeError("mock tts engine failure")


def run_test() -> None:
    original_settings = get_broadcast_settings()
    original_speak_message = broadcast_service.speak_message
    created_event_ids: list[str] = []

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
            enable_tts=True,
        )

        created_event_id = saved_event["event_id"]
        created_event_ids.append(created_event_id)
        first_broadcast = saved_event["broadcast"]

        assert first_broadcast["executed"] is True
        assert first_broadcast["reason"] == "broadcast_printed"
        assert first_broadcast["zone_name"] == "프레스 구역"
        assert first_broadcast["language"] == "ko"
        assert (
            first_broadcast["message"]
            == "프레스 구역 작업자는 안전모 착용 상태를 확인해 주세요."
        )
        assert first_broadcast["tts"]["spoken"] is True
        assert first_broadcast["tts"]["reason"] == "tts_completed"

        print("\n[test 2] 동일 상황 재방송 cooldown 적용")
        duplicate_broadcast = execute_warning_broadcast(
            event_id=created_event_id,
            ppe_type="helmet",
            zone_name="프레스 구역",
            enable_tts=True,
        )

        assert duplicate_broadcast["executed"] is False
        assert duplicate_broadcast["reason"] == "cooldown_active"
        assert "cooldown_remaining_sec" in duplicate_broadcast
        assert "tts" not in duplicate_broadcast

        print("\n[test 3] 기본 언어 English 및 전체 구역 템플릿 적용")
        save_broadcast_settings(TEST_SETTINGS_EN)
        reset_broadcast_cooldown()

        english_broadcast = execute_warning_broadcast(
            event_id="TEST_VEST_EVENT",
            ppe_type="vest",
            zone_name="자재 이동 구역",
            enable_tts=False,
        )

        assert english_broadcast["executed"] is True
        assert english_broadcast["reason"] == "broadcast_printed"
        assert english_broadcast["language"] == "en"
        assert (
            english_broadcast["message"]
            == "Workers in this area, please check your safety vest."
        )
        assert "tts" not in english_broadcast

        print("\n[test 4] 방송 OFF 설정 적용")
        save_broadcast_settings(TEST_SETTINGS_DISABLED)
        reset_broadcast_cooldown()

        disabled_broadcast = execute_warning_broadcast(
            event_id="TEST_DISABLED_EVENT",
            ppe_type="helmet",
            zone_name="프레스 구역",
            enable_tts=True,
        )

        assert disabled_broadcast["executed"] is False
        assert disabled_broadcast["reason"] == "broadcast_disabled"
        assert "tts" not in disabled_broadcast

        print("\n[test 5] TTS 실패 시 터미널 로그 fallback 처리")
        save_broadcast_settings(TEST_SETTINGS_KO)
        reset_broadcast_cooldown()

        broadcast_service.speak_message = mock_raised_tts

        failed_tts_broadcast = execute_warning_broadcast(
            event_id="TEST_TTS_FAILURE_EVENT",
            ppe_type="helmet",
            zone_name="프레스 구역",
            enable_tts=True,
        )

        assert failed_tts_broadcast["executed"] is True
        assert failed_tts_broadcast["reason"] == "broadcast_printed"
        assert (
            failed_tts_broadcast["message"]
            == "프레스 구역 작업자는 안전모 착용 상태를 확인해 주세요."
        )
        assert failed_tts_broadcast["tts"]["spoken"] is False
        assert failed_tts_broadcast["tts"]["reason"] == "tts_error"

        print("\n[test 6] candidate event 생성 시 enable_tts=False 적용")
        save_broadcast_settings(TEST_SETTINGS_KO)
        reset_broadcast_cooldown()

        silent_event = create_no_helmet_candidate_event(
            camera_id="CAM_001",
            confidence=0.88,
            source_path="test_videos/test_video1.avi",
            frame_image=None,
            model_version="helmet_yolov8n",
            enable_tts=False,
        )

        silent_event_id = silent_event["event_id"]
        created_event_ids.append(silent_event_id)
        silent_broadcast = silent_event["broadcast"]

        assert silent_broadcast["executed"] is True
        assert silent_broadcast["reason"] == "broadcast_printed"
        assert silent_broadcast["language"] == "ko"
        assert "tts" not in silent_broadcast

        delete_candidate_event(silent_event_id)

        print("\n[OK] warning broadcast service test passed.")

    finally:
        broadcast_service.speak_message = original_speak_message

        for event_id in created_event_ids:
            delete_candidate_event(event_id)

        save_broadcast_settings(original_settings)
        reset_broadcast_cooldown()


if __name__ == "__main__":
    run_test()