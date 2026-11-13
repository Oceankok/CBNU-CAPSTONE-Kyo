"""Reference stream/context resolution for the legacy processing path."""

from datetime import datetime, timedelta, timezone


def _utc(value):
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def _covers(chunks, start, end):
    selected = sorted(
        (row for row in chunks
         if _utc(row['capture_start_at']) < end
         and _utc(row['capture_start_at']) + timedelta(milliseconds=row['duration_ms']) > start),
        key=lambda row: _utc(row['capture_start_at']),
    )
    if not selected:
        return False
    covered = start
    for prior, row in zip(selected, selected[1:]):
        if row['sequence'] != prior['sequence'] + 1:
            return False
    for row in selected:
        stamp = _utc(row['capture_start_at'])
        if (stamp - covered).total_seconds() > 0.25:
            return False
        covered = max(covered, stamp + timedelta(milliseconds=row['duration_ms']))
    return (end - covered).total_seconds() <= 0.25


def resolve_reference_context(streams, chunks_by_stream, start, end, *, has_context_registry=False,
                              active_contexts=()):
    """Resolve one exact stream/context or defer while an active stream fills."""
    candidates = []
    context_streams = [row for row in streams if row.get('capture_context_id') is not None]
    applicable = [context for context in active_contexts
                  if _utc(context['valid_from']) <= start
                  and (context.get('valid_until') is None or end <= _utc(context['valid_until']))]
    if applicable:
        allowed = {(row['context_id'], row['session_id'], row['assignment_version'])
                   for row in applicable}
        context_streams = [row for row in context_streams
                           if (row.get('capture_context_id'), row.get('capture_session_id'),
                               row.get('assignment_version')) in allowed]
    for stream in context_streams:
        if _covers(chunks_by_stream.get(stream['stream_id'], ()), start, end):
            candidates.append(stream)
    identities = {(row['capture_context_id'], row['capture_session_id'], row['assignment_version'])
                  for row in candidates}
    if len(candidates) > 1:
        error = 'reference_context_ambiguous' if len(identities) > 1 else 'reference_stream_ambiguous'
        return {'capture_context_state': 'unavailable', 'capture_context_error': error}
    if len(identities) == 1:
        stream = candidates[0]
        return {'capture_context_state': 'bound', 'stream_id': stream['stream_id'],
                'capture_context_id': stream['capture_context_id'],
                'capture_session_id': stream['capture_session_id'],
                'assignment_version': stream['assignment_version']}
    active = [row for row in context_streams if row.get('status') != 'closed']
    if len(active) == 1:
        stream = active[0]
        return {'capture_context_state': 'pending', 'stream_id': stream['stream_id'],
                'capture_context_id': stream['capture_context_id'],
                'capture_session_id': stream['capture_session_id'],
                'assignment_version': stream['assignment_version']}
    if len(active) > 1:
        return {'capture_context_state': 'unavailable', 'capture_context_error': 'reference_stream_ambiguous'}
    if context_streams:
        return {'capture_context_state': 'unavailable', 'capture_context_error': 'reference_context_unavailable'}
    if len(applicable) == 1:
        context = applicable[0]
        return {'capture_context_state': 'pending',
                'capture_context_id': context['context_id'],
                'capture_session_id': context['session_id'],
                'assignment_version': context['assignment_version']}
    if len(applicable) > 1:
        return {'capture_context_state': 'unavailable', 'capture_context_error': 'reference_context_ambiguous'}
    if active_contexts:
        return {'capture_context_state': 'unavailable', 'capture_context_error': 'reference_context_unavailable'}
    if has_context_registry:
        return {'capture_context_state': 'pending'}
    return {'capture_context_state': 'legacy'}
