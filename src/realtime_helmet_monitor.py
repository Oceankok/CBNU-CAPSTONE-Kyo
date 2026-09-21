"""Real-time, multi-worker helmet monitoring.

The hot camera loop only captures, tracks, associates and renders.  Clip
encoding, database writes and TTS run on a background worker so an event does
not freeze inference.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import importlib.util
from pathlib import Path
from queue import Full, Queue
import sys
from tempfile import NamedTemporaryFile
from threading import Thread
import time
from typing import Any, Sequence

import cv2
from ultralytics import YOLO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from backend.services.candidate_event_service import create_no_helmet_candidate_event
from src.ppe_tracking import (
    Detection,
    IoUTrackFallback,
    PersonPPEAssociator,
    ViolationEvent,
    ViolationTracker,
    WorkerObservation,
)


DEFAULT_MODEL_PATH = "Exp01_yolov8n_640_clean_3class-13/weights/best.pt"
DEFAULT_CAMERA_ID = "CAM_001"
CLIP_WIDTH = 640
CLIP_HEIGHT = 480


@dataclass(frozen=True)
class EventPayload:
    frame_image: Any
    frame_buffer: tuple[Any, ...]
    events: tuple[ViolationEvent, ...]
    clip_fps: float


class EventWorker:
    """Serialize media/DB/TTS work away from the inference loop."""

    def __init__(
        self,
        camera_id: str,
        model_version: str,
        enable_tts: bool,
        queue_size: int = 8,
    ) -> None:
        self.camera_id = camera_id
        self.model_version = model_version
        self.enable_tts = enable_tts
        self._queue: Queue[EventPayload | None] = Queue(maxsize=queue_size)
        self._thread = Thread(target=self._run, name="ppe-event-writer", daemon=True)
        self._thread.start()

    def submit(self, payload: EventPayload) -> bool:
        try:
            self._queue.put_nowait(payload)
            return True
        except Full:
            print("[WARN] 이벤트 저장 대기열이 가득 차 이번 이벤트를 건너뜁니다.")
            return False

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join()

    @staticmethod
    def _write_clip(frames: Sequence[Any], fps: float) -> Path | None:
        if not frames:
            return None
        with NamedTemporaryFile(prefix="ppe_event_", suffix=".mp4", delete=False) as tmp:
            path = Path(tmp.name)
        writer = cv2.VideoWriter(
            str(path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            max(1.0, fps),
            (CLIP_WIDTH, CLIP_HEIGHT),
        )
        if not writer.isOpened():
            path.unlink(missing_ok=True)
            return None
        try:
            for frame in frames:
                writer.write(frame)
        finally:
            writer.release()
        return path

    def _run(self) -> None:
        while True:
            payload = self._queue.get()
            if payload is None:
                self._queue.task_done()
                return
            clip_path: Path | None = None
            try:
                clip_path = self._write_clip(payload.frame_buffer, payload.clip_fps)
                for event in payload.events:
                    try:
                        saved = create_no_helmet_candidate_event(
                            camera_id=self.camera_id,
                            confidence=event.confidence,
                            source_path=clip_path or "realtime_camera",
                            frame_image=payload.frame_image,
                            model_version=self.model_version,
                            duration_sec=max(1, round(event.duration_sec)),
                            frame_sample_count=event.sample_count,
                            tracking_id=str(event.track_id),
                            enable_tts=self.enable_tts,
                        )
                        print(
                            "[EVENT] "
                            f"track={event.track_id} status={event.status} "
                            f"confidence={event.confidence:.2f} "
                            f"event_id={saved.get('event_id')}"
                        )
                    except Exception as exc:
                        print(f"[ERROR] track={event.track_id} 이벤트 저장 실패: {exc}")
            except Exception as exc:  # Keep the worker alive after one failed event.
                print(f"[ERROR] 이벤트 저장 실패: {exc}")
            finally:
                if clip_path is not None:
                    clip_path.unlink(missing_ok=True)
                self._queue.task_done()


class RollingPerformance:
    def __init__(self, window_size: int = 120) -> None:
        self.inference_ms: deque[float] = deque(maxlen=window_size)
        self.started_at = time.perf_counter()
        self.frames = 0
        self.inference_frames = 0

    def add_frame(self) -> None:
        self.frames += 1

    def add_inference(self, latency_ms: float) -> None:
        self.inference_frames += 1
        self.inference_ms.append(latency_ms)

    def summary(self) -> str:
        elapsed = max(time.perf_counter() - self.started_at, 1e-6)
        mean_ms = (
            sum(self.inference_ms) / len(self.inference_ms)
            if self.inference_ms
            else 0.0
        )
        return (
            f"capture={self.frames / elapsed:.1f} FPS | "
            f"infer={mean_ms:.1f} ms | "
            f"processed={self.inference_frames / elapsed:.1f} FPS"
        )


def parse_source(value: str) -> int | str:
    return int(value) if value.isdigit() else value


def extract_detections(result: Any, names: dict[int, str] | list[str]) -> list[Detection]:
    detections: list[Detection] = []
    boxes = result.boxes
    track_ids = boxes.id.int().cpu().tolist() if boxes.id is not None else []
    for index, box in enumerate(boxes):
        class_id = int(box.cls[0])
        label = names[class_id]
        if label not in {"person", "helmet", "no_helmet"}:
            continue
        xyxy = tuple(float(value) for value in box.xyxy[0].cpu().tolist())
        detections.append(
            Detection(
                label=label,
                confidence=float(box.conf[0]),
                box=xyxy,  # type: ignore[arg-type]
                track_id=track_ids[index] if index < len(track_ids) else None,
            )
        )
    return detections


def annotate_workers(frame: Any, observations: Sequence[WorkerObservation]) -> Any:
    for observation in observations:
        x1, y1, x2, y2 = (int(value) for value in observation.person.box)
        compliant = observation.status == "helmet"
        color = (20, 190, 20) if compliant else (20, 20, 230)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            frame,
            f"ID {observation.track_id}: {observation.status}",
            (x1, max(18, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            2,
            cv2.LINE_AA,
        )
    return frame


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--source", default="0", help="camera index or video path")
    parser.add_argument("--camera-id", default=DEFAULT_CAMERA_ID)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--max-det", type=int, default=100)
    parser.add_argument("--frame-interval", type=int, default=2)
    parser.add_argument("--tracker", default="bytetrack.yaml")
    parser.add_argument("--device", default=None)
    parser.add_argument("--half", action="store_true")
    parser.add_argument("--event-duration", type=float, default=2.0)
    parser.add_argument("--event-cooldown", type=float, default=10.0)
    parser.add_argument("--min-violation-ratio", type=float, default=0.6)
    parser.add_argument("--clip-seconds", type=float, default=5.0)
    parser.add_argument(
        "--require-no-helmet-class",
        action="store_true",
        help="do not infer violation from an unmatched person",
    )
    parser.add_argument("--no-display", action="store_true")
    parser.add_argument("--no-events", action="store_true")
    parser.add_argument("--no-tts", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.frame_interval < 1:
        raise ValueError("--frame-interval must be at least 1")
    model_path = Path(args.model)
    if not model_path.exists():
        raise FileNotFoundError(f"학습된 안전모 모델을 찾을 수 없습니다: {model_path}")

    model = YOLO(str(model_path))
    source = parse_source(args.source)
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"입력을 열 수 없습니다: {source}")
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    source_fps = cap.get(cv2.CAP_PROP_FPS)
    clip_fps = source_fps if 1.0 <= source_fps <= 240.0 else 20.0
    frame_buffer: deque[Any] = deque(
        maxlen=max(1, round(clip_fps * args.clip_seconds))
    )

    associator = PersonPPEAssociator(
        infer_missing_helmet=not args.require_no_helmet_class
    )
    violation_tracker = ViolationTracker(
        duration_sec=args.event_duration,
        cooldown_sec=args.event_cooldown,
        min_violation_ratio=args.min_violation_ratio,
    )
    fallback_tracker = IoUTrackFallback()
    event_worker = None
    if not args.no_events:
        event_worker = EventWorker(
            camera_id=args.camera_id,
            model_version=model_path.stem,
            enable_tts=not args.no_tts,
        )
    performance = RollingPerformance()
    native_tracker_available = importlib.util.find_spec("lap") is not None
    if not native_tracker_available:
        print(
            "[WARN] lap 패키지가 없어 ByteTrack 대신 내장 IoU 추적기를 사용합니다. "
            "requirements.txt 설치 후 ByteTrack이 자동 활성화됩니다."
        )
    is_live_source = isinstance(source, int)
    frame_index = 0
    display_frame: Any | None = None

    print(
        f"모니터링 시작: model={model_path} source={source} "
        f"imgsz={args.imgsz} conf={args.conf} interval={args.frame_interval}"
    )
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            performance.add_frame()
            frame_index += 1
            frame_buffer.append(cv2.resize(frame, (CLIP_WIDTH, CLIP_HEIGHT)))

            if display_frame is None:
                display_frame = frame.copy()
            if frame_index % args.frame_interval == 0:
                started = time.perf_counter()
                inference_options = {
                    "source": frame,
                    "imgsz": args.imgsz,
                    "conf": args.conf,
                    "iou": args.iou,
                    "max_det": args.max_det,
                    "device": args.device,
                    "verbose": False,
                }
                if args.half:
                    inference_options["half"] = True
                if native_tracker_available:
                    result = model.track(
                        persist=True,
                        tracker=args.tracker,
                        **inference_options,
                    )[0]
                else:
                    result = model.predict(**inference_options)[0]
                performance.add_inference((time.perf_counter() - started) * 1000.0)
                detections = extract_detections(result, model.names)
                people = [item for item in detections if item.label == "person"]
                fallback_ids = fallback_tracker.update([item.box for item in people])
                observations = associator.associate(detections, fallback_ids)

                if is_live_source:
                    now = time.monotonic()
                else:
                    now = max(0.0, cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
                events = violation_tracker.update(observations, now)
                display_frame = annotate_workers(result.plot(), observations)
                cv2.putText(
                    display_frame,
                    performance.summary(),
                    (12, 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )

                if event_worker is not None and events:
                    event_worker.submit(
                        EventPayload(
                            frame_image=display_frame.copy(),
                            frame_buffer=tuple(frame_buffer),
                            events=tuple(events),
                            clip_fps=clip_fps,
                        )
                    )

            if not args.no_display and display_frame is not None:
                cv2.imshow("Realtime Helmet Monitor", display_frame)
                key = cv2.waitKey(1) & 0xFF
                if key in {ord("q"), 27}:
                    break
    except KeyboardInterrupt:
        print("Ctrl+C로 종료합니다.")
    finally:
        cap.release()
        if not args.no_display:
            cv2.destroyAllWindows()
        if event_worker is not None:
            event_worker.close()
        print(f"모니터링 종료: {performance.summary()}")


if __name__ == "__main__":
    main()
