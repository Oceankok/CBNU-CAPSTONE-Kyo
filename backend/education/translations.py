"""Version-bound translations. Source content is never rewritten by this module."""
import json
from uuid import uuid4
from threading import BoundedSemaphore
from fastapi import HTTPException
from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.education import service
from backend.education.models import MaterialContent

slot = BoundedSemaphore(1)


def source(conn, material_id, expected_version=None, *, kind='material'):
    if kind == 'bundle':
        from backend.education_bundles.service import find
        row = find(conn, material_id)
    elif kind == 'material':
        row = service.find(conn, material_id)
    else:
        raise ValueError('Unknown translation source kind')
    if row['status'] != 'published':
        raise HTTPException(409, 'education_translation_source_not_published')
    if expected_version is not None and row['version'] != expected_version:
        raise HTTPException(409, 'education_translation_source_changed')
    return row


def find(conn, translation_id):
    row = conn.execute('SELECT * FROM education_translation WHERE translation_id=?', (translation_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'education_translation_not_found')
    return row


def serialize(conn, row):
    result = service.serialize(row)
    if row['bundle_id'] is not None:
        from backend.education_bundles.service import find as find_bundle
        original = find_bundle(conn, row['bundle_id'])
    else:
        original = service.find(conn, row['material_id'])
    result['source_kind'] = 'bundle' if row['bundle_id'] is not None else 'material'
    result['stale'] = original['status'] != 'published' or original['version'] != row['source_version']
    return result


def get(translation_id):
    with get_connection() as conn:
        return serialize(conn, find(conn, translation_id))


def list_for(material_id, limit=50, offset=0, *, kind='material'):
    with get_connection() as conn:
        if kind == 'bundle':
            from backend.education_bundles.service import find as find_bundle
            find_bundle(conn, material_id)
        else:
            service.find(conn, material_id)
        column = 'bundle_id' if kind == 'bundle' else 'material_id'
        return [serialize(conn, row) for row in conn.execute(f'''SELECT * FROM education_translation
            WHERE {column}=? ORDER BY created_at DESC,translation_id LIMIT ? OFFSET ?''', (material_id,limit,offset)).fetchall()]


def create(material_id, content, language, expected_source_version, actor, *, provider='manual', model=None, kind='material'):
    if language.split('-')[0] == 'ko':
        raise HTTPException(422, 'education_translation_original_language')
    # Validate provider-returned content independently, including length limits.
    content = MaterialContent.model_validate(content.model_dump())
    ident, stamp = str(uuid4()), service.now()
    with get_connection() as conn:
        begin_write(conn)
        source(conn, material_id, expected_source_version, kind=kind)
        conn.execute('''INSERT INTO education_translation(translation_id,material_id,bundle_id,language,source_version,
            title,body,checklist_json,provider,model,created_by,created_at,updated_by,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (ident,material_id if kind == 'material' else None,material_id if kind == 'bundle' else None,language,expected_source_version,
            content.title,content.body,json.dumps(content.checklist,ensure_ascii=False),provider,model,actor,stamp,actor,stamp))
    return get(ident)


def edit(translation_id, body, actor):
    with get_connection() as conn:
        begin_write(conn)
        row = find(conn,translation_id)
        if row['status'] != 'draft' or row['version'] != body.expected_version:
            raise HTTPException(409, 'education_translation_conflict')
        source(conn,row['material_id'] or row['bundle_id'],row['source_version'],kind='bundle' if row['bundle_id'] else 'material')
        conn.execute('''UPDATE education_translation SET title=?,body=?,checklist_json=?,updated_by=?,updated_at=?,version=version+1
            WHERE translation_id=?''', (body.title,body.body,json.dumps(body.checklist,ensure_ascii=False),actor,service.now(),translation_id))
    return get(translation_id)


def publish(translation_id, expected_version, actor):
    with get_connection() as conn:
        begin_write(conn)
        row = find(conn,translation_id)
        if row['status'] != 'draft' or row['version'] != expected_version:
            raise HTTPException(409,'education_translation_conflict')
        source(conn,row['material_id'] or row['bundle_id'],row['source_version'],kind='bundle' if row['bundle_id'] else 'material')
        stamp = service.now()
        conn.execute('''UPDATE education_translation SET status='superseded',version=version+1,updated_by=?,updated_at=?
            WHERE (material_id=? OR bundle_id=?) AND language=? AND status='published' ''', (actor,stamp,row['material_id'],row['bundle_id'],row['language']))
        conn.execute('''UPDATE education_translation SET status='published',version=version+1,updated_by=?,updated_at=?,published_by=?,published_at=?
            WHERE translation_id=?''', (actor,stamp,actor,stamp,translation_id))
    return get(translation_id)


def unpublish(translation_id, expected_version, actor):
    with get_connection() as conn:
        begin_write(conn)
        row=find(conn,translation_id)
        if row['status'] != 'published' or row['version'] != expected_version:
            raise HTTPException(409,'education_translation_conflict')
        conn.execute('''UPDATE education_translation SET status='draft',version=version+1,updated_by=?,updated_at=?,published_by=NULL,published_at=NULL
            WHERE translation_id=?''', (actor,service.now(),translation_id))
    return get(translation_id)


def generate(material_id, request, actor, *, kind='material'):
    from backend.education import translation_provider as provider
    from backend.education.provider import GenerationError
    if request.language.split('-')[0]=='ko':
        raise HTTPException(422,'education_translation_original_language')
    if not request.allow_external_processing:
        raise HTTPException(400,'education_translation_external_processing_required')
    if not slot.acquire(blocking=False):
        raise HTTPException(429,'education_translation_busy')
    try:
        with get_connection() as conn:
            row=source(conn,material_id,request.expected_source_version,kind=kind)
            original=MaterialContent(title=row['title'],body=row['body'],checklist=json.loads(row['checklist_json']))
        try:
            content, model = provider.generate(original,request.language)
        except GenerationError as exc:
            raise HTTPException(exc.status_code,exc.code) from None
        # Recheck after slow external call: stale generated results must not be saved.
        return create(material_id,content,request.language,request.expected_source_version,actor,provider='openai',model=model,kind=kind)
    finally:
        slot.release()


def published_content(conn, material_id, source_version, language, *, kind='material'):
    column = 'bundle_id' if kind == 'bundle' else 'material_id'
    from backend.education.languages import candidates
    for candidate in candidates(language):
        # Korean in the preference list chooses the original over lower priorities.
        if candidate == 'ko':
            return None
        row = conn.execute(f'''SELECT title,body,checklist_json,language FROM education_translation WHERE {column}=?
            AND source_version=? AND language=? AND status='published' ''',
            (material_id, source_version, candidate)).fetchone()
        if row is not None:
            return row
    return None
