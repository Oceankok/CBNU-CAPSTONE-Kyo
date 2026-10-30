import hashlib
import secrets
import time
from uuid import uuid4

from fastapi import HTTPException

from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.media.paths import private_source_path

LEASE_SECONDS = 90
MAX_SECONDS = 1800


def job(conn, job_id):
    row = conn.execute('SELECT * FROM processing_job WHERE job_id=?', (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'processing_job_not_found')
    return dict(row)


def public(row):
    return {key: row[key] for key in ('job_id', 'media_id', 'generation', 'status', 'redaction_mode', 'kind',
                                    'worker_node_id', 'lease_until', 'error_code', 'local_cleanup_status')}


def authorized(conn, job_id, node_id, token, *, terminal=False):
    row = job(conn, job_id)
    if row['worker_node_id'] != node_id or not token or not secrets.compare_digest(
            hashlib.sha256(token.encode()).hexdigest(), row['lease_token_hash'] or ''):
        raise HTTPException(403, 'processing_lease_invalid')
    media = conn.execute('SELECT * FROM event_media WHERE media_id=?', (row['media_id'],)).fetchone()
    retention = conn.execute('SELECT decision FROM event_retention WHERE event_id=?', (media['event_id'],)).fetchone()
    if terminal and row['status'] == 'succeeded' and retention['decision'] != 'delete' and media['processing_generation'] == row['generation']:
        return row
    if row['status'] != 'running' or row['lease_until'] <= time.time() or media['status'] != 'processing' or \
            media['processing_generation'] != row['generation'] or retention['decision'] == 'delete':
        raise HTTPException(409, 'processing_job_inactive')
    return row


def enqueue(media_id):
    with get_connection() as conn:
        begin_write(conn)
        media = conn.execute('SELECT * FROM event_media WHERE media_id=?', (media_id,)).fetchone()
        if not media or media['status'] != 'processing':
            raise ValueError('media_not_processing')
        retention = conn.execute('SELECT decision FROM event_retention WHERE event_id=?', (media['event_id'],)).fetchone()
        if retention['decision'] == 'delete':
            raise ValueError('event_media_deleted')
        if conn.execute("SELECT 1 FROM processing_job WHERE media_id=? AND status IN ('queued','running')", (media_id,)).fetchone():
            raise ValueError('processing_in_progress')
        generation = media['processing_generation'] + 1
        ident = str(uuid4())
        parent = conn.execute('SELECT job_id FROM processing_job WHERE media_id=? ORDER BY created_at DESC LIMIT 1', (media_id,)).fetchone()
        conn.execute('UPDATE event_media SET processing_generation=?,processing_started_at=NULL WHERE media_id=?', (generation, media_id))
        conn.execute('''INSERT INTO processing_job(job_id,media_id,generation,parent_job_id,status,redaction_mode,kind,created_at,updated_at)
            VALUES(?,?,?,?,'queued',?,?,?,?)''', (ident, media_id, generation, parent[0] if parent else None,
                                               media['redaction_mode'], media['kind'], time.time(), time.time()))
    return ident


def claim(node_id):
    with get_connection() as conn:
        begin_write(conn)
        conn.execute('UPDATE field_node SET last_seen_at=? WHERE node_id=?', (time.time(), node_id))
        if conn.execute('SELECT 1 FROM host_ai_job WHERE worker_node_id=? AND cleanup_pending=1', (node_id,)).fetchone():
            return {'job': None}
        load = conn.execute('SELECT MAX(busy_until) FROM processing_inference_load').fetchone()
        if load and load[0] and load[0] > time.time():
            return {'job': None}
        if conn.execute("SELECT 1 FROM processing_job WHERE worker_node_id=? AND local_cleanup_status='pending'", (node_id,)).fetchone():
            return {'job': None}
        if conn.execute("SELECT 1 FROM processing_job WHERE worker_node_id=? AND status='running' AND lease_until>?", (node_id, time.time())).fetchone():
            return {'job': None}
        rows = conn.execute("SELECT * FROM processing_job WHERE status='queued' ORDER BY created_at,job_id").fetchall()
        for candidate in rows:
            media = conn.execute('SELECT * FROM event_media WHERE media_id=?', (candidate['media_id'],)).fetchone()
            if media:
                if media['request_id']:
                    clip = conn.execute('SELECT status FROM event_clip_request WHERE request_id=?', (media['request_id'],)).fetchone()
                    if not clip or clip[0] != 'received':
                        continue
                retention = conn.execute('SELECT decision FROM event_retention WHERE event_id=?', (media['event_id'],)).fetchone()
                if retention and retention[0] == 'delete':
                    conn.execute("UPDATE processing_job SET status='cancelled',updated_at=? WHERE job_id=?", (time.time(), candidate['job_id']))
                    continue
            if not media or media['status'] != 'processing' or not media['source_path'] or \
                    media['source_expires_at'] <= time.time() or not private_source_path(media['source_path']).is_file():
                conn.execute("UPDATE processing_job SET status='failed',error_code='source_file_missing',finished_at=?,updated_at=? WHERE job_id=?", (time.time(), time.time(), candidate['job_id']))
                conn.execute("UPDATE event_media SET status='failed',redaction_status='failed',error_code='source_file_missing' WHERE media_id=? AND status='processing'", (candidate['media_id'],))
                continue
            token = secrets.token_urlsafe(32)
            expiry = min(time.time() + LEASE_SECONDS, media['source_expires_at'])
            conn.execute("""UPDATE processing_job SET status='running',worker_node_id=?,lease_token_hash=?,lease_until=?,
                started_at=?,updated_at=?,local_cleanup_status='pending' WHERE job_id=?""",
                         (node_id, hashlib.sha256(token.encode()).hexdigest(), expiry, time.time(), time.time(), candidate['job_id']))
            conn.execute('UPDATE event_media SET processing_started_at=? WHERE media_id=?', (time.time(), candidate['media_id']))
            result = public(job(conn, candidate['job_id']))
            return {'job': {**result, 'lease_token': token, 'source_url': f"/api/processing/jobs/{candidate['job_id']}/source"}}
    return {'job': None}


def renew(job_id, node_id, token):
    with get_connection() as conn:
        begin_write(conn)
        row = authorized(conn, job_id, node_id, token)
        conn.execute('UPDATE field_node SET last_seen_at=? WHERE node_id=?', (time.time(), node_id))
        source_expiry = conn.execute('SELECT source_expires_at FROM event_media WHERE media_id=?', (row['media_id'],)).fetchone()[0]
        until = min(time.time() + LEASE_SECONDS, row['started_at'] + MAX_SECONDS, source_expiry)
        if until <= time.time():
            raise HTTPException(409, 'processing_deadline_exceeded')
        conn.execute('UPDATE processing_job SET lease_until=?,updated_at=? WHERE job_id=?', (until, time.time(), job_id))
        return public(job(conn, job_id))


def finish_failure(job_id, node_id, token, code):
    with get_connection() as conn:
        begin_write(conn)
        row = authorized(conn, job_id, node_id, token)
        conn.execute("UPDATE processing_job SET status='failed',error_code=?,finished_at=?,updated_at=? WHERE job_id=?", (code, time.time(), time.time(), job_id))
        conn.execute("UPDATE event_media SET status='failed',redaction_status='failed',error_code=?,processing_started_at=NULL WHERE media_id=?", (code, row['media_id']))
        return public(job(conn, job_id))


def cleanup(job_id, node_id):
    with get_connection() as conn:
        begin_write(conn)
        row = job(conn, job_id)
        if row['worker_node_id'] != node_id:
            raise HTTPException(403, 'processing_worker_mismatch')
        if row['status'] in {'queued', 'running'}:
            raise HTTPException(409, 'processing_still_active')
        conn.execute("UPDATE processing_job SET local_cleanup_status='confirmed',updated_at=? WHERE job_id=?", (time.time(), job_id))
        event = conn.execute('SELECT event_id FROM event_media WHERE media_id=?', (row['media_id'],)).fetchone()[0]
    from backend.media.service import get_retention, delete_event_media
    if get_retention(event)['decision'] == 'delete':
        delete_event_media(event)
    with get_connection() as conn:
        return public(job(conn, job_id))


def expire_jobs():
    with get_connection() as conn:
        begin_write(conn)
        expired = conn.execute("SELECT * FROM processing_job WHERE status='running' AND lease_until<=?", (time.time(),)).fetchall()
        for row in expired:
            conn.execute("UPDATE processing_job SET status='failed',error_code='processing_lease_expired',finished_at=?,updated_at=? WHERE job_id=?", (time.time(), time.time(), row['job_id']))
            conn.execute("UPDATE event_media SET status='failed',redaction_status='failed',error_code='processing_interrupted',processing_started_at=NULL WHERE media_id=? AND status='processing' AND processing_generation=?", (row['media_id'], row['generation']))
        queued = conn.execute("""SELECT j.* FROM processing_job j JOIN event_media m ON m.media_id=j.media_id
            WHERE j.status='queued' AND (m.source_expires_at<=? OR m.source_path='')""", (time.time(),)).fetchall()
        for row in queued:
            conn.execute("UPDATE processing_job SET status='failed',error_code='source_expired',finished_at=?,updated_at=? WHERE job_id=?", (time.time(), time.time(), row['job_id']))
            conn.execute("UPDATE event_media SET status='failed',redaction_status='failed',error_code='source_expired' WHERE media_id=? AND status='processing'", (row['media_id'],))


def ingest(event_id, camera_id, kind, role, source, mode, request_id=None):
    import cv2
    if kind == 'image':
        frame = cv2.imread(str(source))
        if frame is None or frame.shape[0] * frame.shape[1] > 16000000:
            raise ValueError('invalid_frame')
    else:
        with source.open('rb') as file:
            signature = file.read(12)
        if len(signature) < 12 or signature[4:8] != b'ftyp':
            raise ValueError('invalid_video_source')
    from backend.media.service import register_media, _preserve_source, get_media, _fail, _finish_processing
    ident = register_media(event_id, camera_id, kind, role, mode, request_id)
    try:
        _preserve_source(ident, source=source, consume_source=True)
        enqueue(ident)
        if request_id:
            with get_connection() as conn:
                begin_write(conn)
                decision = conn.execute('SELECT decision FROM event_retention WHERE event_id=?', (event_id,)).fetchone()[0]
                if decision == 'delete':
                    raise ValueError('event_media_deleted')
                changed = conn.execute("UPDATE event_clip_request SET status='received',updated_at=? WHERE request_id=? AND status='pending'", (time.time(), request_id))
                if changed.rowcount != 1:
                    raise ValueError('clip_request_already_finished')
    except Exception as error:
        with get_connection() as conn:
            begin_write(conn)
            conn.execute("UPDATE processing_job SET status='failed',error_code='clip_ingest_failed',finished_at=?,updated_at=? WHERE media_id=? AND status='queued'", (time.time(), time.time(), ident))
            if request_id:
                conn.execute("UPDATE event_clip_request SET status='failed',error_code='clip_ingest_failed',updated_at=? WHERE request_id=? AND status='pending'", (time.time(), request_id))
        _fail(ident, error)
        _finish_processing(ident)
        raise
    return get_media(ident)


def reprocess(media_id, mode):
    from backend.media.service import reprocess_unavailable_reason, get_media
    with get_connection() as conn:
        begin_write(conn)
        row = conn.execute('SELECT * FROM event_media WHERE media_id=?', (media_id,)).fetchone()
        if not row:
            raise ValueError('media_not_found')
        retention = conn.execute('SELECT decision FROM event_retention WHERE event_id=?', (row['event_id'],)).fetchone()
        reason = reprocess_unavailable_reason(dict(row), retention['decision'])
        if reason:
            raise ValueError(reason)
        if not row['source_path']:
            raise ValueError('source_file_missing')  # Remote worker never consumes published legacy output.
        if row['request_id']:
            request = conn.execute('SELECT * FROM event_clip_request WHERE request_id=? AND event_id=? AND camera_id=?',
                                   (row['request_id'], row['event_id'], row['camera_id'])).fetchone()
            if not request:
                raise ValueError('clip_request_not_found')
            # Ingest can preserve the complete upload before queue creation fails.
            # Recover delivery only from that failure, after the source checks above.
            # An explicit unavailable decision must never be silently reversed.
            if request['status'] == 'failed' and request['error_code'] == 'clip_ingest_failed':
                conn.execute("UPDATE event_clip_request SET status='received',error_code=NULL,updated_at=? WHERE request_id=?",
                             (time.time(), row['request_id']))
            elif request['status'] != 'received':
                raise ValueError('clip_request_not_received')
        conn.execute("UPDATE event_media SET status='processing',redaction_status='unprocessed',redaction_mode=?,error_code=NULL WHERE media_id=?", (mode, media_id))
    # Failure leaves a recoverable failed record instead of an untracked processing state.
    try:
        enqueue(media_id)
    except Exception as error:
        from backend.media.service import _fail
        _fail(media_id, error)
        raise
    return get_media(media_id)
