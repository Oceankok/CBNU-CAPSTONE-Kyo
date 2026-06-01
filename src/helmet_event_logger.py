from pathlib import Path
import sys

from ultralytics import YOLO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from backend.services.candidate_event_service import create_no_helmet_candidate_event


MODEL_PATH = "Exp01_yolov8n_640_clean_3class-13/weights/best.pt"
SOURCE_PATH = "test_videos/test_video1.avi"

CAMERA_ID = "CAM_001"
MODEL_VERSION = "Exp01_yolov8n_640_clean_3class-13"

PERSON_CLASS_NAME = "person"
HELMET_CLASS_NAME = "helmet"
NO_HELMET_CLASS_NAME = "no_helmet"

CONF_THRESHOLD = 0.25


def validate_input_paths():
    model_path = Path(MODEL_PATH)
    source_path = Path(SOURCE_PATH)

    if not model_path.exists():
        raise FileNotFoundError(
            f"학습된 안전모 모델을 찾을 수 없습니다: {MODEL_PATH}"
        )

    if not source_path.exists():
        raise FileNotFoundError(
            f"분석할 입력 파일을 찾을 수 없습니다: {SOURCE_PATH}"
        )


def count_detections(result, names):
    person_count = 0
    helmet_count = 0
    no_helmet_count = 0
    max_no_helmet_confidence = 0.0

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

    return person_count, helmet_count, no_helmet_count, max_no_helmet_confidence


def has_no_helmet_candidate(person_count, helmet_count, no_helmet_count):
    if no_helmet_count > 0:
        return True

    return person_count - helmet_count > 0


def get_candidate_confidence(no_helmet_count, max_no_helmet_confidence):
    if no_helmet_count > 0:
        return max_no_helmet_confidence

    return 0.50


def save_no_helmet_event(result, source_name, confidence, person_index):
    # bbox가 그려진 프레임을 백엔드 서비스에 넘김
    annotated_frame = result.plot()

    saved_event = create_no_helmet_candidate_event(
        camera_id=CAMERA_ID,
        confidence=confidence,
        source_path=str(source_name),
        frame_image=annotated_frame,
        model_version=MODEL_VERSION,
        enable_tts=True,
    )

    print("\n[후보 이벤트 저장 완료]")
    print(f"작업자 {person_index} / confidence={confidence:.2f}")
    print(f"event_id={saved_event.get('event_id')}")
    print(f"thumbnail_path={saved_event.get('thumbnail_path')}")
    print(f"video_clip_path={saved_event.get('video_clip_path')}")
    print(f"event_status={saved_event.get('event_status')}")
    print(f"broadcast={saved_event.get('broadcast')}")


def main():
    validate_input_paths()

    model = YOLO(MODEL_PATH)

    results = model.predict(
        source=SOURCE_PATH,
        save=True,
        conf=CONF_THRESHOLD
    )

    for result in results:
        source_name = Path(result.path).name
        names = model.names

        person_count, helmet_count, no_helmet_count, max_confidence = count_detections(
            result=result,
            names=names
        )

        print(f"\n파일: {source_name}")
        print(f"Person 탐지 수: {person_count}")
        print(f"helmet 탐지 수: {helmet_count}")
        print(f"no_helmet 탐지 수: {no_helmet_count}")

        if has_no_helmet_candidate(
            person_count=person_count,
            helmet_count=helmet_count,
            no_helmet_count=no_helmet_count
        ):
            confidence = get_candidate_confidence(
                no_helmet_count=no_helmet_count,
                max_no_helmet_confidence=max_confidence
            )

            print("안전모 미착용 후보 감지")

            save_no_helmet_event(
                result=result,
                source_name=source_name,
                confidence=confidence,
                person_index=1
            )

            print("\n이벤트 1건 저장 후 분석을 종료합니다.")
            return

        print("안전모 미착용 후보 없음")

    print("\n안전모 미착용 이벤트가 없어 저장하지 않았습니다.")


if __name__ == "__main__":
    main()