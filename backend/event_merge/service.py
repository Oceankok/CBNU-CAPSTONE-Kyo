"""Explicit administrator merges; no automatic incident identity inference."""
import json
from datetime import datetime, timezone

from fastapi import HTTPException

from backend.db.connection import begin_write
from backend.db.event_repository import get_connection


def require_unmerged(conn, event_id):
    if conn.execute('SELECT 1 FROM event_merge WHERE source_event_id=?', (event_id,)).fetchone():
        raise ValueError('event_merged')


def metadata(conn, event_id):
    parent = conn.execute('SELECT target_event_id FROM event_merge WHERE source_event_id=?', (event_id,)).fetchone()
    rows = conn.execute('SELECT * FROM event_merge WHERE target_event_id=? ORDER BY merged_at,source_event_id', (event_id,)).fetchall()
    history = []
    for row in rows:
        item = dict(row)
        item['media_ids'] = json.loads(item.pop('media_ids_json'))
        item['request_ids'] = json.loads(item.pop('request_ids_json'))
        history.append(item)
    return {'merged_into_event_id': parent[0] if parent else None,
            'merged_event_ids': [row['source_event_id'] for row in rows],
            'merge_version': len(rows), 'merge_history': history}


def eligible(conn, event_id):
    row = conn.execute('SELECT * FROM candidate_event WHERE event_id=?', (event_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'event_not_found')
    if row['event_status'] != 'pending' or conn.execute('SELECT 1 FROM event_review WHERE event_id=?', (event_id,)).fetchone():
        raise HTTPException(409, 'unreviewed_event_required')
    if conn.execute('SELECT 1 FROM event_merge WHERE source_event_id=?', (event_id,)).fetchone():
        raise HTTPException(409, 'event_already_merged')
    retention = conn.execute('SELECT decision,version FROM event_retention WHERE event_id=?', (event_id,)).fetchone()
    if retention and (retention['decision'] != 'pending' or retention['version'] != 0):
        raise HTTPException(409, 'undecided_retention_required')
    for table in ('event_retention_history', 'event_consent', 'event_review_history'):
        if conn.execute(f'SELECT 1 FROM {table} WHERE event_id=?', (event_id,)).fetchone():
            raise HTTPException(409, 'event_history_prevents_merge')
    if conn.execute("SELECT 1 FROM event_clip_request WHERE event_id=? AND status IN ('pending','failed')", (event_id,)).fetchone():
        raise HTTPException(409, 'clip_delivery_pending')
    if conn.execute("SELECT 1 FROM event_media WHERE event_id=? AND (status IN ('registered','processing','delete_pending') OR processing_started_at IS NOT NULL)", (event_id,)).fetchone():
        raise HTTPException(409, 'media_processing_pending')
    if conn.execute("""SELECT 1 FROM processing_job j JOIN event_media m ON m.media_id=j.media_id
            WHERE m.event_id=? AND j.status IN ('queued','running')""", (event_id,)).fetchone():
        raise HTTPException(409, 'media_processing_pending')
    return dict(row)


def merge(target_id, source_ids, expected_version, reason, actor):
    if target_id in source_ids or len(set(source_ids)) != len(source_ids):
        raise HTTPException(422, 'distinct_merge_events_required')
    with get_connection() as conn:
        begin_write(conn)
        target = eligible(conn, target_id)
        if metadata(conn, target_id)['merge_version'] != expected_version:
            raise HTTPException(409, 'event_merge_version_conflict')
        sources = [eligible(conn, ident) for ident in source_ids]
        for row in sources:
            if row['ppe_type'] != target['ppe_type']:
                raise HTTPException(409, 'matching_ppe_type_required')
            if conn.execute('SELECT 1 FROM event_merge WHERE target_event_id=?', (row['event_id'],)).fetchone():
                raise HTTPException(409, 'merge_target_cannot_be_source')
        stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        quarters = set()
        for row in sources:
            ident = row['event_id']
            media = [item[0] for item in conn.execute('SELECT media_id FROM event_media WHERE event_id=? ORDER BY media_id', (ident,))]
            requests = [item[0] for item in conn.execute('SELECT request_id FROM event_clip_request WHERE event_id=? ORDER BY request_id', (ident,))]
            conn.execute('''INSERT INTO event_merge(source_event_id,target_event_id,merged_by,merged_at,reason,media_ids_json,request_ids_json)
                VALUES(?,?,?,?,?,?,?)''', (ident, target_id, actor, stamp, reason, json.dumps(media), json.dumps(requests)))
            # IDs and files stay unchanged; cameras, timestamps and processing history remain intact.
            conn.execute("UPDATE event_media SET event_id=?,role='reference' WHERE event_id=?", (target_id, ident))
            conn.execute("UPDATE event_clip_request SET event_id=?,role='reference' WHERE event_id=?", (target_id, ident))
            conn.execute("UPDATE candidate_event SET event_status='merged' WHERE event_id=?", (ident,))
            if row['timestamp_start']:
                from backend.db.event_repository import _get_quarter_from_timestamp
                quarters.add(_get_quarter_from_timestamp(row['timestamp_start']))
        # Only candidate counts change: all sources were unreviewed. Keep approved education intact.
        from backend.db.event_repository import _get_quarter_date_range
        for quarter in quarters:
            start, end = _get_quarter_date_range(quarter)
            count = conn.execute("SELECT COUNT(*) FROM candidate_event WHERE timestamp_start>=? AND timestamp_start<? AND event_status!='merged'", (start, end)).fetchone()[0]
            conn.execute('UPDATE quarterly_summary SET candidate_count=? WHERE quarter=?', (count, quarter))
        return {'event_id': target_id, **metadata(conn, target_id)}
