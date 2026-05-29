"""
candidate_event_service.py

AI 탐지 결과를 candidate_event DB 구조에 맞게 변환하고 저장하는 서비스 파일.

역할:
- 후보 이벤트 ID 생성
- 이벤트 썸네일 저장
- 영상 또는 참조 클립 경로 정리
- candidate_event dict 생성
- DB 저장 함수 호출
- 후보 이벤트 저장 후 경고 방송 실행

이 서비스는 AI 탐지 코드에서 직접 DB 구조를 다루지 않도록 하기 위한 연결 계층이다.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union
from uuid import uuid4

import cv2

from backend.db.event_repository import (
    get_candidate_event_by_id,
    insert_candidate_event,
)
from backend.services.warning_broadcast_service import execute_warning_broadcast


PROJECT_ROOT = Path(__file__).resolve().parents[2]

MEDIA_ROOT = PROJECT_ROOT / "storage" / "candidate_events"
THUMBNAIL_DIR = MEDIA_ROOT / "thumbnails"
CLIP_DIR = MEDIA_ROOT / "clips"

DEFAULT_MODEL_VERSION = "helmet_yolov8n"


def _now_str() -> str:
    """
    현재 시간을 DB 저장용 문자열로 반환함.

    Returns:
        str:
            예: "2026-05-18 14:30:00"
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _make_event_id() -> str:
    """
    후보 이벤트 ID를 생성함.

    Returns:
        str:
            예: "EVT_20260518143000_a1b2c3"
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_id = uuid4().hex[:6]

    return f"EVT_{timestamp}_{short_id}"


def _ensure_media_dirs() -> None:
    """
    이벤트 미디어 저장 폴더를 생성함.
    """
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    CLIP_DIR.mkdir(parents=True, exist_ok=True)


def _to_relative_path(path: Path) -> str:
    """
    프로젝트 루트 기준 상대 경로 문자열로 변환함.

    Args:
        path (Path):
            변환할 경로.

    Returns:
        str:
            프로젝트 루트 기준 상대 경로.
    """
    return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")


def _save_thumbnail(event_id: str, frame_image: Optional[Any]) -> str:
    """
    이벤트 썸네일 이미지를 저장함.

    Args:
        event_id (str):
            후보 이벤트 ID.
        frame_image (Optional[Any]):
            OpenCV 이미지 배열. 일반적으로 YOLO result.orig_img 사용.

    Returns:
        str:
            저장된 썸네일의 프로젝트 루트 기준 상대 경로.
            이미지가 없거나 저장에 실패하면 빈 문자열 반환.
    """
    if frame_image is None:
        return ""

    _ensure_media_dirs()

    thumbnail_path = THUMBNAIL_DIR / f"{event_id}.jpg"
    success = cv2.imwrite(str(thumbnail_path), frame_image)

    if not success:
        return ""

    return _to_relative_path(thumbnail_path)


def _normalize_media_path(
    source_path: Optional[Union[str, Path]],
) -> str:
    """
    영상 또는 참조 파일 경로를 프로젝트 루트 기준 상대 경로로 정리함.

    Args:
        source_path (Optional[Union[str, Path]]):
            원본 영상, 추론 결과 영상, 또는 참조 클립 경로.

    Returns:
        str:
            정리된 경로 문자열.
    """
    if source_path is None:
        return ""

    path = Path(source_path)

    if not path.is_absolute():
        return str(path).replace("\\", "/")

    try:
        return _to_relative_path(path)
    except ValueError:
        return str(path).replace("\\", "/")


def create_no_helmet_candidate_event(
    *,
    camera_id: str,
    confidence: float,
    source_path: Optional[Union[str, Path]] = None,
    frame_image: Optional[Any] = None,
    model_version: str = DEFAULT_MODEL_VERSION,
    timestamp_start: Optional[str] = None,
    timestamp_end: Optional[str] = None,
    duration_sec: int = 0,
    frame_sample_count: int = 1,
    tracking_id: Optional[str] = None,
    enable_tts: bool = True,
) -> dict[str, Any]:
    """
    안전모 미착용 후보 이벤트를 생성하고 DB에 저장한 뒤 경고 방송을 실행함.

    Args:
        camera_id (str):
            이벤트가 발생한 카메라 ID.
        confidence (float):
            AI 탐지 신뢰도.
        source_path (Optional[Union[str, Path]]):
            원본 영상, 추론 결과 영상, 또는 참조 클립 경로.
        frame_image (Optional[Any]):
            썸네일로 저장할 프레임 이미지.
        model_version (str):
            사용한 AI 모델 버전.
        timestamp_start (Optional[str]):
            이벤트 시작 시각. 없으면 현재 시각 사용.
        timestamp_end (Optional[str]):
            이벤트 종료 시각. 없으면 시작 시각과 동일하게 사용.
        duration_sec (int):
            이벤트 지속 시간.
        frame_sample_count (int):
            이벤트 판단에 사용한 프레임 수.
        tracking_id (Optional[str]):
            외부 추적 ID. 없으면 event_id 기반으로 생성.
        enable_tts (bool):
            True이면 경고 방송 메시지를 실제 음성으로 출력함.
            False이면 터미널 로그만 출력함.

    Returns:
        dict[str, Any]:
            생성된 후보 이벤트 정보와 경고 방송 실행 결과.
    """
    event_id = _make_event_id()
    now = _now_str()

    start_time = timestamp_start or now
    end_time = timestamp_end or start_time

    thumbnail_path = _save_thumbnail(event_id, frame_image)
    video_clip_path = _normalize_media_path(source_path)

    event = {
        "event_id": event_id,
        "camera_id": camera_id,
        "tracking_id": tracking_id or f"TRK_{event_id}",
        "ppe_type": "helmet",
        "timestamp_start": start_time,
        "timestamp_end": end_time,
        "duration_sec": duration_sec,
        "frame_sample_count": frame_sample_count,
        "thumbnail_path": thumbnail_path,
        "video_clip_path": video_clip_path,
        "ai_confidence": float(confidence),
        "person_detected": 1,
        "ppe_detected": 0,
        "model_version": model_version,
        "event_status": "pending",
    }

    insert_candidate_event(event)

    saved_event = get_candidate_event_by_id(event_id)
    zone_name = ""

    if saved_event is not None:
        zone_name = saved_event.get("zone_name") or ""

    broadcast_result = execute_warning_broadcast(
        event_id=event_id,
        ppe_type=event["ppe_type"],
        zone_name=zone_name,
        enable_tts=enable_tts,
    )

    return {
        "event_id": event_id,
        "thumbnail_path": thumbnail_path,
        "video_clip_path": video_clip_path,
        "event_status": "pending",
        "broadcast": broadcast_result,
    }