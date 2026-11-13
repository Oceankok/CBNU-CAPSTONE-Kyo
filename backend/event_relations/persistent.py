"""Explicit evidence bookmarks, not suppression or permanent audit history."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import HTTPException
from backend.event_relations.service import preview, page_state

def run_write(operation):
    # READ COMMITTED is deliberate: advisory-lock waiting must not freeze the
    # evidence snapshot before the preceding writer commits. Normal evidence
    # writers share this schema-scoped lock. External SQL is outside this contract.
    from backend.db.connection import connect, begin_write
    with connect() as conn:
        if getattr(conn, 'dialect', 'sqlite') == 'postgresql':
            conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        begin_write(conn)
        result = operation(conn)
    return result


RULE = 'registered_group_version_positive_observation_overlap_v1'


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def evidence(conn, first, second):
    a, b = sorted((first, second))
    if a == b:
        raise HTTPException(422, 'distinct_events_required')
    rows = []
    for ident in (a, b):
        row = conn.execute('SELECT event_id,camera_id,ppe_type,timestamp_start,timestamp_end FROM candidate_event WHERE event_id=?', (ident,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Event not found')
        rows.append(dict(row))
    result = preview(conn, a, 1, 0, peer_id=b)
    if not result['items']:
        return {'eligible': False, 'reason': result['reason'] or 'pair_not_matching', 'fingerprint': None, 'evidence': None}
    item = result['items'][0]
    payload = {'rule_version': RULE, 'events': rows,
               'shared_group_snapshots': item['shared_group_snapshots'],
               'interval_basis': 'observation_envelope', 'clock_tolerance_seconds': 0}
    fingerprint = hashlib.sha256(canonical(payload).encode()).hexdigest()
    return {'eligible': True, 'reason': None, 'fingerprint': fingerprint, 'evidence': payload}


def serialize(conn, row):
    current = evidence(conn, row['event_a'], row['event_b'])
    return {'relation_id': row['relation_id'], 'event_a': row['event_a'], 'event_b': row['event_b'],
            'fingerprint': row['fingerprint'], 'evidence': json.loads(row['evidence_json']),
            'saved_by': row['saved_by'], 'saved_at': row['saved_at'],
            'released_by': row['released_by'], 'released_at': row['released_at'],
            'state': 'released' if row['released_at'] else 'saved',
            'stale': current['fingerprint'] != row['fingerprint'],
            'current_fingerprint': current['fingerprint'], 'current_reason': current['reason'],
            'suppresses_preview': False, 'automatic_merge': False, 'statistics_changed': False}


def save(conn, first, second, expected, actor):
    current = evidence(conn, first, second)
    if not current['eligible'] or current['fingerprint'] != expected:
        raise HTTPException(409, 'relation_evidence_changed_or_ineligible')
    existing = conn.execute('SELECT * FROM saved_event_relation WHERE fingerprint=?', (expected,)).fetchone()
    if existing:
        return serialize(conn, existing)
    a, b = sorted((first, second))
    ident = 'REL_' + uuid4().hex
    conn.execute("""INSERT INTO saved_event_relation(relation_id,event_a,event_b,fingerprint,evidence_json,saved_by,saved_at)
        VALUES(?,?,?,?,?,?,?)""", (ident,a,b,expected,canonical(current['evidence']),actor,now()))
    return serialize(conn, conn.execute('SELECT * FROM saved_event_relation WHERE relation_id=?', (ident,)).fetchone())


def release(conn, relation_id, expected, actor):
    row = conn.execute('SELECT * FROM saved_event_relation WHERE relation_id=?', (relation_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'Saved relation not found')
    # Targets the saved immutable evidence, not whichever live candidate exists now.
    if row['fingerprint'] != expected:
        raise HTTPException(409, 'saved_relation_fingerprint_conflict')
    if row['released_at'] is None:
        conn.execute('UPDATE saved_event_relation SET released_by=?,released_at=? WHERE relation_id=?', (actor,now(),relation_id))
    return serialize(conn, conn.execute('SELECT * FROM saved_event_relation WHERE relation_id=?', (relation_id,)).fetchone())


def listing(conn, event_id, limit, offset):
    if conn.execute('SELECT 1 FROM candidate_event WHERE event_id=?', (event_id,)).fetchone() is None:
        raise HTTPException(404, 'Event not found')
    total = conn.execute('SELECT COUNT(*) FROM saved_event_relation WHERE event_a=? OR event_b=?', (event_id,event_id)).fetchone()[0]
    rows = conn.execute('SELECT * FROM saved_event_relation WHERE event_a=? OR event_b=? ORDER BY saved_at DESC,relation_id DESC LIMIT ? OFFSET ?', (event_id,event_id,limit,offset)).fetchall()
    return {'items': [serialize(conn, row) for row in rows], 'total': total, 'limit': limit, 'offset': offset,
            **page_state(total, offset, len(rows))}


def reconcile(conn, event_id, actor, limit=20, offset=0):
    """Explicit admin scan: save only never-reviewed pairs from current evidence.

    Any saved/released/stale record for a pair blocks automatic re-saving. Only
    the existing explicit evidence review/save endpoint can create another
    evidence version. No scheduler or ingest hook invokes this function.
    """
    page = preview(conn, event_id, limit, offset)
    items = []
    for peer in page['items']:
        a, b = sorted((event_id, peer['event_id']))
        existing = conn.execute('''SELECT relation_id,released_at FROM saved_event_relation
            WHERE event_a=? AND event_b=? ORDER BY saved_at DESC,relation_id DESC LIMIT 1''', (a,b)).fetchone()
        if existing is not None:
            items.append({'peer_event_id': peer['event_id'], 'action': 'existing_pair_preserved',
                          'relation_id': existing['relation_id'],
                          'existing_state': 'released' if existing['released_at'] else 'saved'})
            continue
        current = evidence(conn, event_id, peer['event_id'])
        if not current['eligible']:
            # Defensive; normal writers share the lock used by this endpoint.
            items.append({'peer_event_id': peer['event_id'], 'action': 'ineligible', 'reason': current['reason']})
            continue
        result = save(conn, event_id, peer['event_id'], current['fingerprint'], actor)
        items.append({'peer_event_id': peer['event_id'], 'action': 'created', 'relation_id': result['relation_id']})
    return {'event_id': event_id, 'mode': 'explicit_current_metadata_scan',
            'rule_version': RULE, 'automatic_merge': False, 'statistics_changed': False,
            'released_pairs_automatically_resaved': False, 'existing_pairs_automatically_refreshed': False,
            'candidate_total': page['total'], 'limit': limit, 'offset': offset,
            'eligible': page['eligible'], 'reason': page['reason'], 'items': items,
            'created_count': sum(item['action'] == 'created' for item in items),
            **{key: page[key] for key in ('has_more','next_offset','pagination_truncated')}}
