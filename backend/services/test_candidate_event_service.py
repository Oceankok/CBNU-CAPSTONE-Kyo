"""
candidate_event_service 수동 테스트 스크립트.

실행 전:
    python backend/db/init_db.py

실행:
    python -m backend.services.test_candidate_event_service
"""

from pathlib import Path

import cv2
import numpy as np

from backend.db.event_repository import delete_candidate_event
from backend.services.candidate_event_service import (
    PROJECT_ROOT,
    create_no_helmet_candidate_event,
)


def main() -> None:
    created_event_id: str | None = None
    created_thumbnail_path: Path | None = None

    try:
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
            enable_tts=False,
        )

        created_event_id = result["event_id"]
        created_thumbnail_path = PROJECT_ROOT / result["thumbnail_path"]

        assert result["event_status"] == "pending"
        assert result["thumbnail_path"] != ""
        assert result["video_clip_path"] == "test_videos/test_video1.avi"
        assert result["broadcast"]["executed"] is True
        assert "tts" not in result["broadcast"]
        assert created_thumbnail_path.exists()

        print("[PASS] 후보 이벤트 및 썸네일 저장을 확인함")
        print(result)
        print("\n[OK] candidate event service test passed.")

    finally:
        if created_event_id is not None:
            delete_candidate_event(created_event_id)

        if created_thumbnail_path is not None and created_thumbnail_path.exists():
            created_thumbnail_path.unlink()


if __name__ == "__main__":
    main()