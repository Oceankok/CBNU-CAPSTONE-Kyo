"""Opt-in, offline complete-scenario observations. No event filtering or decisions."""
import json
import math
import os
import re
from pathlib import Path
from inference_worker.person_tracker import PersonTracker
from inference_worker.ppe_rules import assess_ppe


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', value)


def collect_scenario(frames, detector, output, *, scenario_id, camera_id,
                     labels, max_frames=3600, max_seconds=120,
                     timing_basis='caller_relative_seconds'):
    """Consume all frames including no-person/present/unknown; never infer labels.

    frames yields (relative_seconds, image). Output is exclusive-create JSONL.
    A missing footer or completed=false is NOT a successful complete scenario.
    Labels describe the intended solo scenario, not per-track verified truth.
    """
    if not _identifier(scenario_id) or not _identifier(camera_id):
        raise ValueError('use anonymous scenario/camera identifiers')
    if (not isinstance(labels, dict) or set(labels) != {'helmet', 'vest'}
            or any(v not in ('worn', 'not_worn', 'ambiguous') for v in labels.values())):
        raise ValueError('explicit helmet and vest scenario labels required')
    if type(max_frames) is not int or not 1 <= max_frames <= 100000:
        raise ValueError('bounded max_frames required')
    if (isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float))
            or not math.isfinite(max_seconds) or not 0 < max_seconds <= 600):
        raise ValueError('max_seconds must be in (0,600]')
    model_version = detector.model_version
    if not isinstance(model_version, str) or not model_version:
        raise ValueError('model version required')
    supported = sorted(set(detector.supported_ppe) & {'helmet', 'vest'})
    target = Path(output)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    count = 0; previous = None; tracks = set(); empty = 0
    tracker = PersonTracker()
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        def emit(row):
            stream.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + '\n')
            stream.flush()
        emit({'type': 'scenario_header', 'version': 1, 'scenario_id': scenario_id,
              'camera_id': camera_id, 'session_id': scenario_id,
              'model_version': model_version, 'supported_ppe': supported,
              'scenario_labels': labels, 'timing_basis': timing_basis,
              'max_frames': max_frames, 'max_seconds': max_seconds,
              'automatic_confirmation_allowed': False,
              'limitations': ['scenario_labels_are_not_verified_track_labels',
                  'source_visibility_and_identity_are_heuristic',
                  'positive_box_absence_is_not_negative_class_confidence',
                  'no_operational_event_or_review_write',
                  'no_images_bounding_boxes_or_person_names_saved']})
        try:
            for timestamp, frame in frames:
                if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                        or not math.isfinite(timestamp) or timestamp < 0
                        or (previous is not None and timestamp <= previous)):
                    raise ValueError('invalid_frame_time')
                if previous is None and timestamp != 0:
                    raise ValueError('scenario_must_start_at_zero')
                if count >= max_frames or timestamp > max_seconds:
                    raise ValueError('scenario_limit_exceeded')
                rows = tracker.update(assess_ppe(detector.detect(frame), frame.shape, supported))
                people = []
                for row in rows:
                    track = str(row['tracking_id']); tracks.add(track)
                    people.append({'tracking_id': track,
                                   'ppe': {p: row['ppe'][p]['status'] for p in ('helmet', 'vest')}})
                if not people: empty += 1
                emit({'type': 'frame', 'index': count, 'timestamp': timestamp, 'people': people})
                count += 1; previous = timestamp
            if count < 2:
                raise ValueError('insufficient_frames')
        except BaseException as error:
            # Do not serialize arbitrary detector errors: they may contain local paths.
            emit({'type': 'scenario_footer', 'completed': False, 'frame_count': count,
                  'failure_kind': type(error).__name__, 'automatic_confirmation_allowed': False})
            raise
        summary = {'type': 'scenario_footer', 'completed': True, 'frame_count': count,
                   'last_frame_time': previous, 'empty_frame_count': empty,
                   'distinct_track_count': len(tracks), 'automatic_confirmation_allowed': False}
        emit(summary)
        return summary
