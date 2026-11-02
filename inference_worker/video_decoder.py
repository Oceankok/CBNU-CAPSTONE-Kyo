
"""Decode MP4 chunk bytes into OpenCV frames."""

import math
import os
import tempfile

import cv2


def decode_mp4(data: bytes, *, with_timing=False, duration_ms=None):
    """Yield BGR frames from an MP4 chunk."""
    if not data:
        raise ValueError("Empty MP4 chunk")

    path = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".mp4", delete=False
        ) as temp:
            temp.write(data)
            path = temp.name

        capture = cv2.VideoCapture(path)

        try:
            if not capture.isOpened():
                raise RuntimeError("Failed to open MP4 chunk")
            if with_timing:
                fps = capture.get(cv2.CAP_PROP_FPS)
                expected = capture.get(cv2.CAP_PROP_FRAME_COUNT)
                if not math.isfinite(fps) or not 5 <= fps <= 60 or not math.isfinite(expected) or not 1 <= expected <= 180:
                    raise RuntimeError("Invalid chunk frame metadata")
                if duration_ms is not None and abs(expected/fps*1000-duration_ms) > 250:
                    raise RuntimeError("Chunk duration mismatch")
                if duration_ms is not None:
                    fps = expected/(duration_ms/1000)  # Preserve the declared capture timeline.
            index = 0
            while True:
                success, frame = capture.read()
                if not success:
                    break

                if with_timing and (index >= 180 or frame.shape[0]*frame.shape[1] > 1920*1080):
                    raise RuntimeError("Chunk frame limits exceeded")
                yield (index, fps, frame) if with_timing else frame
                index += 1
            if with_timing and index != int(expected):
                raise RuntimeError("Incomplete chunk decode")
        finally:
            capture.release()

    finally:
        if path and os.path.exists(path):
            os.remove(path)
