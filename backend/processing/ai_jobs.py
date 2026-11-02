"""Short-lived host AI requests, independent of event media and paid fallback."""
import base64
import hashlib
import json
import secrets
import time
from uuid import UUID, uuid4

from fastapi import HTTPException

from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.media.paths import WORK_ROOT

ROOT = WORK_ROOT / 'host_ai'
DEADLINE_SECONDS = 90
CAPABILITY_SECONDS = 30
PROVIDERS = {'equipment': {'codex_chatgpt', 'chatgpt_oauth', 'openai'},
             'education': {'codex_chatgpt', 'openai'}}
ERRORS = {'provider_timeout', 'provider_unavailable', 'provider_auth_required', 'provider_usage_limit',
          'provider_rate_limited', 'provider_response_invalid', 'redaction_failed',
          'education_provider_timeout', 'education_provider_unavailable', 'education_provider_auth_required',
          'education_provider_usage_limit', 'education_provider_rate_limited', 'education_provider_response_invalid',
          'education_provider_auth_failed', 'education_generation_refused', 'education_generation_not_configured',
          'education_topic_invalid'}


def source_path(job_id):
    if str(UUID(job_id)) != job_id:
        raise ValueError('Invalid job ID')
    path = ROOT / (job_id + '.image')
    if path.is_symlink() or path.resolve().parent != ROOT.resolve():
        raise ValueError('Unsafe source path')
    return path


def job(conn, ident):
    row = conn.execute('SELECT * FROM host_ai_job WHERE job_id=?', (ident,)).fetchone()
    if not row:
        raise HTTPException(404, 'host_ai_job_not_found')
    return dict(row)


def advertise(node_id, capabilities):
    with get_connection() as conn:
        begin_write(conn)
        conn.execute('DELETE FROM host_ai_worker WHERE node_id=?', (node_id,))
        for kind, provider in capabilities.items():
            if kind not in PROVIDERS or provider not in PROVIDERS[kind]:
                raise HTTPException(422, 'host_ai_capability_invalid')
            conn.execute('INSERT INTO host_ai_worker(node_id,kind,provider,expires_at) VALUES(?,?,?,?)',
                         (node_id, kind, provider, time.time() + CAPABILITY_SECONDS))


def available(conn, kind, provider):
    return conn.execute('''SELECT 1 FROM host_ai_worker a JOIN processing_worker p ON p.node_id=a.node_id
        JOIN field_node n ON n.node_id=a.node_id WHERE a.kind=? AND a.provider=? AND a.expires_at>?
        AND p.enabled=1 AND n.is_active=1''', (kind, provider, time.time())).fetchone() is not None


def enqueue(kind, provider, payload, image=None):
    ident = str(uuid4())
    with get_connection() as conn:
        begin_write(conn)
        if kind not in PROVIDERS or provider not in PROVIDERS[kind] or not available(conn, kind, provider):
            raise HTTPException(503, 'processing_worker_not_connected')
        if conn.execute("SELECT COUNT(*) FROM host_ai_job WHERE status IN ('queued','running')").fetchone()[0] >= 4:
            raise HTTPException(429, 'host_ai_busy')
        try:
            if image is not None:
                ROOT.mkdir(parents=True, exist_ok=True)
                with source_path(ident).open('xb') as file:
                    file.write(image)
            conn.execute('''INSERT INTO host_ai_job(job_id,kind,provider,input_json,status,deadline,created_at)
                VALUES(?,?,?,?,'queued',?,?)''',
                         (ident, kind, provider, json.dumps(payload, ensure_ascii=False),
                          time.time() + DEADLINE_SECONDS, time.time()))
        except Exception:
            source_path(ident).unlink(missing_ok=True)
            raise
    return ident


def claim(node_id):
    with get_connection() as conn:
        begin_write(conn)
        from backend.processing.priority import busy_until
        if busy_until(conn) > time.time():
            return {'job': None}
        if conn.execute("SELECT 1 FROM processing_job WHERE worker_node_id=? AND local_cleanup_status='pending'", (node_id,)).fetchone():
            return {'job': None}
        if conn.execute('SELECT 1 FROM host_ai_job WHERE worker_node_id=? AND cleanup_pending=1', (node_id,)).fetchone():
            return {'job': None}
        row = conn.execute('''SELECT j.* FROM host_ai_job j JOIN host_ai_worker w
            ON w.kind=j.kind AND w.provider=j.provider WHERE w.node_id=? AND w.expires_at>?
            AND j.status='queued' AND j.deadline>? ORDER BY j.created_at,j.job_id LIMIT 1''',
                           (node_id, time.time(), time.time())).fetchone()
        if not row:
            return {'job': None}
        token = secrets.token_urlsafe(32)
        conn.execute("UPDATE host_ai_job SET status='running',worker_node_id=?,lease_hash=?,cleanup_pending=1 WHERE job_id=?",
                     (node_id, hashlib.sha256(token.encode()).hexdigest(), row['job_id']))
        return {'job': {'job_id': row['job_id'], 'kind': row['kind'], 'provider': row['provider'],
                        'input': json.loads(row['input_json']), 'deadline': row['deadline'], 'lease_token': token}}


def authorized(conn, ident, node_id, lease):
    row = job(conn, ident)
    if row['worker_node_id'] != node_id or not lease or not secrets.compare_digest(
            row['lease_hash'] or '', hashlib.sha256(lease.encode()).hexdigest()):
        raise HTTPException(403, 'host_ai_lease_invalid')
    if row['status'] != 'running' or row['deadline'] <= time.time():
        raise HTTPException(409, 'host_ai_job_inactive')
    return row


def complete(ident, node_id, lease, result=None, error=None):
    with get_connection() as conn:
        begin_write(conn)
        row = authorized(conn, ident, node_id, lease)
        if error is not None:
            if error not in ERRORS:
                raise HTTPException(422, 'host_ai_error_invalid')
        else:
            try:
                if row['kind'] == 'equipment':
                    from backend.equipment.provider import ProviderResult
                    analysis = ProviderResult.model_validate(result['analysis'])
                    image = base64.b64decode(result['redacted_jpeg'], validate=True)
                    if not image or len(image) > 10 * 1024 * 1024 or not image.startswith(b'\xff\xd8\xff'):
                        raise ValueError()
                    result = {'analysis': analysis.model_dump(), 'redacted_jpeg': result['redacted_jpeg']}
                else:
                    from backend.education.models import MaterialContent
                    import re
                    content = MaterialContent.model_validate(result['content'])
                    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}', result['model']):
                        raise ValueError()
                    result = {'content': content.model_dump(), 'model': result['model']}
            except (ValueError, KeyError, TypeError):
                raise HTTPException(422, 'host_ai_result_invalid') from None
        conn.execute('UPDATE host_ai_job SET status=?,result_json=?,error_code=?,input_json=? WHERE job_id=?',
                     ('failed' if error else 'succeeded', json.dumps(result) if not error else None, error, '{}', ident))
        try:
            source_path(ident).unlink(missing_ok=True)
        except OSError:
            pass  # Terminal source remains private; maintenance retries its deletion.
    return {'status': 'failed' if error else 'succeeded'}


def cleanup_local(ident, node_id):
    with get_connection() as conn:
        begin_write(conn)
        row = job(conn, ident)
        if row['worker_node_id'] != node_id:
            raise HTTPException(403, 'host_ai_worker_mismatch')
        if row['status'] in {'queued', 'running'}:
            if row['deadline'] > time.time():
                raise HTTPException(409, 'host_ai_job_active')
            conn.execute("UPDATE host_ai_job SET status='cancelled',input_json='{}' WHERE job_id=?", (ident,))
        conn.execute('UPDATE host_ai_job SET cleanup_pending=0 WHERE job_id=?', (ident,))
    return {'cleaned': True}


def request_job(kind, provider, payload, image=None):
    ident = enqueue(kind, provider, payload, image)
    try:
        while True:
            with get_connection() as conn:
                row = job(conn, ident)
            if row['deadline'] <= time.time() or row['status'] == 'cancelled':
                raise HTTPException(504, 'host_ai_timeout')
            if row['status'] == 'succeeded':
                return json.loads(row['result_json'])
            if row['status'] == 'failed':
                if kind == 'education':
                    from backend.education.provider import GenerationError
                    code = row['error_code']
                    status = 504 if code.endswith('timeout') else 429 if code.endswith('rate_limited') else \
                        503 if code.endswith(('auth_required', 'auth_failed', 'usage_limit', 'not_configured')) else \
                        422 if code.endswith('refused') else 409 if code == 'education_topic_invalid' else 502
                    raise GenerationError(code, status)
                from backend.equipment.provider import ProviderError
                raise ProviderError(row['error_code'])
            time.sleep(0.2)
    finally:
        with get_connection() as conn:
            begin_write(conn)
            conn.execute("""UPDATE host_ai_job SET status=CASE WHEN status IN ('queued','running') THEN 'cancelled' ELSE status END,
                         input_json='{}',result_json=NULL WHERE job_id=?""", (ident,))
            try:
                source_path(ident).unlink(missing_ok=True)
            except OSError:
                pass  # Deadline maintenance retries private source deletion.


def maintain():
    with get_connection() as conn:
        begin_write(conn)
        rows = conn.execute('SELECT job_id FROM host_ai_job WHERE deadline<=?', (time.time(),)).fetchall()
        for row in rows:
            try:
                source_path(row[0]).unlink(missing_ok=True)
            except OSError:
                continue
        conn.execute("""UPDATE host_ai_job SET status=CASE WHEN status IN ('queued','running') THEN 'cancelled' ELSE status END,
                     input_json='{}',result_json=NULL WHERE deadline<=?""", (time.time(),))
        conn.execute('DELETE FROM host_ai_worker WHERE expires_at<=?', (time.time(),))
        conn.execute('DELETE FROM host_ai_job WHERE deadline<? AND cleanup_pending=0', (time.time()-86400,))
    if ROOT.exists():
        for file in ROOT.glob('*.image'):
            try:
                if source_path(file.stem) == file and file.stat().st_mtime < time.time()-DEADLINE_SECONDS-60:
                    file.unlink()
            except (ValueError, OSError):
                continue
