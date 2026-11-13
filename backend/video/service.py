"""Private, bounded short-video buffer. Only trusted processors can read raw chunks."""
from backend.capture_context.service import begin as capture_begin
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import time
from uuid import uuid4

from fastapi import HTTPException

from backend.cameras.service import require_camera
from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.media.paths import WORK_ROOT

ROOT = WORK_ROOT / 'video_ingest'
BUFFER_SECONDS = 30

def context_raw_retention_seconds():
    """Explicit deployment opt-in, never a lease or quota expansion."""
    import os
    value=os.environ.get("PPE_CONTEXT_RAW_RETENTION_SECONDS","30")
    if value not in {"30","60","120"}:
        raise HTTPException(503,"invalid_context_raw_retention_policy")
    return int(value)

MAX_BYTES = 8 * 1024 * 1024
CAMERA_BYTES = 64 * 1024 * 1024
TOTAL_BYTES = 256 * 1024 * 1024


def chunk_path(ident):
    if not re.fullmatch(r'[0-9a-f-]{36}', ident):
        raise HTTPException(404, 'video_chunk_not_found')
    root = ROOT.resolve()
    path = root / (ident + '.mp4')
    if path.is_symlink() or path.resolve().parent != root:
        raise HTTPException(409, 'unsafe_video_chunk')
    return path


def source_allowed(conn, camera_id, node_id):
    require_camera(conn, camera_id, active=True)
    row = conn.execute('''SELECT n.source_node_id,f.is_active FROM camera_node n
        JOIN field_node f ON f.node_id=n.source_node_id WHERE n.camera_id=?''', (camera_id,)).fetchone()
    if not row or row['source_node_id'] != node_id or not row['is_active']:
        raise HTTPException(403, 'camera_source_node_required')


def stream(conn, ident, node_id=None):
    row = conn.execute('SELECT * FROM video_stream WHERE stream_id=?', (ident,)).fetchone()
    if not row:
        raise HTTPException(404, 'video_stream_not_found')
    if node_id is not None and row['node_id'] != node_id:
        raise HTTPException(403, 'video_stream_owner_required')
    source_allowed(conn, row['camera_id'], row['node_id'])
    from backend.capture_context import service as contexts
    extra=contexts.stream_fields(conn,ident)
    if extra:contexts.owned(conn,extra['capture_context_id'],node_id,row['camera_id'],active=True)
    if row['status'] != 'active' or (not extra and row['last_seen_at'] + BUFFER_SECONDS <= time.time()):
        raise HTTPException(409, 'video_stream_expired')
    return row


def _delete_expired_chunks(conn, timestamp):
    removed = 0
    for row in conn.execute('SELECT chunk_id FROM video_chunk WHERE expires_at<=?', (timestamp,)).fetchall():
        try:
            chunk_path(row['chunk_id']).unlink(missing_ok=True)
        except (OSError, HTTPException):
            continue  # Failed file deletion must continue consuming quota.
        conn.execute('DELETE FROM video_chunk WHERE chunk_id=?', (row['chunk_id'],))
        removed += 1
    return removed


def _buffer_full(conn, camera_id, size):
    total = conn.execute('SELECT COALESCE(SUM(byte_size),0) FROM video_chunk').fetchone()[0]
    camera = conn.execute('''SELECT COALESCE(SUM(c.byte_size),0),COUNT(*) FROM video_chunk c
        JOIN video_stream s ON s.stream_id=c.stream_id WHERE s.camera_id=?''', (camera_id,)).fetchone()
    return total+size > TOTAL_BYTES or camera[0]+size > CAMERA_BYTES or camera[1] >= 40


def cleanup():
    timestamp = time.time()
    removed = 0
    with get_connection() as conn:
        capture_begin(conn)
        conn.execute("UPDATE video_stream SET status='expired' WHERE status='active' AND last_seen_at<=? AND NOT EXISTS (SELECT 1 FROM capture_context_stream m WHERE m.stream_id=video_stream.stream_id)", (timestamp-BUFFER_SECONDS,))
        removed += _delete_expired_chunks(conn, timestamp)
        referenced = {row[0] for row in conn.execute('SELECT chunk_id FROM video_chunk')}
        # Orphan files may remain after a process dies between file publish and DB commit.
        if ROOT.exists():
            for path in ROOT.glob('*.mp4'):
                try:
                    if path.stem not in referenced and not path.is_symlink() and path.stat().st_mtime < timestamp-60:
                        chunk_path(path.stem).unlink()
                        removed += 1
                except (OSError, HTTPException):
                    pass
        # Empty, inactive sessions contain no raw data; bound their metadata lifetime too.
        conn.execute("DELETE FROM video_stream WHERE status!='active' AND last_seen_at<? AND NOT EXISTS (SELECT 1 FROM video_chunk c WHERE c.stream_id=video_stream.stream_id) AND NOT EXISTS (SELECT 1 FROM capture_context_stream m WHERE m.stream_id=video_stream.stream_id)", (timestamp-86400,))
    return removed


def create_stream(camera_id, node_id, *, capture_context_id=None,requested_id=None):
    cleanup()
    policy_fields=({'raw_retention_seconds_for_new_uploads':context_raw_retention_seconds()}
                   if capture_context_id else {})
    with get_connection() as conn:
        capture_begin(conn)
        source_allowed(conn, camera_id, node_id)
        from backend.capture_context import service as contexts
        if capture_context_id is None and (contexts.enabled() or conn.execute('SELECT 1 FROM capture_context_registry WHERE camera_id=?',(camera_id,)).fetchone()):
            raise HTTPException(422,'capture_context_required')
        if capture_context_id is not None:
            contexts.require_enabled();contexts.owned(conn,capture_context_id,node_id,camera_id,active=True)
            if requested_id is None:raise HTTPException(422,'idempotent_stream_id_required')
        if requested_id:
            old=conn.execute('SELECT * FROM video_stream WHERE stream_id=?',(requested_id,)).fetchone()
            if old:
                extra=contexts.stream_fields(conn,requested_id)
                if old['camera_id']!=camera_id or old['node_id']!=node_id or extra.get('capture_context_id')!=capture_context_id:
                    raise HTTPException(409,'video_stream_request_conflict')
                return {'stream_id':requested_id,'camera_id':camera_id,'buffer_seconds':BUFFER_SECONDS,'max_chunk_bytes':MAX_BYTES,**extra,**policy_fields}
        if capture_context_id and conn.execute('SELECT 1 FROM capture_context_stream WHERE context_id=?',(capture_context_id,)).fetchone():
            raise HTTPException(409,'capture_context_stream_already_exists')
        # An authenticated current source node may replace its camera's old session.
        conn.execute("UPDATE video_stream SET status='closed' WHERE camera_id=? AND status='active'", (camera_id,))
        if conn.execute("SELECT COUNT(*) FROM video_stream WHERE status='active'").fetchone()[0] >= 8:
            raise HTTPException(429, 'video_stream_capacity_reached')
        if conn.execute('SELECT COUNT(*) FROM video_stream').fetchone()[0] >= 10000:
            raise HTTPException(429, 'video_stream_history_full')
        ident, timestamp = requested_id or str(uuid4()), time.time()
        conn.execute("INSERT INTO video_stream(stream_id,camera_id,node_id,status,created_at,last_seen_at) VALUES(?,?,?,'active',?,?)", (ident, camera_id, node_id, timestamp, timestamp))
        if capture_context_id:
            conn.execute('INSERT INTO capture_context_stream(stream_id,context_id) VALUES(?,?)',(ident,capture_context_id))
        extra=contexts.stream_fields(conn,ident)
        conn.execute('UPDATE field_node SET last_seen_at=? WHERE node_id=?', (timestamp, node_id))
    return {'stream_id': ident, 'camera_id': camera_id, 'buffer_seconds': BUFFER_SECONDS, 'max_chunk_bytes': MAX_BYTES,**extra,**policy_fields}


def validate_video(path, duration_ms):
    import cv2
    with path.open('rb') as file:
        header = file.read(12)
    if len(header) < 12 or header[4:8] != b'ftyp':
        raise HTTPException(422, 'invalid_mp4_chunk')
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        expected = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        area = cap.get(cv2.CAP_PROP_FRAME_WIDTH)*cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        code = int(cap.get(cv2.CAP_PROP_FOURCC))
        codec = ''.join(chr((code >> (8*i)) & 255) for i in range(4)).lower()
        if not cap.isOpened() or not math.isfinite(fps) or not 5 <= fps <= 60 or codec not in {'avc1', 'h264'}:
            raise HTTPException(422, 'h264_chunk_required')
        if not math.isfinite(expected) or not 1 <= expected <= 180 or not 1 <= area <= 1920*1080:
            raise HTTPException(422, 'video_chunk_dimensions_or_frames_exceeded')
        count = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            count += 1
            if count > 180 or frame.shape[0]*frame.shape[1] > 1920*1080:
                raise HTTPException(422, 'video_chunk_dimensions_or_frames_exceeded')
        if count != int(expected) or abs(count/fps*1000-duration_ms) > 250:
            raise HTTPException(422, 'video_chunk_duration_mismatch')
    finally:
        cap.release()


def accept(ident, node_id, sequence, capture_start, duration_ms, source):
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    size = source.stat().st_size
    moved = None
    try:
        with get_connection() as conn:
            capture_begin(conn)
            current = stream(conn, ident, node_id)
            from backend.capture_context.service import stream_fields,owned
            extra=stream_fields(conn,ident)
            if extra:
                context=owned(conn,extra['capture_context_id'],node_id,current['camera_id'],active=True)
                if datetime.fromisoformat(capture_start)<datetime.fromisoformat(context['valid_from']) or datetime.fromisoformat(capture_start).timestamp()+duration_ms/1000>time.time():
                    raise HTTPException(422,'capture_before_session')
            if extra:
                receipt=conn.execute('SELECT * FROM capture_chunk_receipt WHERE stream_id=? AND sequence=?',(ident,sequence)).fetchone()
                if receipt:
                    if receipt['sha256']!=digest or receipt['capture_start_at']!=capture_start or receipt['duration_ms']!=duration_ms:
                        raise HTTPException(409,'video_chunk_conflict')
                    return {'received':True,'duplicate':True,'sequence':sequence,'chunk_id':receipt['chunk_id']}
            old = conn.execute('SELECT * FROM video_chunk WHERE stream_id=? AND sequence=?', (ident, sequence)).fetchone()
            if old:
                if old['sha256'] != digest or old['capture_start_at'] != capture_start or old['duration_ms'] != duration_ms:
                    raise HTTPException(409, 'video_chunk_conflict')
                return {'received': True, 'duplicate': True, 'sequence': sequence, 'chunk_id': old['chunk_id']}
            previous = current['last_sequence']
            if extra and sequence > previous + 1:
                raise HTTPException(409, 'capture_sequence_gap')
            if sequence <= previous:
                raise HTTPException(409, 'video_sequence_out_of_order')
            if _buffer_full(conn, current['camera_id'], size):
                # Reclaim expired payloads only on quota pressure, under the same
                # write lock. Do not scan orphan files on the upload hot path.
                _delete_expired_chunks(conn, time.time())
                if _buffer_full(conn, current['camera_id'], size):
                    raise HTTPException(429, 'video_buffer_full', headers={'Retry-After':'1'})
            retention=context_raw_retention_seconds() if extra else BUFFER_SECONDS
            key, timestamp = str(uuid4()), time.time()
            target = chunk_path(key)
            source.replace(target)
            moved = target
            conn.execute('INSERT INTO video_chunk VALUES(?,?,?,?,?,?,?,?,?)', (key, ident, sequence, digest, size, capture_start, duration_ms, timestamp, timestamp+retention))
            if extra:
                conn.execute('INSERT INTO capture_chunk_receipt VALUES(?,?,?,?,?,?)',(ident,sequence,key,digest,capture_start,duration_ms))
            conn.execute('UPDATE video_stream SET last_seen_at=?,last_sequence=?,sequence_gap_count=sequence_gap_count+? WHERE stream_id=?',
                         (timestamp, sequence, sequence-previous-1, ident))
            conn.execute('UPDATE field_node SET last_seen_at=? WHERE node_id=?', (timestamp, node_id))
        return {'received': True, 'duplicate': False, 'sequence': sequence, 'chunk_id': key,
                'sequence_gap': sequence-previous-1}
    except Exception:
        if moved:
            moved.unlink(missing_ok=True)
        raise


def capture_time(value):
    try:
        timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            raise ValueError()
        return timestamp.astimezone(timezone.utc).isoformat(timespec='milliseconds')
    except (ValueError, OverflowError):
        raise HTTPException(422, 'capture_start_timezone_required')
