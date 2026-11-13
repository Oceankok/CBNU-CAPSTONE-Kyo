"""Explicit multi-zone snapshots. Never mutate the single-zone material lifecycle."""
import hashlib
import json
from uuid import uuid4
from fastapi import HTTPException
from backend.db.connection import run_stats_transaction
from backend.education.service import now, generation_slot
from backend.education.models import MaterialContent
from backend.zones.membership import zones_for


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


def selection(conn, ids):
    if len(ids) < 2 or len(ids) > 20 or len(set(ids)) != len(ids):
        raise HTTPException(422, 'education_bundle_invalid_selection')
    sources = []
    for identifier in ids:
        row = conn.execute('''SELECT recommendation_id,quarter,zone_name,ppe_type,
            education_topic,confirmed_count,priority_score,score_version
            FROM education_recommendation WHERE recommendation_id=?''', (identifier,)).fetchone()
        if row is None:
            raise HTTPException(409, 'education_bundle_selection_changed')
        sources.append(dict(row))
    sources.sort(key=lambda row: row['zone_name'])
    if (len({s['quarter'] for s in sources}) != 1 or
            len({s['ppe_type'] for s in sources}) != 1 or
            len({s['zone_name'] for s in sources}) != len(sources) or
            any(not s['zone_name'] for s in sources)):
        raise HTTPException(422, 'education_bundle_same_quarter_ppe_distinct_zones_required')
    return {'quarter': sources[0]['quarter'], 'ppe_type': sources[0]['ppe_type'],
            'sources': sources, 'snapshot': digest(sources),
            'confirmed_count_sum': sum(s['confirmed_count'] for s in sources)}


def preview(ids):
    return run_stats_transaction(lambda conn: selection(conn, ids))


def checked(conn, ids, expected):
    result = selection(conn, ids)
    if result['snapshot'] != expected:
        raise HTTPException(409, 'education_bundle_selection_changed')
    return result


def write_checked(conn, ids, expected):
    # RR snapshots may predate the advisory lock holder's committed refresh.
    # Touch source rows before trusting them so PostgreSQL raises 40001 and
    # run_stats_transaction retries with a fresh snapshot instead of approving
    # old source content. The stable quarter parent also gates cache rebuilds.
    quarters = set()
    for identifier in sorted(ids):
        row = conn.execute('SELECT quarter FROM education_recommendation WHERE recommendation_id=?',
                           (identifier,)).fetchone()
        if row:
            quarters.add(row['quarter'])
    for quarter in sorted(quarters):
        conn.execute('UPDATE quarterly_summary SET created_at=created_at WHERE quarter=?', (quarter,))
    for identifier in sorted(ids):
        conn.execute('UPDATE education_recommendation SET education_topic=education_topic WHERE recommendation_id=?',
                     (identifier,))
    return checked(conn, ids, expected)


def serialize(row):
    result = dict(row)
    result['sources'] = json.loads(result.pop('sources_json'))
    result['checklist'] = json.loads(result.pop('checklist_json'))
    result['snapshot'] = digest(result['sources'])
    return result


def find(conn, identifier):
    row = conn.execute('SELECT * FROM education_bundle WHERE bundle_id=?', (identifier,)).fetchone()
    if row is None:
        raise HTTPException(404, 'education_bundle_not_found')
    return row


def get(identifier):
    return run_stats_transaction(lambda conn: serialize(find(conn, identifier)))


def list_bundles(quarter, limit, offset):
    return run_stats_transaction(lambda conn: [serialize(row) for row in conn.execute(
        '''SELECT * FROM education_bundle WHERE quarter=?
        ORDER BY created_at DESC,bundle_id LIMIT ? OFFSET ?''', (quarter, limit, offset)).fetchall()])


def create(ids, expected, content, actor, *, provider='manual', model=None):
    def operation(conn):
        selected = write_checked(conn, ids, expected)
        key = digest([selected['quarter'], selected['ppe_type'], [s['zone_name'] for s in selected['sources']]])
        identifier, stamp = 'BUNDLE_' + uuid4().hex, now()
        conn.execute('''INSERT INTO education_bundle(bundle_id,quarter,ppe_type,scope_key,
            sources_json,title,body,checklist_json,provider,model,created_by,created_at,updated_by,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (identifier, selected['quarter'],selected['ppe_type'],key,
            packed(selected['sources']),content.title,content.body,packed(content.checklist),provider,model,
            actor,stamp,actor,stamp))
        return serialize(find(conn, identifier))
    return run_stats_transaction(operation, write=True)


def generate(ids, expected, actor):
    from backend.education import provider
    from backend.runtime import processing_mode
    if not generation_slot.acquire(blocking=False):
        raise HTTPException(429, 'education_generation_busy')
    try:
        selected = run_stats_transaction(lambda conn: checked(conn, ids, expected))
        # Stored recommendation topics include zone names and may contain arbitrary
        # administrator content. Never forward them or try to redact free text.
        # Only an allowlisted PPE code and application-owned generic topic leave.
        labels = {'helmet': '안전모', 'vest': '안전조끼', 'goggles': '보안경',
                  'gloves': '안전장갑', 'safety_shoes': '안전화',
                  'hearing_protection': '청력보호구', 'mask': '마스크', 'harness': '안전대'}
        ppe = selected['ppe_type']
        if ppe not in labels:
            raise HTTPException(422, 'education_bundle_unsupported_generation_ppe')
        scope = {'ppe_type': ppe,
                 'education_topic': labels[ppe] + ' 착용 기준 및 착용 전 점검 절차 교육'}
        try:
            configured = provider.settings()
            if configured.provider == 'codex_chatgpt' and processing_mode() == 'remote':
                from backend.processing.ai_jobs import request_job
                result = request_job('education', configured.provider, scope)
                content, model = MaterialContent.model_validate(result['content']), result['model']
            else:
                if configured.provider == 'codex_chatgpt' and processing_mode() == 'disabled':
                    raise provider.GenerationError('processing_worker_not_connected', 503)
                content, model = provider.generate(scope)
        except provider.GenerationError as exc:
            raise HTTPException(exc.status_code, exc.code) from None
        return create(ids, expected, content, actor, provider=configured.provider, model=model)
    finally:
        generation_slot.release()


def change(identifier, version, actor, *, action, content=None):
    def operation(conn):
        row = find(conn, identifier)
        if row['version'] != version:
            raise HTTPException(409, 'education_bundle_version_conflict')
        if row['status'] != ('published' if action == 'unpublish' else 'draft'):
            raise HTTPException(409, 'education_bundle_invalid_status')
        stamp = now()
        if action == 'edit':
            conn.execute('''UPDATE education_bundle SET title=?,body=?,checklist_json=?,
                updated_by=?,updated_at=?,version=version+1 WHERE bundle_id=?''',
                (content.title,content.body,packed(content.checklist),actor,stamp,identifier))
        elif action == 'publish':
            sources = json.loads(row['sources_json'])
            write_checked(conn, [s['recommendation_id'] for s in sources], digest(sources))
            conn.execute('''UPDATE education_bundle SET status='superseded',version=version+1,
                updated_by=?,updated_at=? WHERE scope_key=? AND status='published' ''', (actor,stamp,row['scope_key']))
            conn.execute('''UPDATE education_bundle SET status='published',published_by=?,published_at=?,
                updated_by=?,updated_at=?,version=version+1 WHERE bundle_id=?''', (actor,stamp,actor,stamp,identifier))
        else:
            conn.execute('''UPDATE education_bundle SET status='draft',published_by=NULL,published_at=NULL,
                updated_by=?,updated_at=?,version=version+1 WHERE bundle_id=?''', (actor,stamp,identifier))
        return serialize(find(conn, identifier))
    try:
        return run_stats_transaction(operation, write=True)
    except Exception as exc:
        # A concurrent first publication can win after PostgreSQL's repeatable
        # read snapshot was acquired while waiting for the advisory lock.
        # Keep the unique invariant and request a fresh explicit user action.
        if action == 'publish' and getattr(exc, 'sqlstate', None) == '23505':
            raise HTTPException(409, 'education_bundle_publication_conflict') from None
        raise


def for_worker(user_id, language="ko"):
    def operation(conn):
        assigned = set(zones_for(conn, user_id))
        latest = {zone: conn.execute('SELECT MAX(quarter) FROM education_recommendation WHERE zone_name=?',
                                    (zone,)).fetchone()[0] for zone in assigned}
        result = []
        for row in conn.execute("SELECT * FROM education_bundle WHERE status='published' ORDER BY published_at DESC,bundle_id").fetchall():
            sources = json.loads(row['sources_json'])
            matching = [s for s in sources if s['zone_name'] in assigned and latest[s['zone_name']] == row['quarter']]
            # An assigned zone's current topic must still match the approved snapshot.
            matching = [s for s in matching if conn.execute('''SELECT 1 FROM education_recommendation
                WHERE quarter=? AND zone_name=? AND ppe_type=? AND education_topic=?''',
                (s['quarter'],s['zone_name'],s['ppe_type'],s['education_topic'])).fetchone()]
            if not matching:
                continue
            from backend.education.translations import published_content
            translated = published_content(conn, row['bundle_id'], row['version'], language, kind='bundle') if language != 'ko' else None
            content = translated if translated is not None else row
            result.append({'bundle_id': row['bundle_id'], 'quarter': row['quarter'], 'ppe_type': row['ppe_type'],
                           'assigned_zones': [s['zone_name'] for s in matching], 'title': content['title'],
                           'body': content['body'], 'checklist': json.loads(content['checklist_json']),
                           'language': language if translated is not None else 'ko',
                           'requested_language': language, 'translation_fallback': language != 'ko' and translated is None})
        return result
    return run_stats_transaction(operation)
