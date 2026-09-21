"""Compatibility entry point for file-based helmet event detection.

Video files now use the same person-PPE matching, tracking and asynchronous
event path as the real-time monitor instead of frame-wide object counts.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))
from src.realtime_helmet_monitor import main as run_monitor


DEFAULT_MODEL = "Exp01_yolov8n_640_clean_3class-13/weights/best.pt"
DEFAULT_SOURCE = "test_videos/test_video1.avi"


def main() -> None:
    run_monitor(
        [
            "--model",
            DEFAULT_MODEL,
            "--source",
            DEFAULT_SOURCE,
            "--no-display",
            *sys.argv[1:],
        ]
    )


if __name__ == "__main__":
    main()
