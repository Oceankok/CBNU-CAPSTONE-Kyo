"""Create an event and broadcast request before processing its private media."""

from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union
from uuid import uuid4

from backend.db.event_repository import get_candidate_event_by_id, insert_candidate_event
from backend.field.service import enqueue_event_broadcast
from backend.media.service import ingest_frame, ingest_file

DEFAULT_MODEL_VERSION = "helmet_yolov8n"


def create_no_helmet_candidate_event(
    *, camera_id: str, confidence: float,
    source_path: Optional[Union[str, Path]] = None, frame_image: Optional[Any] = None,
    model_version: str = DEFAULT_MODEL_VERSION,
    timestamp_start: Optional[str] = None, timestamp_end: Optional[str] = None,
    duration_sec: int = 0, frame_sample_count: int = 1,
    tracking_id: Optional[str] = None, enable_tts: bool = True,
) -> dict[str, Any]:
    """Save metadata, request field audio, then redact supplied image/short clip.

    The caller's source file is input, not owned storage, and is never deleted.
    Media failures remain visible as failed records and never expose raw output.
    Processing is synchronous in this first capstone implementation; broadcasting
    has already been queued independently before video conversion starts.
    """
    now = datetime.now()
    event_id = f"EVT_{now:%Y%m%d%H%M%S}_{uuid4().hex[:6]}"
    start = timestamp_start or now.strftime("%Y-%m-%d %H:%M:%S")
    insert_candidate_event({
        "event_id": event_id, "camera_id": camera_id,
        "tracking_id": tracking_id or f"TRK_{event_id}", "ppe_type": "helmet",
        "timestamp_start": start, "timestamp_end": timestamp_end or start,
        "duration_sec": duration_sec, "frame_sample_count": frame_sample_count,
        "thumbnail_path": "", "video_clip_path": "", "ai_confidence": float(confidence),
        "person_detected": 1, "ppe_detected": 0, "model_version": model_version,
        "event_status": "pending",
    })
    broadcast = enqueue_event_broadcast(event_id) if enable_tts else {"queued": False, "reason": "broadcast_suppressed"}
    media = []
    if frame_image is not None:
        media.append(ingest_frame(event_id, camera_id, frame_image))
    if source_path is not None:
        media.append(ingest_file(event_id, camera_id, "video", "clip", Path(source_path)))
    saved = get_candidate_event_by_id(event_id)
    return {
        "event_id": event_id, "thumbnail_path": saved["thumbnail_path"],
        "video_clip_path": saved["video_clip_path"], "event_status": "pending",
        "media": media, "broadcast": broadcast,
    }
