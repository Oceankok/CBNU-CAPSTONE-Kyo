"""Private, bounded short-video buffer. Only trusted processors can read raw chunks."""
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
    if row['status'] != 'active' or row['last_seen_at'] + BUFFER_SECONDS <= time.time():
        raise HTTPException(409, 'video_stream_expired')
    return row


def cleanup():
    timestamp = time.time()
    removed = 0
    with get_connection() as conn:
        begin_write(conn)
        conn.execute("UPDATE video_stream SET status='expired' WHERE status='active' AND last_seen_at<=?", (timestamp-BUFFER_SECONDS,))
        for row in conn.execute('SELECT chunk_id FROM video_chunk WHERE expires_at<=?', (timestamp,)).fetchall():
            try:
                chunk_path(row['chunk_id']).unlink(missing_ok=True)
            except (OSError, HTTPException):
                continue  # Retain metadata and quota until a later cleanup succeeds.
            conn.execute('DELETE FROM video_chunk WHERE chunk_id=?', (row['chunk_id'],))
            removed += 1
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
        conn.execute("DELETE FROM video_stream WHERE status!='active' AND last_seen_at<? AND NOT EXISTS (SELECT 1 FROM video_chunk c WHERE c.stream_id=video_stream.stream_id)", (timestamp-86400,))
    return removed


def create_stream(camera_id, node_id):
    cleanup()
    with get_connection() as conn:
        begin_write(conn)
        source_allowed(conn, camera_id, node_id)
        # An authenticated current source node may replace its camera's old session.
        conn.execute("UPDATE video_stream SET status='closed' WHERE camera_id=? AND status='active'", (camera_id,))
        if conn.execute("SELECT COUNT(*) FROM video_stream WHERE status='active'").fetchone()[0] >= 8:
            raise HTTPException(429, 'video_stream_capacity_reached')
        if conn.execute('SELECT COUNT(*) FROM video_stream').fetchone()[0] >= 10000:
            raise HTTPException(429, 'video_stream_history_full')
        ident, timestamp = str(uuid4()), time.time()
        conn.execute("INSERT INTO video_stream(stream_id,camera_id,node_id,status,created_at,last_seen_at) VALUES(?,?,?,'active',?,?)", (ident, camera_id, node_id, timestamp, timestamp))
        conn.execute('UPDATE field_node SET last_seen_at=? WHERE node_id=?', (timestamp, node_id))
    return {'stream_id': ident, 'camera_id': camera_id, 'buffer_seconds': BUFFER_SECONDS, 'max_chunk_bytes': MAX_BYTES}


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
    cleanup()
    moved = None
    try:
        with get_connection() as conn:
            begin_write(conn)
            current = stream(conn, ident, node_id)
            old = conn.execute('SELECT * FROM video_chunk WHERE stream_id=? AND sequence=?', (ident, sequence)).fetchone()
            if old:
                if old['sha256'] != digest or old['capture_start_at'] != capture_start or old['duration_ms'] != duration_ms:
                    raise HTTPException(409, 'video_chunk_conflict')
                return {'received': True, 'duplicate': True, 'sequence': sequence, 'chunk_id': old['chunk_id']}
            previous = current['last_sequence']
            if sequence <= previous:
                raise HTTPException(409, 'video_sequence_out_of_order')
            total = conn.execute('SELECT COALESCE(SUM(byte_size),0) FROM video_chunk').fetchone()[0]
            camera = conn.execute('''SELECT COALESCE(SUM(c.byte_size),0),COUNT(*) FROM video_chunk c
                JOIN video_stream s ON s.stream_id=c.stream_id WHERE s.camera_id=?''', (current['camera_id'],)).fetchone()
            if total+size > TOTAL_BYTES or camera[0]+size > CAMERA_BYTES or camera[1] >= 40:
                raise HTTPException(429, 'video_buffer_full', headers={'Retry-After':'1'})
            key, timestamp = str(uuid4()), time.time()
            target = chunk_path(key)
            source.replace(target)
            moved = target
            conn.execute('INSERT INTO video_chunk VALUES(?,?,?,?,?,?,?,?,?)', (key, ident, sequence, digest, size, capture_start, duration_ms, timestamp, timestamp+BUFFER_SECONDS))
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
