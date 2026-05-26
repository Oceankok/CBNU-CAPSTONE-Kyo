from datetime import datetime
from pathlib import Path
import sys
from uuid import uuid4

import cv2
from ultralytics import YOLO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from backend.db.event_repository import insert_candidate_event


MODEL_PATH = "runs/detect/helmet_yolov8n/weights/best.pt"
SOURCE_PATH = "test_videos/test_video1.avi"

CAMERA_ID = "CAM_001"
MODEL_VERSION = "helmet_yolov8n"

PERSON_CLASS_NAME = "Person"
HELMET_CLASS_NAME = "helmet"
NO_HELMET_CLASS_NAME = "no_helmet"

THUMBNAIL_DIR = "backend/media/event_thumbnails"


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_event_id():
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_id = uuid4().hex[:6]
    return f"EVT_{timestamp}_{short_id}"


def save_event_thumbnail(result, event_id):
    thumbnail_dir = Path(THUMBNAIL_DIR)
    thumbnail_dir.mkdir(parents=True, exist_ok=True)

    thumbnail_path = thumbnail_dir / f"{event_id}.jpg"

    # bbox가 그려진 프레임 이미지 생성
    annotated_frame = result.plot()

    cv2.imwrite(str(thumbnail_path), annotated_frame)

    return str(thumbnail_path)


def save_no_helmet_event(result, source_name, confidence, person_index):
    event_id = make_event_id()
    timestamp = now_str()

    thumbnail_path = save_event_thumbnail(result, event_id)

    event = {
        "event_id": event_id,
        "camera_id": CAMERA_ID,
        "tracking_id": f"TRK_{event_id}",
        "ppe_type": "helmet",
        "timestamp_start": timestamp,
        "timestamp_end": timestamp,
        "duration_sec": 0,
        "frame_sample_count": 1,
        "thumbnail_path": thumbnail_path,
        "video_clip_path": str(source_name),
        "ai_confidence": float(confidence),
        "person_detected": 1,
        "ppe_detected": 0,
        "model_version": MODEL_VERSION,
        "event_status": "pending",
    }

    insert_candidate_event(event)

    print(f"DB 저장 완료: {event_id}")
    print(f"작업자 {person_index} / confidence={confidence:.2f}")
    print(f"이벤트 캡처 저장: {thumbnail_path}")


def main():
    model_path = Path(MODEL_PATH)

    if not model_path.exists():
        raise FileNotFoundError(
            f"학습된 안전모 모델을 찾을 수 없습니다: {MODEL_PATH}"
        )

    source_path = Path(SOURCE_PATH)

    if not source_path.exists():
        raise FileNotFoundError(
            f"분석할 입력 파일을 찾을 수 없습니다: {SOURCE_PATH}"
        )

    model = YOLO(MODEL_PATH)

    results = model.predict(
        source=SOURCE_PATH,
        save=True,
        conf=0.25
    )

    for result in results:
        source_name = Path(result.path).name
        names = model.names

        person_count = 0
        helmet_count = 0
        no_helmet_count = 0
        max_no_helmet_confidence = 0.0

        print(f"\n파일: {source_name}")

        for box in result.boxes:
            cls_id = int(box.cls[0])
            cls_name = names[cls_id]
            confidence = float(box.conf[0])

            if cls_name == PERSON_CLASS_NAME:
                person_count += 1

            elif cls_name == HELMET_CLASS_NAME:
                helmet_count += 1

            elif cls_name == NO_HELMET_CLASS_NAME:
                no_helmet_count += 1
                max_no_helmet_confidence = max(max_no_helmet_confidence, confidence)

        print(f"Person 탐지 수: {person_count}")
        print(f"helmet 탐지 수: {helmet_count}")
        print(f"no_helmet 탐지 수: {no_helmet_count}")

        if no_helmet_count > 0:
            print(f"no_helmet 직접 탐지: {no_helmet_count}건")

            save_no_helmet_event(
                result=result,
                source_name=source_name,
                confidence=max_no_helmet_confidence,
                person_index=1
            )

            print("\n이벤트 1건 저장 후 분석을 종료합니다.")
            return

        print("no_helmet 직접 탐지 없음")

        missing_helmet_count = person_count - helmet_count

        if missing_helmet_count > 0:
            print(f"보조 규칙 적용: 안전모 미착용 후보 {missing_helmet_count}명")

            save_no_helmet_event(
                result=result,
                source_name=source_name,
                confidence=0.50,
                person_index=1
            )

            print("\n이벤트 1건 저장 후 분석을 종료합니다.")
            return

        print("보조 규칙 기준 안전모 미착용 후보 없음")

    print("\n안전모 미착용 이벤트가 없어 저장하지 않았습니다.")


if __name__ == "__main__":
    main()