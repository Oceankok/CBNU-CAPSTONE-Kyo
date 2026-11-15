"""Opt-in, verified-text translation cache. Database transactions contain no HTTP calls."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.education.models import MaterialContent
from backend.education.languages import candidates, normalize, requested
from backend.education import translations


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def content(row):
    return {'title': row['title'], 'body': row['body'], 'checklist': json.loads(row['checklist_json'])}


def digest(row):
    return hashlib.sha256(json.dumps(content(row), ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def policy():
    """A deployment-owned file, never supplied through a worker request.

    Verification is an operator assertion about exact text/language pairs, not
    an NLP classifier or evidence that this repository's samples were validated.
    Invalid/missing settings fail closed. No language is approved by default.
    """
    try:
        path = os.environ.get('PPE_AUTO_TRANSLATION_POLICY', '')
        if not path or Path(path).stat().st_size > 65536:
            return None
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if type(data.get('enabled')) is not bool or type(data.get('allow_external_processing')) is not bool:
            return None
        for key, ceiling in [('daily_requests', 1000), ('total_requests', 10000),
                             ('total_source_chars', 10000000), ('max_source_chars', 16000), ('max_queued', 1000)]:
            if type(data.get(key)) is not int or not 1 <= data[key] <= ceiling:
                return None
        entries = data['verified_catalog']
        if not isinstance(entries, list) or len(entries) > 128:
            return None
        for entry in entries:
            if (entry.get('classification') != 'basic_ppe' or entry.get('safety_critical') is not False
                    or entry.get('representative_validation_passed') is not True
                    or not isinstance(entry.get('validation_reference'), str)
                    or not 1 <= len(entry['validation_reference']) <= 500
                    or entry.get('ppe_type') not in {'helmet', 'vest', 'gloves', 'goggles', 'safety_shoes', 'mask', 'harness', 'hearing_protection'}
                    or not isinstance(entry.get('content_sha256'), str)
                    or len(entry['content_sha256']) != 64
                    or any(c not in '0123456789abcdef' for c in entry['content_sha256'])
                    or normalize(entry.get('language', '')) != entry.get('language')
                    or entry['language'].split('-')[0] == 'ko'):
                return None
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def eligible(row, language, configured):
    if configured is None or sum(len(s) for s in [row['title'], row['body'], *json.loads(row['checklist_json'])]) > configured['max_source_chars']:
        return None
    hashed = digest(row)
    return next((entry for entry in configured['verified_catalog']
                 if entry['content_sha256'] == hashed and entry['language'] == language
                 and entry['ppe_type'] == row['ppe_type']), None)


def generation_enabled(configured):
    if not configured or not configured['enabled'] or not configured['allow_external_processing']:
        return False
    from backend.education import translation_provider
    return translation_provider.availability()['configured']


def source_row(conn, kind, identifier):
    table, column = ('education_material', 'material_id') if kind == 'material' else ('education_bundle', 'bundle_id')
    return conn.execute(f'SELECT * FROM {table} WHERE {column}=?', (identifier,)).fetchone()


def visible_source(conn, user_id, kind, identifier):
    """Same publication, membership, quarter and topic boundaries as worker GETs."""
    from backend.zones.membership import zones_for
    row = source_row(conn, kind, identifier)
    if row is None or row['status'] != 'published':
        raise HTTPException(404, 'education_translation_source_unavailable')
    zones = set(zones_for(conn, user_id))
    scopes = json.loads(row['sources_json']) if kind == 'bundle' else [row]
    for scope in scopes:
        if scope['zone_name'] not in zones:
            continue
        latest = conn.execute('SELECT MAX(quarter) FROM education_recommendation WHERE zone_name=?', (scope['zone_name'],)).fetchone()[0]
        if latest == row['quarter'] and conn.execute('''SELECT 1 FROM education_recommendation
            WHERE quarter=? AND zone_name=? AND ppe_type=? AND education_topic=?''',
            (scope['quarter'], scope['zone_name'], scope['ppe_type'], scope['education_topic'])).fetchone():
            return row
    raise HTTPException(404, 'education_translation_source_unavailable')


def job(conn, kind, row, language):
    identifier = row['material_id'] if kind == 'material' else row['bundle_id']
    return conn.execute('''SELECT * FROM education_translation_job
        WHERE source_kind=? AND source_id=? AND source_version=? AND language=?''',
        (kind, identifier, row['version'], language)).fetchone()


def generation_language(row, language, configured):
    for candidate in candidates(language):
        if candidate == 'ko':
            break
        if eligible(row, candidate, configured):
            return candidate
    return None


def selection(conn, kind, row, language):
    """Read-only resolution, with reviewed publications ahead of any AI cache."""
    identifier = row['material_id'] if kind == 'material' else row['bundle_id']
    column = 'material_id' if kind == 'material' else 'bundle_id'
    available = {r[0] for r in conn.execute(f'''SELECT language FROM education_translation
        WHERE {column}=? AND source_version=? AND status='published' ''', (identifier, row['version'])).fetchall()}
    configured = policy()
    if configured:
        available.update(e['language'] for e in configured['verified_catalog'] if eligible(row, e['language'], configured))
    meta = {'status': 'original', 'origin': 'ko', 'source_kind': kind, 'source_id': identifier,
            'source_version': row['version'], 'available_languages': sorted(available),
            'request_language': None, 'poll_after_seconds': None}
    translated = translations.published_content(conn, identifier, row['version'], language, kind=kind)
    if translated is not None:
        meta.update(status='manual', origin='reviewed')
        return translated, meta
    if requested(language).split('-')[0] == 'ko':
        return None, meta
    for candidate in candidates(language):
        if candidate == 'ko':
            break
        if not eligible(row, candidate, configured):
            continue
        cached = job(conn, kind, row, candidate)
        if cached and cached['content_digest'] == digest(row) and cached['state'] == 'ready':
            meta.update(status='ready', origin='ai')
            return cached, meta
    if not generation_enabled(configured):
        meta['status'] = 'disabled'
        return None, meta
    target = generation_language(row, language, configured)
    if target is None:
        meta['status'] = 'ineligible'
        return None, meta
    meta['request_language'] = target
    cached = job(conn, kind, row, target)
    state = cached['state'] if cached else None
    meta['status'] = ('pending' if state in ('queued', 'dispatched') else 'failed') if cached else 'missing'
    if state == 'dispatched' and cached['deadline_at'] < now():
        meta['status'] = 'failed'
    if state == 'queued' and cached['created_at'] < (datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat(timespec='seconds'):
        meta['status'] = 'failed'
    if meta['status'] == 'pending':
        meta['poll_after_seconds'] = 3
    return None, meta


def request(user_id, payload):
    with get_connection() as conn:
        begin_write(conn)
        row = visible_source(conn, user_id, payload.source_kind, payload.source_id)
        if row['version'] != payload.source_version:
            raise HTTPException(409, 'education_translation_source_changed')
        _, meta = selection(conn, payload.source_kind, row, payload.language)
        if meta['status'] in ('manual', 'ready'):
            return {'status': meta['status'], 'poll_after_seconds': None}
        if meta['status'] == 'pending':
            return {'status': 'pending', 'poll_after_seconds': 3}
        if meta['status'] != 'missing':
            raise HTTPException(409, 'education_auto_translation_' + meta['status'])
        configured = policy()
        target = generation_language(row, payload.language, configured)
        entry = eligible(row, target, configured) if target else None
        if entry is None:
            raise HTTPException(409, 'education_auto_translation_ineligible')
        if conn.execute("SELECT COUNT(*) FROM education_translation_job WHERE state='queued'").fetchone()[0] >= configured['max_queued']:
            raise HTTPException(429, 'education_auto_translation_queue_full')
        conn.execute('''INSERT INTO education_translation_job(job_id,source_kind,source_id,source_version,language,
            content_digest,validation_reference,state,created_at) VALUES(?,?,?,?,?,?,?,'queued',?)
            ON CONFLICT(source_kind,source_id,source_version,language) DO NOTHING''',
            (str(uuid4()), payload.source_kind, payload.source_id, row['version'], target,
             digest(row), entry['validation_reference'], now()))
    return {'status': 'pending', 'poll_after_seconds': 3}


def admin_cached(kind, identifier, source_version):
    """Copy-only view: automatic job IDs are never manual translation IDs."""
    with get_connection() as conn:
        row = translations.source(conn, identifier, source_version, kind=kind)
        items = []
        for cached in conn.execute('''SELECT * FROM education_translation_job
                WHERE source_kind=? AND source_id=? AND source_version=? ORDER BY language''',
                (kind, identifier, source_version)).fetchall():
            valid = cached['state'] == 'ready' and cached['content_digest'] == digest(row)
            items.append({'source_kind': kind, 'source_id': identifier, 'source_version': source_version,
                          'language': cached['language'], 'status': cached['state'] if valid or cached['state'] != 'ready' else 'stale',
                          'origin': 'ai', 'content': content(cached) if valid else None})
        return {'items': items}


def claim():
    """Commit a one-way dispatch fence and quota before any external I/O.

    A dispatched job is NEVER reclaimed/retried. A crash at the fence can lose
    work; this is necessary without a provider idempotency guarantee.
    """
    configured = policy()
    if not generation_enabled(configured):
        return None
    with get_connection() as conn:
        begin_write(conn)
        stamp = now()
        conn.execute("UPDATE education_translation_job SET state='unknown',failure_code='dispatch_outcome_unknown' WHERE state='dispatched' AND deadline_at<?", (stamp,))
        conn.execute("UPDATE education_translation_job SET state='failed',failure_code='worker_unavailable' WHERE state='queued' AND created_at<?",
                     ((datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat(timespec='seconds'),))
        queued = conn.execute("SELECT * FROM education_translation_job WHERE state='queued' ORDER BY created_at,job_id LIMIT 1").fetchone()
        if queued is None:
            return None
        row = source_row(conn, queued['source_kind'], queued['source_id'])
        if (row is None or row['status'] != 'published' or row['version'] != queued['source_version']
                or digest(row) != queued['content_digest'] or not eligible(row, queued['language'], configured)):
            conn.execute("UPDATE education_translation_job SET state='stale',failure_code='source_or_policy_changed' WHERE job_id=?", (queued['job_id'],))
            return None
        if translations.published_content(conn, queued['source_id'], queued['source_version'], queued['language'], kind=queued['source_kind']) is not None:
            conn.execute("UPDATE education_translation_job SET state='stale',failure_code='reviewed_translation_available' WHERE job_id=?", (queued['job_id'],))
            return None
        length = sum(len(s) for s in [row['title'], row['body'], *json.loads(row['checklist_json'])])
        for bucket, maximum in [('total', configured['total_requests']), (stamp[:10], configured['daily_requests'])]:
            conn.execute('INSERT INTO education_translation_budget(bucket) VALUES(?) ON CONFLICT DO NOTHING', (bucket,))
            budget = conn.execute('SELECT * FROM education_translation_budget WHERE bucket=?', (bucket,)).fetchone()
            if budget['dispatched_count'] >= maximum or (bucket == 'total' and budget['source_chars'] + length > configured['total_source_chars']):
                conn.execute("UPDATE education_translation_job SET state='failed',failure_code='budget_exhausted' WHERE job_id=?", (queued['job_id'],))
                return None
        token = str(uuid4())
        changed = conn.execute("""UPDATE education_translation_job SET state='dispatched',claim_token=?,dispatched_at=?,deadline_at=?
            WHERE job_id=? AND state='queued'""", (token, stamp, (datetime.now(timezone.utc)+timedelta(seconds=120)).isoformat(timespec='seconds'), queued['job_id']))
        if changed.rowcount != 1:
            return None
        for bucket in ('total', stamp[:10]):
            conn.execute('UPDATE education_translation_budget SET dispatched_count=dispatched_count+1,source_chars=source_chars+? WHERE bucket=?', (length, bucket))
        return dict(queued), token, MaterialContent(**content(row))


def complete(queued, token, result=None, model=None, *, failure=None):
    with get_connection() as conn:
        begin_write(conn)
        current = conn.execute('SELECT * FROM education_translation_job WHERE job_id=?', (queued['job_id'],)).fetchone()
        if current['state'] != 'dispatched' or current['claim_token'] != token:
            return
        row = source_row(conn, queued['source_kind'], queued['source_id'])
        state = 'ready'
        if current['deadline_at'] < now():
            state, failure = 'unknown', 'dispatch_outcome_unknown'
        elif failure:
            state = 'failed'
        elif (row is None or row['status'] != 'published' or row['version'] != queued['source_version']
              or digest(row) != queued['content_digest'] or not eligible(row, queued['language'], policy())):
            state, failure = 'stale', 'source_or_policy_changed'
        values = result.model_dump() if state == 'ready' else {'title': None, 'body': None, 'checklist': None}
        conn.execute('''UPDATE education_translation_job SET state=?,failure_code=?,completed_at=?,model=?,title=?,body=?,checklist_json=?
            WHERE job_id=? AND claim_token=? AND state='dispatched' ''',
            (state, failure, now(), model, values['title'], values['body'], json.dumps(values['checklist']) if state == 'ready' else None, queued['job_id'], token))


def run_once():
    from backend.education import translation_provider
    claimed = claim()
    if claimed is None:
        return False
    queued, token, original = claimed
    # No HTTP call inside a transaction or retry helper. Once the fence committed,
    # even configuration errors and transport ambiguity consume this attempt.
    try:
        result, model = translation_provider.generate(original, queued['language'])
        result = MaterialContent.model_validate(result.model_dump())
    except Exception:
        complete(queued, token, failure='provider_failed_or_unknown')
    else:
        complete(queued, token, result, model)
    return True


if __name__ == '__main__':
    import argparse
    import time
    parser = argparse.ArgumentParser(description='Run opt-in education translation jobs; no automatic retries after dispatch.')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    while True:
        run_once()
        if args.once:
            break
        time.sleep(3)
