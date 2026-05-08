from datetime import datetime
from pathlib import Path
import sys
from uuid import uuid4

from ultralytics import YOLO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from backend.db.event_repository import insert_candidate_event


MODEL_PATH = "runs/detect/helmet_yolov8n/weights/best.pt"

# 이미지 테스트용
# SOURCE_PATH = "test_images"

# 영상 테스트용
SOURCE_PATH = "test_videos/test_video1.avi"

CAMERA_ID = "CAM_001"
MODEL_VERSION = "helmet_yolov8n"

PERSON_CLASS_NAME = "Person"
HELMET_CLASS_NAME = "helmet"
NO_HELMET_CLASS_NAME = "no_helmet"


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_event_id():
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_id = uuid4().hex[:6]
    return f"EVT_{timestamp}_{short_id}"


def save_no_helmet_event(source_name, confidence, person_index):
    event_id = make_event_id()
    timestamp = now_str()

    event = {
        "event_id": event_id,
        "camera_id": CAMERA_ID,
        "tracking_id": f"TRK_{event_id}",
        "ppe_type": "helmet",
        "timestamp_start": timestamp,
        "timestamp_end": timestamp,
        "duration_sec": 0,
        "frame_sample_count": 1,
        "thumbnail_path": None,
        "video_clip_path": str(source_name),
        "ai_confidence": float(confidence),
        "person_detected": 1,
        "ppe_detected": 0,
        "model_version": MODEL_VERSION,
        "event_status": "pending",
    }

    insert_candidate_event(event)
    print(f"DB 저장 완료: {event_id} / 작업자 {person_index} / confidence={confidence:.2f}")


def main():
    model_path = Path(MODEL_PATH)

    if not model_path.exists():
        raise FileNotFoundError(
            f"학습된 안전모 모델을 찾을 수 없습니다: {MODEL_PATH}"
        )

    model = YOLO(MODEL_PATH)

    results = model.predict(
        source=SOURCE_PATH,
        save=True,
        conf=0.25
    )

    event_saved = False

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

        if event_saved:
            print("이미 이벤트를 저장했으므로 추가 저장하지 않음")
            continue

        if no_helmet_count > 0:
            print(f"no_helmet 직접 탐지: {no_helmet_count}건")

            save_no_helmet_event(
                source_name=source_name,
                confidence=max_no_helmet_confidence,
                person_index=1
            )

            event_saved = True
            continue

        print("no_helmet 직접 탐지 없음")

        missing_helmet_count = person_count - helmet_count

        if missing_helmet_count > 0:
            print(f"보조 규칙 적용: 안전모 미착용 후보 {missing_helmet_count}명")

            save_no_helmet_event(
                source_name=source_name,
                confidence=0.50,
                person_index=1
            )

            event_saved = True
        else:
            print("보조 규칙 기준 안전모 미착용 후보 없음")

    print("\n안전모 미착용 이벤트 저장 처리 완료")


if __name__ == "__main__":
    main()