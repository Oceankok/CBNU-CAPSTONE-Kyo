"""Webcam PPE monitor using positive helmet/vest/person detections.

Run from the repository root: python src/realtime_helmet_monitor.py
Candidate delivery is opt-in; absence never becomes a verified model score.
"""
import argparse
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import time
from uuid import uuid4

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from inference_worker.person_tracker import PersonTracker
from inference_worker.ppe_rules import assess_ppe
from inference_worker.violation_tracker import ViolationTracker


def main():
    from inference_worker.ppe_detector import PPEDetector, DEFAULT_MODEL

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path', type=Path, default=DEFAULT_MODEL)
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--camera-id', default='CAM_001')
    parser.add_argument('--conf', type=float, default=.5)
    parser.add_argument('--submit-review-candidates', action='store_true',
                        help='Store review candidates; server settings control preventive broadcasts')
    args = parser.parse_args()
    if not 0 < args.conf <= 1:
        parser.error('--conf must be in (0, 1]')
    detector = PPEDetector(args.model_path, confidence=args.conf)
    if detector.supported_ppe != {'helmet', 'vest'}:
        parser.error('Select weights with person, helmet and vest classes')
    print(f'[MODEL] {args.model_path} classes={detector.model.names}')
    tracker = PersonTracker()
    violations = ViolationTracker(threshold_seconds=3)
    cap = (cv2.VideoCapture(args.camera, cv2.CAP_DSHOW) if sys.platform == 'win32'
           else cv2.VideoCapture(args.camera))
    if not cap.isOpened():
        cap.release()
        raise RuntimeError(f'Cannot open camera {args.camera}')
    buffer = deque()
    alerts = {}
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            now = time.monotonic()
            detections = detector.detect(frame)
            people = tracker.update(assess_ppe(detections, frame.shape, detector.supported_ppe))
            candidates = violations.update(people, now)
            # Bound raw memory to 5 seconds, 10 FPS, 640x480 frames.
            if not buffer or now - buffer[-1][0] >= .1:
                buffer.append((now, cv2.resize(frame, (640, 480))))
            while buffer and now - buffer[0][0] > 5:
                buffer.popleft()
            for candidate in candidates:
                key = (candidate['tracking_id'], candidate['ppe_type'])
                alerts[key] = now
                print(f"[SUSPECTED] person={key[0]} ppe={key[1]} duration={candidate['duration_seconds']:.1f}s")
                if args.submit_review_candidates:
                    # Reset observation after synchronous media processing: the gap
                    # must not count as continuously observed non-compliance.
                    save_candidate(args, detector, candidate, frame, list(buffer))
            active = {(p['tracking_id'], kind) for p in people for kind, state in p['ppe'].items()
                      if state['status'] == f'suspected_no_{kind}'}
            alerts = {key: ts for key, ts in alerts.items() if key in active}
            display = frame.copy()
            for d in detections:
                if d['class_name'] not in ('helmet', 'vest'):
                    continue
                x1, y1, x2, y2 = map(int, d['bbox'])
                cv2.rectangle(display, (x1, y1), (x2, y2), (0, 220, 0), 2)
                cv2.putText(display, d['class_name'], (x1, max(15, y1-5)),
                            cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 220, 0), 1)
            for p in people:
                x1, y1, x2, y2 = map(int, p['person_bbox'])
                warning = any((p['tracking_id'], kind) in alerts for kind in p['ppe'])
                color = (0, 0, 255) if warning else (0, 200, 255)
                cv2.rectangle(display, (x1, y1), (x2, y2), color, 2)
                for i, (kind, state) in enumerate(p['ppe'].items()):
                    label = f"{p['tracking_id']} {kind}: {state['status']}"
                    cv2.putText(display, label, (x1, max(15, y1-8)+i*18),
                                cv2.FONT_HERSHEY_SIMPLEX, .45, color, 1)
            cv2.imshow('PPE monitor - suspected cases require review', display)
            if cv2.waitKey(1) & 0xff in (27, ord('q')):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


def save_candidate(args, detector, candidate, frame, samples):
    from datetime import timedelta
    from backend.processing.events import EventSubmission, submit
    from backend.media.service import ingest_frame, ingest_file
    from inference_worker.clip_manager import create_clip

    end = datetime.now(timezone.utc)
    event_id = 'EVT_' + uuid4().hex
    event = EventSubmission(
        event_id=event_id, camera_id=args.camera_id,
        tracking_id=candidate['tracking_id'], ppe_type=candidate['ppe_type'],
        timestamp_start=end-timedelta(seconds=candidate['duration_seconds']),
        timestamp_end=end, confidence=None, review_only=True,
        frame_sample_count=candidate['frame_sample_count'], model_version=detector.model_version,
    )
    submit(event)
    ingest_frame(event_id, args.camera_id, frame)
    if len(samples) >= 2:
        # Irregular inference timing: use the measured span, not a fabricated 20 FPS.
        fps = (len(samples)-1)/(samples[-1][0]-samples[0][0])
        with tempfile.TemporaryDirectory(prefix='ppe_webcam_') as temp:
            path = create_clip([image for _, image in samples], Path(temp)/'clip.mp4', fps=fps)
            ingest_file(event_id, args.camera_id, 'video', 'clip', path)
    print(f'[REVIEW CANDIDATE] {event_id}')


if __name__ == '__main__':
    main()
