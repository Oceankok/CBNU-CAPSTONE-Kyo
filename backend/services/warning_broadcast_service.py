"""
warning_broadcast_service.py

경고 방송 설정을 조회하고, PPE 미착용 후보 이벤트에 대한
터미널 기반 경고 방송을 실행하는 서비스 파일.

현재 단계의 역할:
- 경고 방송 사용 여부 확인
- PPE 유형/구역/언어에 맞는 메시지 선택
- cooldown 기반 중복 방송 방지
- 터미널 기반 방송 시뮬레이션
- 방송 실행 결과 dict 반환

실제 TTS 음성 출력은 후속 단계에서 확장한다.
"""

import math
from datetime import datetime
from time import monotonic
from typing import Any, Optional, Tuple

from backend.db.event_repository import get_broadcast_settings


_last_broadcast_at: dict[Tuple[str, str, str], float] = {}


def _now_str() -> str:
    """
    현재 시간을 로그 반환용 문자열로 생성함.
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _select_broadcast_message(
    templates: list[dict[str, Any]],
    ppe_type: str,
    zone_name: str,
    language: str,
) -> Optional[str]:
    """
    PPE 유형, 구역, 언어에 맞는 경고 메시지를 선택함.

    선택 우선순위:
    1. PPE 유형 + 특정 구역 + 언어가 모두 일치하는 템플릿
    2. PPE 유형 + 전체 구역("") + 언어가 일치하는 템플릿

    Args:
        templates (list[dict[str, Any]]):
            저장된 경고 방송 메시지 템플릿 목록.
        ppe_type (str):
            PPE 유형. 예: "helmet", "vest"
        zone_name (str):
            이벤트 발생 구역명.
        language (str):
            사용할 방송 언어. 예: "ko", "en"

    Returns:
        Optional[str]:
            선택된 메시지. 사용할 템플릿이 없으면 None.
    """
    candidate_zones = [zone_name]

    if zone_name != "":
        candidate_zones.append("")

    for target_zone in candidate_zones:
        for template in templates:
            if (
                template["ppe_type"] == ppe_type
                and template["zone_name"] == target_zone
                and template["language"] == language
            ):
                return str(template["message"])

    return None


def _print_broadcast_result(result: dict[str, Any]) -> None:
    """
    방송 실행 결과를 터미널에 로그 형태로 출력함.

    Args:
        result (dict[str, Any]):
            방송 실행 결과.
    """
    title = "[WARNING BROADCAST]" if result["executed"] else "[WARNING BROADCAST SKIPPED]"

    print(f"\n{title}")
    print(f"event_id={result['event_id']}")
    print(f"result={result['reason']}")
    print(f"ppe_type={result['ppe_type']}")
    print(f"zone_name={result['zone_name']}")
    print(f"language={result['language']}")

    if result["message"] != "":
        print(f"message={result['message']}")

    if "cooldown_remaining_sec" in result:
        print(f"cooldown_remaining_sec={result['cooldown_remaining_sec']}")

    print(f"executed_at={result['executed_at']}")


def reset_broadcast_cooldown() -> None:
    """
    메모리에 저장된 cooldown 상태를 초기화함.

    Notes:
        수동 테스트 및 추후 반복 시연 시 사용함.
    """
    _last_broadcast_at.clear()


def execute_warning_broadcast(
    *,
    ppe_type: str,
    zone_name: str = "",
    event_id: Optional[str] = None,
    language: Optional[str] = None,
) -> dict[str, Any]:
    """
    경고 방송 설정에 따라 터미널 기반 경고 방송을 실행함.

    Args:
        ppe_type (str):
            미착용이 감지된 PPE 유형.
        zone_name (str):
            이벤트 발생 구역명.
        event_id (Optional[str]):
            연결된 후보 이벤트 ID.
        language (Optional[str]):
            강제로 사용할 언어. 지정하지 않으면 기본 방송 언어 사용.

    Returns:
        dict[str, Any]:
            방송 실행 여부, 생략 사유, 메시지, 실행 시각 등을 포함한 결과.
    """
    settings = get_broadcast_settings()
    selected_language = language or settings.get("default_language", "ko")
    executed_at = _now_str()

    base_result = {
        "event_id": event_id or "",
        "ppe_type": ppe_type,
        "zone_name": zone_name,
        "language": selected_language,
        "message": "",
        "executed_at": executed_at,
    }

    if not settings.get("enabled", False):
        result = {
            **base_result,
            "executed": False,
            "reason": "broadcast_disabled",
        }
        _print_broadcast_result(result)
        return result

    message = _select_broadcast_message(
        templates=settings.get("templates", []),
        ppe_type=ppe_type,
        zone_name=zone_name,
        language=selected_language,
    )

    if message is None:
        result = {
            **base_result,
            "executed": False,
            "reason": "template_not_found",
        }
        _print_broadcast_result(result)
        return result

    cooldown_sec = max(int(settings.get("cooldown_sec", 0)), 0)
    cooldown_key = (ppe_type, zone_name, selected_language)
    current_time = monotonic()
    previous_time = _last_broadcast_at.get(cooldown_key)

    if previous_time is not None:
        elapsed_sec = current_time - previous_time

        if elapsed_sec < cooldown_sec:
            result = {
                **base_result,
                "message": message,
                "executed": False,
                "reason": "cooldown_active",
                "cooldown_remaining_sec": math.ceil(cooldown_sec - elapsed_sec),
            }
            _print_broadcast_result(result)
            return result

    _last_broadcast_at[cooldown_key] = current_time

    result = {
        **base_result,
        "message": message,
        "executed": True,
        "reason": "broadcast_printed",
    }

    _print_broadcast_result(result)
    return result