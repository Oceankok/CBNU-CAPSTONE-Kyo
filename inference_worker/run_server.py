
"""Read server video chunks and analyze PPE violation candidates.

Event delivery is disabled by default; inference priority is still reported.
--submit-events enables verified event/clip delivery (may broadcast).
--submit-review-candidates sends 3-class suspected cases for human judgment;
server broadcast settings can enable preventive warnings for these candidates.
Unredacted clips are temporary and removed on success or failure.
"""

import argparse
import json
import math
import httpx
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from inference_worker.video_client import VideoClient
from inference_worker.video_decoder import decode_mp4
from inference_worker.ppe_rules import assess_helmets, assess_ppe
from inference_worker.person_tracker import PersonTracker
from inference_worker.violation_tracker import ViolationTracker
from inference_worker.policy import helmet_policy_status, ppe_policy_status
from inference_worker.work_area_policy import resolve_person_policy
from inference_worker.time_utils import (
    frame_timestamp,
    is_continuous_chunk,
)
from inference_worker.event_builder import build_event
from inference_worker.event_validation import validate_candidate
from inference_worker.clip_buffer import ClipBuffer
from inference_worker.clip_manager import create_clip, calculate_clip_window
from inference_worker.pending_clips import PendingClips
from inference_worker.event_pipeline import ClipDeliveryError, submit_event_with_clip, _retry
from inference_worker.inference_load import InferenceLoad
from backend.runtime_version import local_version


def create_detector(model_path=None):
    # Offline contract tests do not import Ultralytics or load weights.
    from inference_worker.ppe_detector import PPEDetector
    return PPEDetector(model_path=model_path) if model_path is not None else PPEDetector()


def build_review_candidate_event(candidate, *, camera_id, start_time, end_time, model_version):
    """Produce metadata for human review, never verified no-helmet evidence."""
    return {
        "event_id": "EVT_" + uuid4().hex,
        "camera_id": camera_id,
        "tracking_id": str(candidate["tracking_id"]),
        "ppe_type": candidate["ppe_type"],
        "timestamp_start": start_time.isoformat(timespec="microseconds"),
        "timestamp_end": end_time.isoformat(timespec="microseconds"),
        "confidence": None,
        "frame_sample_count": candidate["frame_sample_count"],
        "model_version": model_version,
        "review_only": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--buffer-mib", type=int, default=256,
                        help="Maximum retained raw frame MiB per camera (default: 256)")
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--stop-file", type=Path,
                        help="Exit cleanly when the supervisor creates this file")
    parser.add_argument("--model-path", type=Path,
                        help="Explicit local PPE weights; no model is downloaded")
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--max-chunks", type=int, default=5)
    parser.add_argument("--submit-events", action="store_true",
                        help="Send qualified events; can trigger field broadcasts")
    parser.add_argument("--submit-review-candidates", action="store_true",
                        help="Submit 3-class suspected cases for manual review; server settings control preventive broadcasts")
    parser.add_argument("--replay-buffer", action="store_true",
                        help="Analyze existing buffered chunks for inspection only; cannot submit events")
    args = parser.parse_args()

    if args.submit_events and args.submit_review_candidates:
        parser.error("--submit-events and --submit-review-candidates are mutually exclusive")
    if args.replay_buffer and (args.submit_events or args.submit_review_candidates):
        parser.error("--replay-buffer cannot be combined with submission modes")

    if not math.isfinite(args.poll) or args.poll <= 0 or args.max_chunks <= 0:
        parser.error("--poll and --max-chunks must be positive")

    if args.buffer_mib < 8:
        parser.error("--buffer-mib must be at least 8")

    def new_clip_buffer():
        return ClipBuffer(retention_seconds=30.0, max_bytes=args.buffer_mib*1024*1024, max_frame_pixels=640*480, max_fps=15)

    # Initialize YOLO and API client.
    detector = create_detector(args.model_path) if args.model_path is not None else create_detector()
    if hasattr(detector, "model_version"):
        print(f"[MODEL] version={detector.model_version} no_helmet={detector.supports_no_helmet}")
    if args.submit_events and getattr(detector, "supports_no_helmet", None) is False:
        parser.error("Event submission requires person/helmet/no_helmet classes; "
                     "select verified local weights with --model-path")
    if args.submit_review_candidates and getattr(detector, "supports_no_helmet", None) is not False:
        parser.error("Review-only mode requires an identified 3-class model (no no_helmet class)")
    client = VideoClient()
    priority = InferenceLoad(client)
    clips = tempfile.TemporaryDirectory(prefix="ppe_inference_")
    startup = local_version()
    print(f"[HOST CODE] revision={startup['revision']} dirty={startup['dirty']}")

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
        while processed < args.max_chunks and not (args.stop_file and args.stop_file.exists()):

            try:
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
                # Work-area policies are evaluated per person, not by camera-wide PPE union.
                # Invalid ROI configuration must fail closed instead of falling back to camera rules.
                if not isinstance(policy, dict):
                    enabled, reason = False, "invalid_policy"
                    policy_signature = "invalid_policy"
                else:
                    work_area_mode = policy.get("scope") == "work_area"
                    if work_area_mode:
                        enabled = (policy.get("configured") is True
                                   and not policy.get("configuration_issues")
                                   and bool(policy.get("policy_version"))
                                   and isinstance(policy.get("areas"), list)
                                   and isinstance(policy.get("zone_required_ppe"), list)
                                   and isinstance(policy.get("event_supported_ppe"), list))
                        reason = "work_area_ready" if enabled else "invalid_work_area_policy"
                    else:
                        enabled = any(ppe_policy_status(policy, ppe)[0] for ppe in ("helmet", "vest"))
                        reason = "ppe_required" if enabled else "no_supported_ppe_required"
                    # Covers ROI geometry, membership and rule changes as well as version changes.
                    policy_signature = json.dumps(policy, sort_keys=True, default=str)

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
                    # Establish a live boundary once per stream. Reopening the process
                    # must not replay old footage under newly generated event IDs.
                    last_sequence = selected["last_sequence"]
                    if type(last_sequence) is not int or last_sequence < -1:
                        raise ValueError("Invalid stream last_sequence")
                    if args.replay_buffer:
                        last_sequence = -1
                    print(f"[START BOUNDARY] stream={stream_id} after_sequence={last_sequence}")
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
                        if args.stop_file and args.stop_file.exists():
                            return  # finally closes decoder, lease and private clips.
                        decoded_count += 1
                        timestamp = frame_timestamp(
                            chunk["capture_start_at"],
                            index,
                            fps,
                        )

                        # Retain recent frames and finish clips after post-roll arrives.
                        clip_buffer.max_fps = fps / math.ceil(fps / 15)
                        chunk_evictions += clip_buffer.add(timestamp, frame)

                        for job in pending_clips.pop_ready(timestamp):
                            # Reject partial clips; no original footage may be inferred.
                            selected = [
                                (ts, image)
                                for ts, image in clip_buffer.frames
                                if job["clip_start"] <= ts <= job["clip_end"]
                            ]
                            tolerance = max(0.25, 1.5 / clip_buffer.max_fps)
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
                                f"{job['camera_id']}_{job['tracking_id']}_{job['ppe_type']}_"
                                f"{int(job['clip_end'].timestamp() * 1000)}.mp4"
                            )
                            clip_path = create_clip(
                                [image for _, image in selected],
                                Path(clips.name) / clip_name,
                                fps=fps / math.ceil(fps / 15),
                            )
                            local_clip_count += 1
                            try:
                                if (args.submit_events or args.submit_review_candidates) and job.get("event"):
                                    priority.release()
                                    result = submit_event_with_clip(client, job["event"], clip_path)
                                    label = "REVIEW CANDIDATE DELIVERED" if job["event"].get("review_only") else "EVENT DELIVERED"
                                    print(f"[{label}] {result['event_id']}")
                                else:
                                    print("[LOCAL CLIP READY] temporary clip validated")
                            except ClipDeliveryError as error:
                                print(f"[EVENT DELIVERY FAILED] event={job['event']['event_id']} reason={error}")
                            finally:
                                clip_path.unlink(missing_ok=True)
                                selected.clear()  # Do not pin evicted frames after encoding.

                        # Uploads release the lease; reacquire before GPU inference.
                        priority.refresh()
                        detections = detector.detect(frame)

                        # Person-level PPE assessment and tracking.
                        assessments = tracker.update(
                            assess_ppe(detections, frame.shape, getattr(detector, "supported_ppe", ("helmet", "vest")))
                            if getattr(detector, "supports_no_helmet", None) is False
                            else assess_helmets(detections)
                        )
                        # Apply PPE requirements for each person's *current* ROI.
                        # The ViolationTracker uses policy_scope_key to prevent carrying
                        # a 3-second observation across area/policy changes.
                        frame_height, frame_width = frame.shape[:2]
                        person_policies = {}
                        for assessment in assessments:
                            track_id = assessment["tracking_id"]
                            try:
                                effective_policy = resolve_person_policy(
                                    policy,
                                    assessment["person_bbox"],
                                    frame_width,
                                    frame_height,
                                )
                                if "ppe" in assessment:
                                    for ppe_type, state in assessment["ppe"].items():
                                        if not ppe_policy_status(effective_policy, ppe_type)[0]:
                                            state["status"] = "unknown"
                                    person_enabled = any(ppe_policy_status(effective_policy, p)[0]
                                                         for p in assessment["ppe"])
                                else:
                                    person_enabled, _ = helmet_policy_status(effective_policy)
                            except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError) as error:
                                # Malformed or out-of-frame geometry cannot generate a candidate.
                                print(f"[ROI SKIP] track={track_id} reason={error}")
                                person_enabled = False
                                effective_policy = None

                            if not person_enabled:
                                assessment["status"] = "unknown"
                                for state in assessment.get("ppe", {}).values():
                                    state["status"] = "unknown"
                                assessment["verified_ppe_confidence"] = None
                                continue

                            assessment["policy_scope_key"] = (
                                effective_policy.get("policy_version"),
                                effective_policy.get("scope"),
                                tuple(effective_policy.get("area_ids") or []),
                                tuple(sorted(effective_policy.get("required_ppe") or [])),
                            )
                            person_policies[track_id] = effective_policy

                        # Both submission modes need pre-roll; preserve diagnostic-only candidates.
                        # Clearing confidence alone cannot reset a review-only streak:
                        # those assessments already have no verified confidence.
                        pre_roll_start, _ = calculate_clip_window(timestamp, timestamp)
                        if clip_buffer.frames[0][0] > pre_roll_start:
                            for assessment in assessments:
                                if args.submit_review_candidates:
                                    assessment["status"] = "unknown"
                                    for state in assessment.get("ppe", {}).values():
                                        state["status"] = "unknown"
                                assessment["verified_ppe_confidence"] = None

                        # Detect sustained suspected violations.
                        candidates = violations.update(
                            assessments,
                            timestamp.timestamp(),
                        )

                        # 8. Create local clips and validate candidates.
                        for candidate in candidates:
                            # Only submit if its track still satisfies the *same* ROI policy.
                            candidate_policy = person_policies.get(candidate["tracking_id"])
                            if candidate_policy is None:
                                blocked_count += 1
                                print("[ROI BLOCKED] no effective person policy")
                                continue
                            candidate_count += 1

                            print(
                                f"[LOCAL CANDIDATE] "
                                f"camera={args.camera_id} "
                                f"track={candidate['tracking_id']} ppe={candidate['ppe_type']} "
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

                            # Check policy and actual negative-class confidence.
                            allowed, reason = validate_candidate(
                                candidate,
                                candidate_policy,
                            )

                            if args.submit_review_candidates:
                                if not ppe_policy_status(candidate_policy, candidate["ppe_type"])[0]:
                                    blocked_count += 1
                                    continue
                                # The three-class detector cannot prove non-compliance.
                                # Keep it explicitly review-only, with no fabricated confidence.
                                # The policy was checked above; preserve the existing
                                # continuous observation and complete-clip requirements.
                                pre_roll_start, _ = calculate_clip_window(start_time, end_time)
                                if (not clip_buffer.frames or
                                        clip_buffer.frames[0][0] > pre_roll_start):
                                    blocked_count += 1
                                    print("[REVIEW BLOCKED] pre-roll unavailable")
                                    continue
                                event = build_review_candidate_event(
                                    candidate,
                                    camera_id=args.camera_id,
                                    start_time=start_time,
                                    end_time=end_time,
                                    model_version=getattr(detector, "model_version", "helmet-3class-review"),
                                )
                                # Register the sustained observation now; post-roll is
                                # uploaded later under the same idempotent event ID.
                                priority.release()
                                _retry(lambda: client.submit_event(event))
                                ready_count += 1
                                pending_clips.add(candidate, args.camera_id, event=event)
                                print(f"[REVIEW CANDIDATE READY] {event['event_id']}")
                                continue

                            if not allowed:
                                blocked_count += 1
                                print(
                                    f"[EVENT BLOCKED] reason={reason}"
                                )
                                if not args.submit_events:
                                    pending_clips.add(candidate, args.camera_id)
                                continue

                            # Use the minimum no_helmet score across the sustained
                            # observation, never person confidence or helmet absence.
                            verified_confidence = candidate[
                                "verified_ppe_confidence"
                            ]

                            event = build_event(
                                camera_id=args.camera_id,
                                tracking_id=candidate["tracking_id"],
                                start_time=start_time,
                                end_time=end_time,
                                confidence=verified_confidence,
                                frame_sample_count=candidate.get("frame_sample_count", max(
                                    1, round(candidate["duration_seconds"] * fps))),
                                model_version=getattr(detector, "model_version", "helmet-yolov8n-v1"),
                            )

                            if args.submit_events:
                                priority.release()
                                _retry(lambda: client.submit_event(event))
                            ready_count += 1

                            pending_clips.add(candidate, args.camera_id, event=event)
                            print(f"[EVENT READY] {event['event_id']}")

                    if not decoded_count:
                        raise RuntimeError(f"No decoded frames: {chunk['chunk_id']}")
                    if chunk_evictions:
                        print(f"[BUFFER LIMIT] camera={args.camera_id} evicted={chunk_evictions} bytes={clip_buffer.bytes_used}")
                    priority.release()
                    # Poll waits belong to empty input. A full poll after every
                    # 1s chunk adds analysis time on top and makes live lag grow.
                    # Keep a short lease-free boundary for redaction to progress.
                    time.sleep(min(args.poll, 0.1))
                    last_sequence = sequence
                    previous_chunk = chunk
                    processed += 1

                    print(
                        f"[OK] camera={args.camera_id} "
                        f"sequence={sequence} "
                        f"frames={decoded_count}"
                    )

            except httpx.HTTPError as error:
                status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
                if status is not None and status not in {429, 500, 502, 503, 504}:
                    raise
                # A network gap invalidates continuous observation, not the camera.
                if frames is not None:
                    frames.close()
                    frames = None
                tracker, violations = reset_tracking()
                clip_buffer = new_clip_buffer()
                pending_clips = PendingClips()
                previous_chunk = None
                try:
                    priority.release()
                except httpx.HTTPError:
                    priority.next_refresh = 0
                    # The server's lease TTL releases a lost acknowledgement.
                print('[SERVER RETRY] temporary API failure; observation reset')
                time.sleep(max(args.poll, 1.0))

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
