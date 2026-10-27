"""Reviewed education content, scoped independently of mutable recommendation ranks."""
from datetime import datetime, timezone
import json
from threading import BoundedSemaphore
from uuid import uuid4

from fastapi import HTTPException

from backend.db.connection import begin_write
from backend.db.event_repository import get_connection


generation_slot = BoundedSemaphore(1)


def generate_draft(recommendation_id, actor):
    from backend.education import provider
    # A small per-process limit; this is not a distributed job or retry system.
    if not generation_slot.acquire(blocking=False):
        raise HTTPException(429, 'education_generation_busy')
    try:
        with get_connection() as conn:
            scope = recommendation(conn, recommendation_id)
        try:
            configured = provider.settings()
            from backend.runtime import processing_mode
            if configured.provider == 'codex_chatgpt' and processing_mode() == 'remote':
                from backend.processing.ai_jobs import request_job
                result = request_job('education', configured.provider, {'ppe_type': scope['ppe_type'],
                                     'education_topic': scope['education_topic']})
                from backend.education.models import MaterialContent
                content, model = MaterialContent.model_validate(result['content']), result['model']
            else:
                if configured.provider == 'codex_chatgpt' and processing_mode() == 'disabled':
                    raise provider.GenerationError('processing_worker_not_connected', 503)
                content, model = provider.generate(scope)
        except provider.GenerationError as exc:
            raise HTTPException(exc.status_code, exc.code) from None
        return create_draft(recommendation_id, content, actor, expected_scope=scope, provider=configured.provider, model=model)
    finally:
        generation_slot.release()


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def recommendation(conn, recommendation_id):
    row = conn.execute('''SELECT recommendation_id,quarter,zone_name,ppe_type,education_topic
        FROM education_recommendation WHERE recommendation_id=?''', (recommendation_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'recommendation_not_found')
    return dict(row)


def find(conn, material_id):
    row = conn.execute('SELECT * FROM education_material WHERE material_id=?', (material_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'education_material_not_found')
    return row


def serialize(row):
    result = dict(row)
    result['checklist'] = json.loads(result.pop('checklist_json'))
    return result


def get_material(material_id):
    with get_connection() as conn:
        return serialize(find(conn, material_id))


def list_materials(recommendation_id, limit=50, offset=0):
    with get_connection() as conn:
        scope = recommendation(conn, recommendation_id)
        rows = conn.execute('''SELECT * FROM education_material WHERE quarter=? AND zone_name=? AND ppe_type=?
            ORDER BY created_at DESC,material_id LIMIT ? OFFSET ?''',
            (scope['quarter'], scope['zone_name'], scope['ppe_type'], limit, offset)).fetchall()
    return [serialize(row) for row in rows]


def create_draft(recommendation_id, content, actor, *, expected_scope=None, provider='manual', model=None):
    material_id = 'MAT_' + uuid4().hex
    with get_connection() as conn:
        begin_write(conn)
        try:
            scope = recommendation(conn, recommendation_id)
        except HTTPException:
            if expected_scope is not None:
                raise HTTPException(409, 'education_recommendation_changed') from None
            raise
        if expected_scope is not None and scope != expected_scope:
            raise HTTPException(409, 'education_recommendation_changed')
        stamp = now()
        conn.execute('''INSERT INTO education_material(material_id,recommendation_id,quarter,zone_name,ppe_type,
            education_topic,title,body,checklist_json,created_by,created_at,updated_by,updated_at,provider,model)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (material_id, recommendation_id, scope['quarter'], scope['zone_name'], scope['ppe_type'], scope['education_topic'],
             content.title, content.body, json.dumps(content.checklist, ensure_ascii=False), actor, stamp, actor, stamp, provider, model))
    return get_material(material_id)


def edit_draft(material_id, content, actor, expected_version):
    with get_connection() as conn:
        begin_write(conn)
        row = find(conn, material_id)
        if row['status'] != 'draft':
            raise HTTPException(409, 'published_material_read_only')
        if row['version'] != expected_version:
            raise HTTPException(409, 'education_material_version_conflict')
        conn.execute('''UPDATE education_material SET title=?,body=?,checklist_json=?,updated_by=?,updated_at=?,
            version=version+1 WHERE material_id=?''',
            (content.title, content.body, json.dumps(content.checklist, ensure_ascii=False), actor, now(), material_id))
    return get_material(material_id)


def publish(material_id, actor, expected_version):
    with get_connection() as conn:
        begin_write(conn)
        row = find(conn, material_id)
        if row['version'] != expected_version:
            raise HTTPException(409, 'education_material_version_conflict')
        if row['status'] != 'draft':
            raise HTTPException(409, 'education_material_not_draft')
        if not conn.execute('''SELECT 1 FROM education_recommendation WHERE quarter=? AND zone_name=?
            AND ppe_type=? AND education_topic=?''', (row['quarter'], row['zone_name'], row['ppe_type'], row['education_topic'])).fetchone():
            raise HTTPException(409, 'education_recommendation_changed')
        conn.execute('''UPDATE education_material SET status='superseded',updated_by=?,updated_at=?,version=version+1
            WHERE quarter=? AND zone_name=? AND ppe_type=? AND status='published' ''',
            (actor, now(), row['quarter'], row['zone_name'], row['ppe_type']))
        stamp = now()
        conn.execute("UPDATE education_material SET status='published',published_by=?,published_at=?,updated_by=?,updated_at=?,version=version+1 WHERE material_id=?",
                     (actor, stamp, actor, stamp, material_id))
    return get_material(material_id)


def unpublish(material_id, actor, expected_version):
    with get_connection() as conn:
        begin_write(conn)
        row = find(conn, material_id)
        if row['version'] != expected_version:
            raise HTTPException(409, 'education_material_version_conflict')
        if row['status'] != 'published':
            raise HTTPException(409, 'education_material_not_published')
        changed = conn.execute('''UPDATE education_material SET status='draft',updated_by=?,updated_at=?,
            version=version+1 WHERE material_id=? AND version=? AND status='published' ''',
            (actor, now(), material_id, expected_version))
        if changed.rowcount != 1:
            raise HTTPException(409, 'education_material_version_conflict')
        result = serialize(find(conn, material_id))
    return result


def published_for(conn, quarter, zone_name, ppe_type, topic):
    row = conn.execute('''SELECT title,body,checklist_json FROM education_material
        WHERE quarter=? AND zone_name=? AND ppe_type=? AND education_topic=? AND status='published' ''',
        (quarter, zone_name, ppe_type, topic)).fetchone()
    if not row:
        return None
    checklist = '\n'.join('- ' + item for item in json.loads(row['checklist_json']))
    return row['title'] + '\n\n' + row['body'] + '\n\n확인 목록\n' + checklist
