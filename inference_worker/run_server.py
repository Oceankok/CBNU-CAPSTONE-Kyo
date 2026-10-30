
"""Read server video chunks and analyze PPE violation candidates.

Event delivery is disabled by default; inference priority is still reported.
--submit-events enables qualified event/clip delivery.
Unredacted clips are temporary and removed on success or failure.
"""

import argparse
import httpx
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from inference_worker.video_client import VideoClient
from inference_worker.video_decoder import decode_mp4
from inference_worker.ppe_rules import assess_helmets
from inference_worker.person_tracker import PersonTracker
from inference_worker.violation_tracker import ViolationTracker
from inference_worker.policy import helmet_policy_status
from inference_worker.time_utils import (
    frame_timestamp,
    is_continuous_chunk,
)
from inference_worker.event_builder import build_event
from inference_worker.event_validation import validate_candidate
from inference_worker.clip_buffer import ClipBuffer
from inference_worker.clip_manager import create_clip
from inference_worker.pending_clips import PendingClips
from inference_worker.event_pipeline import submit_event_with_clip
from inference_worker.inference_load import InferenceLoad


def create_detector():
    # Offline contract tests do not import Ultralytics or load weights.
    from inference_worker.ppe_detector import PPEDetector
    return PPEDetector()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--max-chunks", type=int, default=5)
    parser.add_argument("--submit-events", action="store_true",
                        help="Send qualified events; can trigger field broadcasts")
    parser.add_argument("--buffer-mib", type=int, default=256,
                        help="Maximum retained raw frame MiB per camera (default: 256)")
    args = parser.parse_args()

    if args.poll <= 0 or args.max_chunks <= 0:
        parser.error("--poll and --max-chunks must be positive")

    if args.buffer_mib < 8:
        parser.error("--buffer-mib must be at least 8")

    def new_clip_buffer():
        return ClipBuffer(retention_seconds=30.0, max_bytes=args.buffer_mib*1024*1024)

    # Initialize YOLO and API client.
    detector = create_detector()
    client = VideoClient()
    priority = InferenceLoad(client)
    clips = tempfile.TemporaryDirectory(prefix="ppe_inference_")

    def reset_tracking():
        return (
            PersonTracker(),
            ViolationTracker(threshold_seconds=3.0),
        )

    tracker, violations = reset_tracking()
    clip_buffer = new_clip_buffer()
    pending_clips = PendingClips()

    stream_id = None
    last_sequence = -1
    previous_chunk = None
    previous_policy = None

    processed = 0
    candidate_count = 0
    blocked_count = 0
    ready_count = 0
    local_clip_count = 0
    incomplete_clip_count = 0
    expired_chunk_count = 0
    frames = None

    try:
        while processed < args.max_chunks:

            # 1. Find active camera stream.
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
                    clip_buffer = new_clip_buffer()
                    pending_clips = PendingClips()
                    stream_id = None
                    previous_chunk = None
                    last_sequence = -1
                    previous_policy = None

                print("[WAIT] No active stream")
                time.sleep(args.poll)
                continue

            # 2. Check camera PPE policy.
            policy = client.get_ppe_policy(args.camera_id)
            enabled, reason = helmet_policy_status(policy)

            policy_signature = (
                policy.get("configured"),
                tuple(policy.get("required_ppe") or []),
                tuple(policy.get("detectable_ppe") or []),
                tuple(policy.get("configuration_issues") or []),
            )

            if previous_policy != policy_signature:
                tracker, violations = reset_tracking()
                clip_buffer = new_clip_buffer()
                pending_clips = PendingClips()
                previous_chunk = None
                previous_policy = policy_signature
                print("[RESET] PPE policy changed")

            if not enabled:
                tracker, violations = reset_tracking()
                clip_buffer = new_clip_buffer()
                pending_clips = PendingClips()
                previous_chunk = None
                print(f"[POLICY SKIP] reason={reason}")
                time.sleep(args.poll)
                continue

            # 3. Reset on stream change.
            if stream_id != selected["stream_id"]:
                stream_id = selected["stream_id"]
                last_sequence = -1
                previous_chunk = None

                tracker, violations = reset_tracking()
                clip_buffer = new_clip_buffer()
                pending_clips = PendingClips()

                print(f"[STREAM] {stream_id}")

            # 4. Fetch newer MP4 chunks.
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

                # 5. Detect missing or discontinuous chunks.
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
                        clip_buffer = new_clip_buffer()
                        pending_clips = PendingClips()
                        print("[RESET] Chunk discontinuity")

                # 6. Download and decode MP4.
                try:
                    data = client.download_chunk(chunk["chunk_id"])
                except httpx.HTTPStatusError as error:
                    if error.response.status_code != 404:
                        raise  # Revoked access and other failures are not expiration.
                    tracker, violations = reset_tracking()
                    clip_buffer = new_clip_buffer()
                    pending_clips = PendingClips()
                    previous_chunk = None
                    last_sequence = sequence
                    processed += 1  # --max-chunks bounds attempted chunks too.
                    expired_chunk_count += 1
                    priority.release()
                    print(f"[CHUNK EXPIRED] camera={args.camera_id} sequence={sequence}")
                    continue
                duration_ms = chunk["duration_ms"]
                if duration_ms <= 0:
                    raise RuntimeError("Invalid chunk duration")
                frames = decode_mp4(data, with_timing=True, duration_ms=duration_ms)
                decoded_count = 0
                chunk_evictions = 0

                # 7. Analyze one decoded frame at a time in capture order.
                for index, fps, frame in frames:
                    decoded_count += 1
                    timestamp = frame_timestamp(
                        chunk["capture_start_at"],
                        index,
                        fps,
                    )

                    # Retain recent frames and finish clips after post-roll arrives.
                    chunk_evictions += clip_buffer.add(timestamp, frame)

                    for job in pending_clips.pop_ready(timestamp):
                        # Reject partial clips; no original footage may be inferred.
                        selected = [
                            (ts, image)
                            for ts, image in clip_buffer.frames
                            if job["clip_start"] <= ts <= job["clip_end"]
                        ]
                        tolerance = max(0.25, 1.5 / fps)
                        complete = bool(selected)
                        if complete:
                            complete = (
                                (selected[0][0] - job["clip_start"]).total_seconds() <= tolerance
                                and (job["clip_end"] - selected[-1][0]).total_seconds() <= tolerance
                                and all(
                                    0 <= (b[0] - a[0]).total_seconds() <= tolerance
                                    for a, b in zip(selected, selected[1:])
                                )
                            )
                        if not complete:
                            incomplete_clip_count += 1
                            print(f"[CLIP INCOMPLETE] track={job['tracking_id']} ")
                            selected.clear()
                            continue

                        clip_name = (
                            f"{job['camera_id']}_{job['tracking_id']}_"
                            f"{int(job['clip_end'].timestamp() * 1000)}.mp4"
                        )
                        clip_path = create_clip(
                            [image for _, image in selected],
                            Path(clips.name) / clip_name,
                            fps=fps,
                        )
                        local_clip_count += 1
                        try:
                            if args.submit_events:
                                priority.release()
                                result = submit_event_with_clip(client, job["event"], clip_path)
                                print(f"[EVENT DELIVERED] {result['event_id']}")
                            else:
                                print("[LOCAL CLIP READY] temporary clip validated")
                        finally:
                            clip_path.unlink(missing_ok=True)
                            selected.clear()  # Do not pin evicted frames after encoding.

                    # Uploads release the lease; reacquire before GPU inference.
                    priority.refresh()
                    detections = detector.detect(frame)

                    # Person-level helmet assessment and tracking.
                    assessments = tracker.update(
                        assess_helmets(detections)
                    )

                    # Detect sustained suspected violations.
                    candidates = violations.update(
                        assessments,
                        timestamp.timestamp(),
                    )

                    # 8. Create local clips and validate candidates.
                    for candidate in candidates:
                        candidate_count += 1

                        print(
                            f"[LOCAL CANDIDATE] "
                            f"camera={args.camera_id} "
                            f"track={candidate['tracking_id']} "
                            f"duration={candidate['duration_seconds']:.2f}s"
                        )

                        start_time = datetime.fromtimestamp(
                            candidate["start_time"],
                            tz=timezone.utc,
                        )

                        end_time = datetime.fromtimestamp(
                            candidate["end_time"],
                            tz=timezone.utc,
                        )

                        # Check policy and PPE confidence.
                        allowed, reason = validate_candidate(
                            candidate,
                            policy,
                        )

                        if not allowed:
                            blocked_count += 1
                            print(
                                f"[EVENT BLOCKED] reason={reason}"
                            )
                            if not args.submit_events:
                                pending_clips.add(candidate, args.camera_id)
                            continue

                        # Only externally verified PPE confidence
                        # may be used for event submission.
                        verified_confidence = candidate[
                            "verified_ppe_confidence"
                        ]

                        event = build_event(
                            camera_id=args.camera_id,
                            tracking_id=candidate["tracking_id"],
                            start_time=start_time,
                            end_time=end_time,
                            confidence=verified_confidence,
                            frame_sample_count=max(
                                1,
                                round(
                                    candidate["duration_seconds"] * fps
                                ),
                            ),
                        )

                        ready_count += 1

                        pending_clips.add(candidate, args.camera_id, event=event)
                        print(f"[EVENT READY] {event['event_id']}")

                if not decoded_count:
                    raise RuntimeError(f"No decoded frames: {chunk['chunk_id']}")
                if chunk_evictions:
                    print(f"[BUFFER LIMIT] camera={args.camera_id} evicted={chunk_evictions} bytes={clip_buffer.bytes_used}")
                priority.release()
                time.sleep(args.poll)  # Give queued redaction work an idle boundary.
                last_sequence = sequence
                previous_chunk = chunk
                processed += 1

                print(
                    f"[OK] camera={args.camera_id} "
                    f"sequence={sequence} "
                    f"frames={decoded_count}"
                )

    finally:
        try:
            if frames is not None:
                frames.close()  # Also release decoder files if inference raises mid-chunk.
        finally:
            try:
                priority.release()
            finally:
                try:
                    client.close()
                finally:
                    clips.cleanup()

    print("\n========== Analysis ==========")
    print(f"Processed chunks: {processed-expired_chunk_count}")
    print(f"Expired chunks: {expired_chunk_count}")
    print(f"Local candidates: {candidate_count}")
    print(f"Local clips: {local_clip_count}")
    print(f"Incomplete clips: {incomplete_clip_count}")
    print(f"Pending clips: {len(pending_clips.pending)}")
    print(f"Blocked events: {blocked_count}")
    print(f"Ready events: {ready_count}")
    print("==============================")


if __name__ == "__main__":
    main()
