
"""Read server video chunks and analyze helmet violation candidates.

Read-only: never submits events or uploads media.
"""

import argparse
import time

from inference_worker.video_client import VideoClient
from inference_worker.video_decoder import decode_mp4
from inference_worker.ppe_detector import PPEDetector
from inference_worker.ppe_rules import assess_helmets
from inference_worker.person_tracker import PersonTracker
from inference_worker.violation_tracker import ViolationTracker
from inference_worker.policy import helmet_policy_status
from inference_worker.time_utils import (
    frame_timestamp,
    is_continuous_chunk,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--max-chunks", type=int, default=5)
    args = parser.parse_args()

    if args.poll <= 0 or args.max_chunks <= 0:
        parser.error("--poll and --max-chunks must be positive")

    detector = PPEDetector()
    client = VideoClient()

    tracker = PersonTracker()
    violations = ViolationTracker(threshold_seconds=3.0)

    stream_id = None
    last_sequence = -1
    previous_chunk = None
    previous_policy = None

    processed = 0
    candidate_count = 0

    def reset_tracking():
        return (
            PersonTracker(),
            ViolationTracker(threshold_seconds=3.0),
        )

    try:
        while processed < args.max_chunks:
            # 1. Find the active camera stream.
            streams = client.list_streams()

            selected = next(
                (
                    stream
                    for stream in streams
                    if stream["camera_id"] == args.camera_id
                ),
                None,
            )

            if selected is None:
                if stream_id is not None:
                    tracker, violations = reset_tracking()
                    stream_id = None
                    previous_chunk = None
                    last_sequence = -1
                    previous_policy = None

                print("[WAIT] No active stream")
                time.sleep(args.poll)
                continue

            # 2. Fetch and validate camera policy.
            policy = client.get_ppe_policy(args.camera_id)
            enabled, reason = helmet_policy_status(policy)

            policy_signature = (
                policy.get("configured"),
                tuple(policy.get("required_ppe") or []),
                tuple(policy.get("configuration_issues") or []),
            )

            if previous_policy != policy_signature:
                tracker, violations = reset_tracking()
                previous_chunk = None
                previous_policy = policy_signature
                print("[RESET] PPE policy changed")

            if not enabled:
                tracker, violations = reset_tracking()
                previous_chunk = None
                print(f"[POLICY SKIP] {reason}")
                time.sleep(args.poll)
                continue

            # 3. Reset when stream identity changes.
            if stream_id != selected["stream_id"]:
                stream_id = selected["stream_id"]
                last_sequence = -1
                previous_chunk = None
                tracker, violations = reset_tracking()
                print(f"[STREAM] {stream_id}")

            # 4. Read newer chunks.
            chunks = client.list_chunks(stream_id, last_sequence)

            if not chunks:
                time.sleep(args.poll)
                continue

            for chunk in chunks:
                if processed >= args.max_chunks:
                    break

                sequence = chunk["sequence"]

                if sequence <= last_sequence:
                    continue

                # 5. Reset if chunk timing is discontinuous.
                if previous_chunk is not None:
                    continuous = (
                        sequence == last_sequence + 1
                        and is_continuous_chunk(
                            previous_chunk["capture_start_at"],
                            previous_chunk["duration_ms"],
                            chunk["capture_start_at"],
                        )
                    )

                    if not continuous:
                        tracker, violations = reset_tracking()
                        print("[RESET] Chunk discontinuity")

                # 6. Download and decode MP4.
                data = client.download_chunk(chunk["chunk_id"])
                frames = list(decode_mp4(data))

                if not frames:
                    raise RuntimeError(
                        f"No decoded frames: {chunk['chunk_id']}"
                    )

                duration_ms = chunk["duration_ms"]

                if duration_ms <= 0:
                    raise RuntimeError("Invalid chunk duration")

                # Local estimate, not necessarily original capture FPS.
                fps = len(frames) / (duration_ms / 1000)

                # 7. Analyze each frame in capture-time order.
                for index, frame in enumerate(frames):
                    timestamp = frame_timestamp(
                        chunk["capture_start_at"],
                        index,
                        fps,
                    )

                    detections = detector.detect(frame)

                    assessments = tracker.update(
                        assess_helmets(detections)
                    )

                    candidates = violations.update(
                        assessments,
                        timestamp.timestamp(),
                    )

                    for candidate in candidates:
                        candidate_count += 1
                        print(
                            f"[LOCAL CANDIDATE] "
                            f"camera={args.camera_id} "
                            f"track={candidate['tracking_id']} "
                            f"duration={candidate['duration_seconds']:.2f}s"
                        )

                last_sequence = sequence
                previous_chunk = chunk
                processed += 1

                print(
                    f"[OK] camera={args.camera_id} "
                    f"sequence={sequence} "
                    f"frames={len(frames)}"
                )

    finally:
        client.close()

    print("\n========== Analysis ==========")
    print(f"Processed chunks: {processed}")
    print(f"Local candidates: {candidate_count}")
    print("==============================")


if __name__ == "__main__":
    main()
