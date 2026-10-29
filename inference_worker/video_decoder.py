
"""Decode MP4 chunk bytes into OpenCV frames."""

import os
import tempfile

import cv2


def decode_mp4(data: bytes):
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

        if not capture.isOpened():
            raise RuntimeError("Failed to open MP4 chunk")

        try:
            while True:
                success, frame = capture.read()
                if not success:
                    break

                yield frame
        finally:
            capture.release()

    finally:
        if path and os.path.exists(path):
            os.remove(path)
