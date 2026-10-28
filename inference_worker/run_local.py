
"""Run YOLO inference and PPE violation tracking on a local MP4 file."""

import argparse
from pathlib import Path

import cv2

from inference_worker.ppe_detector import PPEDetector
from inference_worker.video_decoder import decode_mp4
from inference_worker.ppe_rules import assess_helmets
from inference_worker.person_tracker import PersonTracker
from inference_worker.violation_tracker import ViolationTracker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("video", help="Path to an MP4 video file")
    args = parser.parse_args()

    path = Path(args.video)

    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")

    # 1. 영상 FPS 확인
    capture = cv2.VideoCapture(str(path))

    if not capture.isOpened():
        raise RuntimeError(f"Failed to open video: {path}")

    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
    finally:
        capture.release()

    if not 0 < fps < float("inf"):
        raise RuntimeError("Invalid video FPS")

    print(f"Video: {path}")
    print(f"FPS: {fps:.2f}")

    # 2. YOLO 및 추적 모듈 초기화
    detector = PPEDetector()
    tracker = PersonTracker()
    violation_tracker = ViolationTracker(threshold_seconds=3.0)

    frame_count = 0
    detection_count = 0
    candidate_count = 0

    # 3. MP4 프레임 순차 분석
    for frame in decode_mp4(path.read_bytes()):
        frame_count += 1

        # 영상 내부 상대 시각(초)
        timestamp = (frame_count - 1) / fps

        # YOLO 객체 탐지
        detections = detector.detect(frame)
        detection_count += len(detections)

        # 사람별 안전모 판정 및 Tracking
        assessments = tracker.update(
            assess_helmets(detections)
        )

        # 3초 이상 연속 의심 상태인지 확인
        candidates = violation_tracker.update(
            assessments, timestamp
        )

        # 프레임별 결과
        if assessments:
            print(f"\nFrame {frame_count} | time={timestamp:.2f}s")

            for person in assessments:
                print(
                    f"  id={person['tracking_id']}, "
                    f"status={person['status']}, "
                    f"confidence={person['person_confidence']:.2f}"
                )

        # 위반 후보 발생 시 출력
        for candidate in candidates:
            candidate_count += 1

            print("\n[위반 후보 발생]")
            print(f"Tracking ID: {candidate['tracking_id']}")
            print(f"PPE: {candidate['ppe_type']}")
            print(f"Start: {candidate['start_time']:.2f}s")
            print(f"End: {candidate['end_time']:.2f}s")
            print(f"Duration: {candidate['duration_seconds']:.2f}s")
            print(f"Confidence: {candidate['confidence']:.2f}")

    # 4. 최종 결과
    print("\n========== 분석 결과 ==========")
    print(f"Processed frames: {frame_count}")
    print(f"Total detections: {detection_count}")
    print(f"Violation candidates: {candidate_count}")
    print("================================")


if __name__ == "__main__":
    main()
