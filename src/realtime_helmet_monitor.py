from collections import deque
from pathlib import Path
import sys
import time

import cv2
from ultralytics import YOLO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from backend.services.candidate_event_service import create_no_helmet_candidate_event


MODEL_PATH = "Exp01_yolov8n_640_clean_3class-13/weights/best.pt"

# USB 웹캠 번호
CAMERA_SOURCE = 0

CAMERA_ID = "CAM_001"
MODEL_VERSION = "Exp01_yolov8n_640_clean_3class-13"

PERSON_CLASS_NAME = "person"
HELMET_CLASS_NAME = "helmet"
NO_HELMET_CLASS_NAME = "no_helmet"

CONF_THRESHOLD = 0.35
FRAME_INTERVAL = 10

# 같은 상황에서 계속 TTS가 울리지 않게 제한
EVENT_COOLDOWN_SEC = 10

# 안전모 미착용 후보가 이 시간 이상 지속될 때만 이벤트 생성
EVENT_DURATION_THRESHOLD_SEC = 2

# 이벤트 발생 전후 클립 저장 설정
CLIP_DIR = "storage/candidate_events/clips"
CLIP_FPS = 20
CLIP_SECONDS = 5
CLIP_WIDTH = 640
CLIP_HEIGHT = 480


def validate_model_path():
    model_path = Path(MODEL_PATH)

    if not model_path.exists():
        raise FileNotFoundError(
            f"학습된 안전모 모델을 찾을 수 없습니다: {MODEL_PATH}"
        )


def calculate_iou(box_a, box_b):
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h

    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)

    union_area = area_a + area_b - inter_area

    if union_area == 0:
        return 0

    return inter_area / union_area


def is_duplicate_box(new_box, saved_boxes, iou_threshold=0.4):
    for saved_box in saved_boxes:
        if calculate_iou(new_box, saved_box) >= iou_threshold:
            return True

    return False


def count_detections(result, names):
    person_boxes = []
    helmet_count = 0
    no_helmet_count = 0
    max_no_helmet_confidence = 0.0

    for box in result.boxes:
        cls_id = int(box.cls[0])
        cls_name = names[cls_id]

        if cls_name == PERSON_CLASS_NAME:
            xyxy = box.xyxy[0].tolist()

            if not is_duplicate_box(xyxy, person_boxes, iou_threshold=0.4):
                person_boxes.append(xyxy)

        elif cls_name == HELMET_CLASS_NAME:
            helmet_count += 1

        elif cls_name == NO_HELMET_CLASS_NAME:
            confidence = float(box.conf[0])
            no_helmet_count += 1
            max_no_helmet_confidence = max(max_no_helmet_confidence, confidence)

    person_count = len(person_boxes)

    return person_count, helmet_count, no_helmet_count, max_no_helmet_confidence


def has_no_helmet_candidate(person_count, helmet_count, no_helmet_count):
    if person_count <= 0:
        return False

    if no_helmet_count > 0:
        return True

    return person_count > helmet_count


def get_candidate_confidence(no_helmet_count, max_no_helmet_confidence):
    if no_helmet_count > 0:
        return max_no_helmet_confidence

    return 0.50


def make_clip_path(event_id):
    clip_dir = Path(CLIP_DIR)
    clip_dir.mkdir(parents=True, exist_ok=True)

    return clip_dir / f"{event_id}.mp4"


def save_clip_from_buffer(frame_buffer, event_id):
    if len(frame_buffer) == 0:
        return "realtime_usb_camera"

    clip_path = make_clip_path(event_id)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(
        str(clip_path),
        fourcc,
        CLIP_FPS,
        (CLIP_WIDTH, CLIP_HEIGHT),
    )

    if not writer.isOpened():
        print("클립 저장 실패: VideoWriter를 열 수 없습니다.")
        return "realtime_usb_camera"

    for frame in frame_buffer:
        resized_frame = cv2.resize(frame, (CLIP_WIDTH, CLIP_HEIGHT))
        writer.write(resized_frame)

    writer.release()

    print(f"이벤트 클립 저장 완료: {clip_path}")
    return str(clip_path)


def save_candidate_event(frame_image, confidence, frame_buffer):
    # backend service 내부에서 event_id를 생성하므로,
    # clip 파일명 생성을 위해 임시 timestamp 기반 id를 사용함.
    temp_event_id = f"REALTIME_{int(time.time())}"
    clip_path = save_clip_from_buffer(frame_buffer, temp_event_id)

    saved_event = create_no_helmet_candidate_event(
        camera_id=CAMERA_ID,
        confidence=confidence,
        source_path=clip_path,
        frame_image=frame_image,
        model_version=MODEL_VERSION,
        enable_tts=True,
    )

    print("\n[실시간 후보 이벤트 저장 완료]")
    print(f"event_id={saved_event.get('event_id')}")
    print(f"thumbnail_path={saved_event.get('thumbnail_path')}")
    print(f"video_clip_path={saved_event.get('video_clip_path')}")
    print(f"event_status={saved_event.get('event_status')}")
    print(f"broadcast={saved_event.get('broadcast')}")


def main():
    validate_model_path()

    model = YOLO(MODEL_PATH)
    names = model.names

    cap = cv2.VideoCapture(CAMERA_SOURCE, cv2.CAP_DSHOW)

    if not cap.isOpened():
        raise RuntimeError(
            f"카메라를 열 수 없습니다. CAMERA_SOURCE={CAMERA_SOURCE}"
        )

    print("실시간 안전모 모니터링 시작")
    print("종료하려면 q 또는 ESC 키를 누르세요.")

    frame_index = 0
    last_event_time = 0
    candidate_start_time = None

    # 최근 5초 정도의 프레임을 계속 보관
    frame_buffer = deque(maxlen=CLIP_FPS * CLIP_SECONDS)

    try:
        while True:
            ret, frame = cap.read()

            if not ret:
                print("프레임을 읽지 못했습니다.")
                break

            frame_index += 1
            display_frame = frame.copy()

            # 클립 저장용 원본 프레임 버퍼
            clip_frame = cv2.resize(frame, (CLIP_WIDTH, CLIP_HEIGHT))
            frame_buffer.append(clip_frame.copy())

            if frame_index % FRAME_INTERVAL == 0:
                results = model.predict(
                    source=frame,
                    conf=CONF_THRESHOLD,
                    iou=0.45,
                    max_det=20,
                    verbose=False
                )

                result = results[0]
                display_frame = result.plot()

                person_count, helmet_count, no_helmet_count, max_confidence = count_detections(
                    result=result,
                    names=names
                )

                print(
                    f"Person={person_count}, helmet={helmet_count}, no_helmet={no_helmet_count}"
                )

                has_candidate = has_no_helmet_candidate(
                    person_count=person_count,
                    helmet_count=helmet_count,
                    no_helmet_count=no_helmet_count
                )

                now = time.time()

                if has_candidate:
                    if candidate_start_time is None:
                        candidate_start_time = now
                        print("안전모 미착용 후보 감지 시작")

                    candidate_duration = now - candidate_start_time
                    print(f"후보 지속 시간: {candidate_duration:.1f}초")

                    if candidate_duration >= EVENT_DURATION_THRESHOLD_SEC:
                        if now - last_event_time >= EVENT_COOLDOWN_SEC:
                            confidence = get_candidate_confidence(
                                no_helmet_count=no_helmet_count,
                                max_no_helmet_confidence=max_confidence
                            )

                            print("실시간 안전모 미착용 후보 지속 기준 충족")

                            save_candidate_event(
                                frame_image=display_frame,
                                confidence=confidence,
                                frame_buffer=list(frame_buffer),
                            )

                            last_event_time = now
                            candidate_start_time = None
                        else:
                            print("cooldown 적용 중: 이벤트 저장 및 방송 생략")
                else:
                    if candidate_start_time is not None:
                        print("후보 상태 해제: 지속 시간 초기화")

                    candidate_start_time = None

            cv2.imshow("Realtime Helmet Monitor", display_frame)

            key = cv2.waitKey(1) & 0xFF

            if key == ord("q") or key == 27:
                print("종료 키 입력됨")
                break

    except KeyboardInterrupt:
        print("Ctrl+C로 종료됨")

    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("실시간 안전모 모니터링 종료")


if __name__ == "__main__":
    main()