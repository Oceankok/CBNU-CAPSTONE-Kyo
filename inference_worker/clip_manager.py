
"""Build a local MP4 clip from decoded video frames."""

from pathlib import Path

import cv2


def create_clip(frames, output_path, fps=15.0):
    """Encode BGR frames into an MP4 file."""
    if not frames:
        raise ValueError("No frames available for clip")

    if fps <= 0:
        raise ValueError("Invalid FPS")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    height, width = frames[0].shape[:2]

    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )

    if not writer.isOpened():
        raise RuntimeError("Unable to initialize MP4 writer")

    try:
        for frame in frames:
            if frame.shape[:2] != (height, width):
                frame = cv2.resize(frame, (width, height))
            writer.write(frame)
    finally:
        writer.release()

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("Failed to create video clip")

    return output_path


from datetime import timedelta


def calculate_clip_window(
    event_start,
    event_end,
    before_seconds=2.0,
    after_seconds=2.0,
):
    """Calculate the required capture interval for an event."""
    if event_start.tzinfo is None or event_end.tzinfo is None:
        raise ValueError("Timezone-aware timestamps required")

    if event_end < event_start:
        raise ValueError("Invalid event time range")

    if before_seconds < 0 or after_seconds < 0:
        raise ValueError("Clip padding must not be negative")

    return (
        event_start - timedelta(seconds=before_seconds),
        event_end + timedelta(seconds=after_seconds),
    )

