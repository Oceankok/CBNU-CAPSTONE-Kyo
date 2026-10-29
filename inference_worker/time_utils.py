
"""Convert server chunk capture times into frame timestamps."""

from datetime import datetime, timedelta


def parse_capture_time(value):
    """Parse an ISO-8601 capture timestamp with a timezone."""
    if not isinstance(value, str):
        raise ValueError("Capture time must be a string")

    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Capture time must contain timezone information")

    return timestamp


def frame_timestamp(capture_start_at, frame_index, fps):
    """Calculate the absolute capture time of a frame."""
    if frame_index < 0 or fps <= 0:
        raise ValueError("Invalid frame index or FPS")

    start = (
        parse_capture_time(capture_start_at)
        if isinstance(capture_start_at, str)
        else capture_start_at
    )

    if not isinstance(start, datetime) or start.tzinfo is None:
        raise ValueError("Timezone-aware datetime required")

    return start + timedelta(seconds=frame_index / fps)


def is_continuous_chunk(
    previous_start,
    previous_duration_ms,
    current_start,
    max_gap_seconds=0.25,
):
    """Check whether two chunks have a continuous capture timeline."""
    if not isinstance(previous_duration_ms, (int, float)) or previous_duration_ms <= 0:
        raise ValueError("Invalid previous chunk duration")

    previous = (
        parse_capture_time(previous_start)
        if isinstance(previous_start, str)
        else previous_start
    )
    current = (
        parse_capture_time(current_start)
        if isinstance(current_start, str)
        else current_start
    )

    if (
        not isinstance(previous, datetime)
        or not isinstance(current, datetime)
        or previous.tzinfo is None
        or current.tzinfo is None
        or previous.utcoffset() is None
        or current.utcoffset() is None
    ):
        raise ValueError("Timezone-aware timestamps required")

    expected_start = previous + timedelta(milliseconds=previous_duration_ms)
    gap = (current - expected_start).total_seconds()

    return abs(gap) <= max_gap_seconds

