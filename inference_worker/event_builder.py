
"""Build validated event payloads without sending HTTP requests."""

from datetime import datetime, timezone
from math import isfinite
from uuid import uuid4


def build_event(
    camera_id,
    tracking_id,
    start_time,
    end_time,
    confidence,
    frame_sample_count,
    model_version="helmet-yolov8n-v1",
    event_id=None,
):
    if (
        not isinstance(start_time, datetime)
        or not isinstance(end_time, datetime)
        or start_time.tzinfo is None
        or end_time.tzinfo is None
        or start_time.utcoffset() is None
        or end_time.utcoffset() is None
    ):
        raise ValueError("Timezone-aware datetimes are required")

    duration = (end_time - start_time).total_seconds()

    if not 0 <= duration <= 3600:
        raise ValueError("Invalid observation duration")

    if confidence is None or not isfinite(float(confidence)):
        raise ValueError("Verified PPE confidence is required")

    if not 0 <= float(confidence) <= 1:
        raise ValueError("Confidence must be between 0 and 1")

    if not 1 <= frame_sample_count <= 100000:
        raise ValueError("Invalid frame sample count")

    return {
        "event_id": event_id or f"EVT_{uuid4().hex}",
        "camera_id": camera_id,
        "tracking_id": tracking_id,
        "ppe_type": "helmet",
        "timestamp_start": start_time.astimezone(timezone.utc).isoformat(),
        "timestamp_end": end_time.astimezone(timezone.utc).isoformat(),
        "confidence": float(confidence),
        "frame_sample_count": frame_sample_count,
        "model_version": model_version,
    }
