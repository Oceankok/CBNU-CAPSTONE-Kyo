"""Whole-event metadata inventory, not a calibrated classifier or media decoder."""
from datetime import datetime, timezone
from math import isfinite


def _time(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return parsed.astimezone(timezone.utc)


def coverage(start_at, end_at, intervals):
    """Union of valid manifest intervals clipped to the event, never double counted."""
    start, end = _time(start_at), _time(end_at)
    if end <= start:
        raise ValueError('positive event span required')
    spans = []
    for left, right in intervals:
        left, right = _time(left), _time(right)
        if right <= left:
            raise ValueError('positive interval required')
        left, right = max(start, left), min(end, right)
        if right > left:
            spans.append((left, right))
    cursor = start
    gaps = []
    covered = 0.0
    for left, right in sorted(spans):
        if left > cursor:
            gaps.append({'start_at': cursor.isoformat(), 'end_at': left.isoformat()})
        if right > cursor:
            covered += (right - max(cursor, left)).total_seconds()
            cursor = right
    if cursor < end:
        gaps.append({'start_at': cursor.isoformat(), 'end_at': end.isoformat()})
    return {'span_seconds': (end-start).total_seconds(), 'covered_seconds': covered,
            'uncovered_ranges': gaps, 'complete': not gaps}


def inventory(conn, event):
    """Read under caller snapshot. Paths, credentials and raw start payload are excluded."""
    ident = event['event_id']
    row = conn.execute('''SELECT phase,observed_seconds,gap_seconds,gap_count,
        final_segment_count,close_reason FROM processing_event_lifecycle WHERE event_id=?''', (ident,)).fetchone()
    if row is None:
        return {'version': 1, 'available': False, 'reason': 'continuous_provenance_missing',
                'automatic_confirmation_allowed': False}
    lifecycle = dict(row)
    segments = [dict(r) for r in conn.execute('''SELECT camera_id,sequence,request_id,
        capture_start_at,capture_end_at FROM processing_event_segment WHERE event_id=?
        ORDER BY camera_id,sequence''', (ident,))]
    camera_rows = list(conn.execute('SELECT camera_id,role FROM processing_event_camera WHERE event_id=? ORDER BY camera_id', (ident,)))
    cameras = []
    for camera in camera_rows:
        selected = [s for s in segments if s['camera_id'] == camera['camera_id']]
        try:
            manifest = coverage(event['timestamp_start'], event['timestamp_end'],
                                [(s['capture_start_at'], s['capture_end_at']) for s in selected])
        except (ValueError, TypeError, AttributeError, OverflowError):
            manifest = {'complete': False, 'error': 'invalid_manifest_interval'}
        cameras.append({'camera_id': camera['camera_id'], 'role': camera['role'],
                        'segment_count': len(selected), 'manifest_coverage': manifest})
    valid_observation = all(isinstance(lifecycle[k], (int, float)) and
                            not isinstance(lifecycle[k], bool) and isfinite(lifecycle[k]) and lifecycle[k] >= 0
                            for k in ('observed_seconds', 'gap_seconds', 'gap_count'))
    try:
        span = (_time(event['timestamp_end']) - _time(event['timestamp_start'])).total_seconds()
        valid_observation = valid_observation and span > 0 and lifecycle['observed_seconds'] + lifecycle['gap_seconds'] <= span + 1e-6
    except (ValueError, TypeError, AttributeError, OverflowError):
        span = None
        valid_observation = False
    return {'version': 1, 'available': True, 'phase': lifecycle['phase'],
            'event_span_seconds': span, 'observation_metadata_valid': valid_observation,
            'observed_seconds': lifecycle['observed_seconds'] if valid_observation else None,
            'gap_seconds': lifecycle['gap_seconds'] if valid_observation else None,
            'gap_count': lifecycle['gap_count'] if valid_observation else None,
            'unaccounted_seconds': max(0.0, span-lifecycle['observed_seconds']-lifecycle['gap_seconds']) if valid_observation else None,
            'close_reason': lifecycle['close_reason'],
            'expected_segments_per_camera': lifecycle['final_segment_count'], 'cameras': cameras,
            'automatic_confirmation_allowed': False,
            'limitations': ['manifest_coverage_not_decoded_video_or_ppe_evidence',
                'observation_metadata_is_producer_report_not_independent_measurement',
                'reference_cameras_have_no_per_frame_classifier_results',
                'event_confidence_is_not_a_calibrated_whole_event_probability']}
