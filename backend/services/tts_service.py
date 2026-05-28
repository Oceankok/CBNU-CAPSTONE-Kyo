"""
tts_service.py

경고 방송 메시지를 실제 음성으로 출력하는 TTS 서비스 파일.

역할:
- 시스템에 설치된 음성 목록 조회
- 언어별 사용 가능한 음성 선택
- 경고 메시지 음성 출력
- 출력 결과 dict 반환

현재 구현은 pyttsx3를 사용하며, Windows에서는 설치된 시스템 음성을 사용함.
"""

from typing import Any, Optional


VOICE_KEYWORDS = {
    "ko": ["ko-kr", "korean", "heami", "sunhi", "한국"],
    "en": ["en-us", "english", "david", "zira", "aria", "guy"],
}


def _load_pyttsx3() -> Any:
    """
    pyttsx3 모듈을 로드함.

    Returns:
        Any:
            pyttsx3 모듈.

    Raises:
        RuntimeError:
            pyttsx3가 설치되어 있지 않은 경우.
    """
    try:
        import pyttsx3
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "pyttsx3 is not installed. Run: pip install pyttsx3"
        ) from exc

    return pyttsx3


def _get_voice_description(voice: Any) -> str:
    """
    음성 객체의 검색용 설명 문자열을 생성함.

    Args:
        voice (Any):
            pyttsx3 voice 객체.

    Returns:
        str:
            음성 ID, 이름, 언어 정보를 합친 소문자 문자열.
    """
    voice_id = str(getattr(voice, "id", ""))
    voice_name = str(getattr(voice, "name", ""))
    voice_languages = " ".join(
        str(language) for language in getattr(voice, "languages", [])
    )

    return f"{voice_id} {voice_name} {voice_languages}".lower()


def _select_voice(voices: list[Any], language: str) -> Optional[Any]:
    """
    요청 언어에 맞는 시스템 음성을 선택함.

    Args:
        voices (list[Any]):
            시스템에 설치된 음성 목록.
        language (str):
            방송 언어. "ko" 또는 "en".

    Returns:
        Optional[Any]:
            선택된 음성 객체. 찾지 못하면 None.
    """
    keywords = VOICE_KEYWORDS.get(language, [])

    for voice in voices:
        description = _get_voice_description(voice)

        if any(keyword in description for keyword in keywords):
            return voice

    return None


def list_available_voices() -> list[dict[str, str]]:
    """
    시스템에서 사용할 수 있는 음성 목록을 반환함.

    Returns:
        list[dict[str, str]]:
            음성 ID, 이름, 언어 정보 목록.
    """
    pyttsx3 = _load_pyttsx3()
    engine = pyttsx3.init()

    try:
        voices = engine.getProperty("voices")

        return [
            {
                "id": str(getattr(voice, "id", "")),
                "name": str(getattr(voice, "name", "")),
                "languages": str(getattr(voice, "languages", [])),
            }
            for voice in voices
        ]
    finally:
        engine.stop()


def speak_message(message: str, language: str) -> dict[str, Any]:
    """
    경고 방송 메시지를 실제 음성으로 출력함.

    Args:
        message (str):
            음성으로 출력할 경고 메시지.
        language (str):
            방송 언어. "ko" 또는 "en".

    Returns:
        dict[str, Any]:
            음성 출력 성공 여부와 사용 음성 정보.
    """
    pyttsx3 = _load_pyttsx3()
    engine = pyttsx3.init()

    try:
        voices = engine.getProperty("voices")
        selected_voice = _select_voice(voices, language)

        if selected_voice is None:
            return {
                "spoken": False,
                "reason": "voice_not_found",
                "language": language,
                "message": message,
            }

        engine.setProperty("voice", selected_voice.id)
        engine.setProperty("rate", 170)

        engine.say(message)
        engine.runAndWait()

        return {
            "spoken": True,
            "reason": "tts_completed",
            "language": language,
            "message": message,
            "voice_id": str(selected_voice.id),
            "voice_name": str(getattr(selected_voice, "name", "")),
        }

    except Exception as exc:
        return {
            "spoken": False,
            "reason": "tts_error",
            "language": language,
            "message": message,
            "error": str(exc),
        }

    finally:
        engine.stop()