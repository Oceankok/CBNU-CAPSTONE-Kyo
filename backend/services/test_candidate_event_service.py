"""
candidate_event_service 수동 테스트 스크립트.

실행 전:
    python backend/db/init_db.py

실행:
    python backend/services/test_candidate_event_service.py
"""

from pathlib import Path
import sys

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from backend.services.candidate_event_service import create_no_helmet_candidate_event


def main() -> None:
    test_image = np.zeros((360, 640, 3), dtype=np.uint8)

    cv2.putText(
        test_image,
        "NO HELMET TEST EVENT",
        (80, 180),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (255, 255, 255),
        2,
    )

    result = create_no_helmet_candidate_event(
        camera_id="CAM_001",
        confidence=0.88,
        source_path="test_videos/test_video1.avi",
        frame_image=test_image,
        model_version="helmet_yolov8n_test",
    )

    print("[candidate_event_service test]")
    print(result)


if __name__ == "__main__":
    main()