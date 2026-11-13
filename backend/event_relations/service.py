"""Conservative metadata previews, never identity claims or automatic merges."""
from datetime import datetime, timezone
from fastapi import HTTPException
from backend.event_time import parse


def capture_group_context(conn, event):
    """New registrations only. Never infer/backfill missing historical groups."""
    aware = all(parse(event[key]).tzinfo is not None for key in ('timestamp_start', 'timestamp_end'))
    conn.execute('INSERT INTO event_group_context(event_id,registered_at,observation_time_basis) VALUES(?,?,?)',
        (event['event_id'], datetime.now(timezone.utc).isoformat(), 'utc' if aware else 'unknown'))
    conn.execute('''INSERT INTO event_group_membership_snapshot(event_id,group_id,group_version)
        SELECT ?,g.group_id,g.version FROM camera_group g
        JOIN camera_group_member m ON m.group_id=g.group_id WHERE m.camera_id=?''',
        (event['event_id'], event['camera_id']))


def preview(conn, event_id, limit, offset, *, peer_id=None):
    event = conn.execute('SELECT * FROM candidate_event WHERE event_id=?', (event_id,)).fetchone()
    if not event:
        raise HTTPException(404, 'Event not found')
    result = {'event_id': event_id, 'mode': 'metadata_preview', 'scope': 'registered_group_version',
              'interval_basis': 'observation_envelope', 'clock_tolerance_seconds': 0,
              'persisted': False, 'automatic_merge': False, 'statistics_changed': False,
              'limit': limit, 'offset': offset, 'total': 0, 'items': [], 'eligible': False, 'has_more': False, 'next_offset': None, 'pagination_truncated': False}
    context = conn.execute('SELECT * FROM event_group_context WHERE event_id=?', (event_id,)).fetchone()
    if not context:
        return {**result, 'reason': 'registration_group_snapshot_unavailable'}
    result['group_snapshot_registered_at'] = context['registered_at']
    if conn.execute('SELECT 1 FROM event_merge WHERE source_event_id=?', (event_id,)).fetchone():
        return {**result, 'reason': 'event_was_merged'}
    if context['observation_time_basis'] != 'utc':
        return {**result, 'reason': 'observation_timezone_unknown'}
    start, end = parse(event['timestamp_start']), parse(event['timestamp_end'])
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        return {**result, 'reason': 'positive_observation_interval_unavailable'}
    if not conn.execute('SELECT 1 FROM event_group_membership_snapshot WHERE event_id=?', (event_id,)).fetchone():
        return {**result, 'reason': 'no_registered_camera_groups'}
    # Repository registration normalizes aware instants to UTC fixed-width ISO.
    # Group version equality is conservative: a later membership/name revision
    # is not silently treated as the historical overlapping-view declaration.
    where = '''FROM candidate_event e JOIN event_group_context c ON c.event_id=e.event_id
        WHERE e.event_id!=? AND e.camera_id!=? AND e.ppe_type=?
        AND c.observation_time_basis='utc'
        AND e.timestamp_start<? AND e.timestamp_end>? AND e.timestamp_end>e.timestamp_start
        AND NOT EXISTS(SELECT 1 FROM event_merge m WHERE m.source_event_id=e.event_id)
        AND EXISTS(SELECT 1 FROM event_group_membership_snapshot mine
            JOIN event_group_membership_snapshot other ON other.group_id=mine.group_id
                AND other.group_version=mine.group_version
            WHERE mine.event_id=? AND other.event_id=e.event_id)'''
    params = (event_id, event['camera_id'], event['ppe_type'], event['timestamp_end'], event['timestamp_start'], event_id)
    if peer_id is not None:
        where += ' AND e.event_id=?'
        params = (*params, peer_id)
    total = conn.execute('SELECT COUNT(*) ' + where, params).fetchone()[0]
    rows = conn.execute('SELECT e.event_id,e.camera_id,e.ppe_type,e.timestamp_start,e.timestamp_end,e.event_status '
        + where + ' ORDER BY e.timestamp_start DESC,e.event_id DESC LIMIT ? OFFSET ?', (*params, limit, offset)).fetchall()
    items = []
    for row in rows:
        overlap = (min(end, parse(row['timestamp_end'])) - max(start, parse(row['timestamp_start']))).total_seconds()
        groups = [dict(g) for g in conn.execute('''SELECT mine.group_id,mine.group_version
            FROM event_group_membership_snapshot mine JOIN event_group_membership_snapshot other
            ON other.group_id=mine.group_id AND other.group_version=mine.group_version
            WHERE mine.event_id=? AND other.event_id=? ORDER BY mine.group_id''', (event_id, row['event_id']))]
        items.append({**dict(row), 'overlap_seconds': overlap, 'shared_group_snapshots': groups,
                      'reason': 'same_registered_group_version_ppe_overlapping_observation'})
    return {**result, 'eligible': True, 'reason': None, 'total': total, 'items': items,
            **page_state(total, offset, len(items))}


def page_state(total, offset, returned):
    next_offset = offset + returned
    remaining = next_offset < total
    allowed = remaining and returned > 0 and next_offset <= 10000
    return {'has_more': allowed, 'next_offset': next_offset if allowed else None,
            'pagination_truncated': remaining and not allowed}


def begin_tag_snapshot(conn):
    """Keep page/details and derived tags on one database snapshot."""
    if getattr(conn, 'dialect', 'sqlite') == 'postgresql':
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    else:
        conn.execute('BEGIN')


def automatic_tag(conn, event_id):
    """Derived indication only: never recreate/release saved evidence records."""
    result = preview(conn, event_id, 1, 0)
    records = conn.execute('SELECT COUNT(*) AS total,\n        SUM(CASE WHEN released_at IS NULL THEN 1 ELSE 0 END) AS saved,\n        SUM(CASE WHEN released_at IS NOT NULL THEN 1 ELSE 0 END) AS released\n        FROM saved_event_relation WHERE event_a=? OR event_b=?', (event_id, event_id)).fetchone()
    return {
        'tag': 'suspected_duplicate' if result['total'] else None,
        'candidate_count': result['total'], 'eligible': result['eligible'],
        'reason': result['reason'], 'rule_version': 'registered_group_version_positive_observation_overlap_v1',
        'mode': 'computed_metadata', 'persisted': False,
        'identity_confirmed': False, 'automatic_merge': False, 'statistics_changed': False,
        'saved_record_count': records['saved'] or 0,
        'released_record_count': records['released'] or 0,
        'saved_counts_are_current_matches': False,
        'release_suppresses_computed_tag': False,
    }
