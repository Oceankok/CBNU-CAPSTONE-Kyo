"""TTS 서비스 수동 테스트 스크립트."""

from backend.services.tts_service import (
    list_available_voices,
    speak_message,
)


def run_test() -> None:
    print("\n[available voices]")

    voices = list_available_voices()

    for index, voice in enumerate(voices, start=1):
        print(f"{index}. {voice}")

    print("\n[test 1] 한국어 TTS 출력")
    korean_result = speak_message(
        "해당 작업 구역의 작업자는 안전모 착용 상태를 확인해 주세요.",
        "ko",
    )
    print(korean_result)

    if not korean_result["spoken"]:
        raise RuntimeError(
            "한국어 음성 출력에 실패했습니다. "
            "Windows에 한국어 음성이 설치되어 있는지 확인해 주세요."
        )

    print("\n[test 2] 영어 TTS 출력")
    english_result = speak_message(
        "Workers in this area, please check your helmet.",
        "en",
    )
    print(english_result)

    if not english_result["spoken"]:
        raise RuntimeError(
            "영어 음성 출력에 실패했습니다. "
            "Windows에 영어 음성이 설치되어 있는지 확인해 주세요."
        )

    print("\n[OK] TTS service test passed.")


if __name__ == "__main__":
    run_test()