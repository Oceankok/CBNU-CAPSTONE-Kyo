"""Pure wire-payload builders for the opt-in continuous-event API.

No networking, credentials, persistence or sequencing is performed here.
The future adapter must durably store each exact body before sending it.
"""
from datetime import datetime, timezone
import hashlib
import json
import re


def _session(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('session_id')
    return value


def _time(value):
    if isinstance(value, bool):
        raise ValueError('timestamp')
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec='microseconds')


def _observation(transition):
    return {'observed_seconds': transition['observed_seconds'],
            'gap_seconds': transition['observation_gap_seconds'],
            'gap_count': transition['observation_gap_count']}


def start_payload(transition, *, session_id, model_version, capture_context_id=None):
    if transition['kind'] != 'start':
        raise ValueError('start transition required')
    policy = json.dumps(transition['policy_scope_key'], sort_keys=True,
                        separators=(',', ':'), allow_nan=False)
    if capture_context_id is not None and (not isinstance(capture_context_id,str) or
            not re.fullmatch('[A-Za-z0-9_-]{1,100}',capture_context_id)):
        raise ValueError('capture_context_id')
    return {**({'capture_context_id':capture_context_id} if capture_context_id is not None else {}),
            'event_id': transition['event_id'], 'camera_id': transition['camera_id'],
            'tracking_id': transition['tracking_id'], 'ppe_type': transition['ppe_type'],
            'timestamp_start': _time(transition['start_time']),
            'timestamp_end': _time(transition['end_time']),
            'confidence': transition['verified_ppe_confidence'],
            'frame_sample_count': transition['frame_sample_count'],
            'model_version': model_version, 'review_only': transition['review_only'],
            'session_id': _session(session_id),
            'policy_key': hashlib.sha256(policy.encode('utf-8')).hexdigest(),
            **_observation(transition)}


def update_payload(transition, *, session_id, sequence, complete=None,
                   final_segment_count=None):
    if transition['kind'] not in ('progress', 'close'):
        raise ValueError('progress or close transition required')
    if type(sequence) is not int or not 1 <= sequence <= 2147483647:
        raise ValueError('sequence')
    body = {'session_id': _session(session_id), 'sequence': sequence,
            'kind': transition['kind'], 'timestamp_end': _time(transition['end_time']),
            'frame_sample_count': transition['frame_sample_count'],
            'confidence': transition['verified_ppe_confidence'],
            **_observation(transition)}
    if transition['kind'] == 'close':
        if (type(complete) is not bool or type(final_segment_count) is not int
                or not 0 <= final_segment_count <= 10000):
            raise ValueError('explicit close manifest required')
        body.update(close_reason=transition['close_reason'], complete=complete,
                    final_segment_count=final_segment_count)
    elif complete is not None or final_segment_count is not None:
        raise ValueError('progress cannot carry close manifest')
    return body


def segment_payload(segment, *, session_id, event_sequence):
    """The event sequence is NOT necessarily the camera spool's sequence."""
    if type(event_sequence) is not int or not 0 <= event_sequence <= 9999:
        raise ValueError('event_sequence')
    if not 0 < segment.capture_end - segment.capture_start <= 60:
        raise ValueError('segment duration')
    return {'session_id': _session(session_id), 'sequence': event_sequence,
            'capture_start_at': _time(segment.capture_start),
            'capture_end_at': _time(segment.capture_end)}


def capture_payload(status, *, camera_id, clock, session_id, sequence,
                    time_basis, required_end, previous_missing_ranges=()):
    """Map an isolated coordinator snapshot, not a durable upload authorization.

    required_end is the server's current last-positive + post-roll boundary.
    Provisional encoded ranges beyond that boundary cannot be reported/uploaded.
    Clock validation and a stable epoch conversion remain caller obligations.
    Caller must supply persisted previous_missing_ranges; this stateless builder
    cannot infer prior reports or authorize recovery from an incomplete state.
    """
    if type(sequence) is not int or not 1 <= sequence <= 2147483647:
        raise ValueError('sequence')
    if time_basis not in ('source_utc_corrected', 'shared_monotonic'):
        raise ValueError('time_basis')
    import math
    if (not math.isfinite(clock.offset) or not math.isfinite(clock.error_bound)
            or clock.error_bound < 0 or not math.isfinite(required_end)
            or required_end <= status['start']):
        raise ValueError('clock or required range')
    # Wire timestamps have microsecond resolution. Compare at that same
    # resolution, not raw epoch-float arithmetic (one ULP is ~0.24 us).
    canonical=lambda t:datetime.fromtimestamp(t,tz=timezone.utc).timestamp()
    required_end=canonical(required_end)
    required_start=canonical(status['start'])
    cap = status['cameras'][camera_id]
    ranges = []
    cursor = required_start
    for segment in cap['segments']:
        a,b=canonical(segment.capture_start),canonical(segment.capture_end)
        if (a < cursor or b <= a or b > required_end):
            raise ValueError('provisional segment outside reportable range')
        ranges.append({'start_at': _time(segment.capture_start),
                       'end_at': _time(segment.capture_end)})
        cursor = canonical(segment.capture_end)
    reasons = list(cap['reasons'])
    if not clock.verified:
        reasons.append('clock_unverified')
    state = 'incomplete' if reasons else 'collecting'
    if cap['done'] and not reasons:
        if status['end'] is None or canonical(status['end']) != required_end:
            raise ValueError('final required range mismatch')
        cursor = required_start
        for segment in cap['segments']:
            if abs(canonical(segment.capture_start) - cursor) > 1e-8:
                raise ValueError('completed capture gap')
            cursor = canonical(segment.capture_end)
        if abs(cursor-required_end) > 1e-8:
            raise ValueError('completed capture gap')
        state = 'complete'
    missing = [dict(row) for row in previous_missing_ranges]
    if missing and state != 'incomplete':
        raise ValueError('previous incomplete capture requires recovery policy')
    if state == 'incomplete':
        cursor = required_start
        for segment in cap['segments']:
            if canonical(segment.capture_start) > cursor:
                missing.append({'start_at': _time(cursor), 'end_at': _time(segment.capture_start),
                                'reason': reasons[0][:100]})
            cursor = canonical(segment.capture_end)
        if cursor < required_end:
            missing.append({'start_at': _time(cursor), 'end_at': _time(required_end),
                            'reason': reasons[0][:100]})
    missing = [row for i, row in enumerate(missing) if row not in missing[:i]]
    return {'session_id': _session(session_id), 'sequence': sequence, 'state': state,
            'captured_ranges': ranges, 'missing_ranges': missing,
            'earliest_available_at': ranges[0]['start_at'] if ranges else None,
            'time_basis': time_basis, 'clock_offset_seconds': clock.offset,
            'clock_uncertainty_seconds': clock.error_bound,
            'reason': reasons[0][:100] if reasons else None}
