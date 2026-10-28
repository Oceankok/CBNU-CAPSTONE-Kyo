
"""Analyze consecutive local MP4 chunks with shared tracking state."""

import argparse
import re
from pathlib import Path

import cv2

from inference_worker.ppe_detector import PPEDetector
from inference_worker.video_decoder import decode_mp4
from inference_worker.ppe_rules import assess_helmets
from inference_worker.person_tracker import PersonTracker
from inference_worker.violation_tracker import ViolationTracker


def chunk_sequence(path):
    match = re.fullmatch(r"chunk_(\d+)\.mp4", path.name)
    return int(match.group(1)) if match else None


def get_fps(path):
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise RuntimeError(f"Cannot open video: {path}")
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not 0 < fps < float("inf"):
            raise RuntimeError(f"Invalid FPS: {path}")
        return fps
    finally:
        capture.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", help="Directory containing MP4 chunks")
    parser.add_argument("--threshold", type=float, default=3.0)
    args = parser.parse_args()

    directory = Path(args.directory)
    paths = sorted(directory.glob("chunk_*.mp4"), key=lambda p: p.name)

    if not paths:
        raise RuntimeError("No MP4 chunks found")

    detector = PPEDetector()
    tracker = PersonTracker()
    violations = ViolationTracker(threshold_seconds=args.threshold)

    total_frames = 0
    total_candidates = 0
    elapsed = 0.0
    previous_sequence = None

    for path in paths:
        sequence = chunk_sequence(path)

        if sequence is None:
            continue

        if previous_sequence is not None and sequence != previous_sequence + 1:
            print(f"[WARNING] Chunk sequence gap before {path.name}")
            tracker = PersonTracker()
            violations = ViolationTracker(threshold_seconds=args.threshold)

        fps = get_fps(path)
        chunk_frames = 0

        print(f"\n[Chunk] {path.name} | FPS={fps:.2f}")

        for frame in decode_mp4(path.read_bytes()):
            timestamp = elapsed + chunk_frames / fps
            chunk_frames += 1
            total_frames += 1

            detections = detector.detect(frame)
            assessments = tracker.update(assess_helmets(detections))
            candidates = violations.update(assessments, timestamp)

            for candidate in candidates:
                total_candidates += 1
                print(
                    f"[CANDIDATE] id={candidate['tracking_id']} "
                    f"duration={candidate['duration_seconds']:.2f}s "
                    f"ppe={candidate['ppe_type']}"
                )

        if chunk_frames == 0:
            raise RuntimeError(f"No decodable frames: {path}")

        elapsed += chunk_frames / fps
        previous_sequence = sequence

        print(f"Frames={chunk_frames} | elapsed={elapsed:.2f}s")

    print("\n========== 분석 결과 ==========")
    print(f"Chunks: {len(paths)}")
    print(f"Frames: {total_frames}")
    print(f"Estimated duration: {elapsed:.2f}s")
    print(f"Violation candidates: {total_candidates}")
    print("================================")


if __name__ == "__main__":
    main()
