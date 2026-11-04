"""Short-lived camera stills. Only leased processors can obtain the raw source."""
import hashlib
import re
import secrets
import time
from uuid import uuid4

from fastapi import HTTPException

from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.cameras.service import CameraStateError
from backend.media.paths import WORK_ROOT
from backend.video import service as video

ROOT = WORK_ROOT / 'camera_snapshots'
TTL_SECONDS = 120


def path(ident, suffix):
    if not re.fullmatch(r'[0-9a-f-]{36}', ident) or suffix not in ('.mp4', '.jpg'):
        raise HTTPException(404, 'snapshot_not_found')
    result = ROOT / (ident + suffix)
    if result.is_symlink() or result.resolve().parent != ROOT.resolve():
        raise HTTPException(409, 'unsafe_snapshot_path')
    return result


def row(conn, ident):
    result = conn.execute('SELECT * FROM camera_snapshot WHERE snapshot_id=?', (ident,)).fetchone()
    if not result:
        raise HTTPException(404, 'snapshot_not_found')
    return dict(result)


def public(item):
    status = 'expired' if item['expires_at'] <= time.time() else item['status']
    return {**{key: item[key] for key in ('snapshot_id', 'camera_id', 'capture_start_at', 'expires_at', 'error_code')},
            'status': status, 'redaction_mode': 'scrfd',
            'image_url': f"/api/cameras/{item['camera_id']}/snapshots/{item['snapshot_id']}/image" if status == 'ready' else None}


def maintain():
    removed = 0
    with get_connection() as conn:
        begin_write(conn)
        conn.execute("UPDATE camera_snapshot SET status='failed',error_code='snapshot_lease_expired' WHERE status='running' AND lease_until<=?", (time.time(),))
        for item in conn.execute('SELECT * FROM camera_snapshot').fetchall():
            expired = item['expires_at'] <= time.time()
            for suffix in ('.mp4', '.jpg'):
                if expired or (suffix == '.mp4' and item['status'] in ('ready', 'failed')):
                    try:
                        file = path(item['snapshot_id'], suffix)
                        if file.exists():
                            file.unlink()
                            removed += 1
                    except (OSError, HTTPException):
                        continue
            if expired:
                conn.execute("UPDATE camera_snapshot SET status='expired' WHERE snapshot_id=?", (item['snapshot_id'],))
                if item['local_cleanup_status'] != 'pending' and not any(path(item['snapshot_id'], s).exists() for s in ('.mp4', '.jpg')):
                    conn.execute('DELETE FROM camera_snapshot WHERE snapshot_id=?', (item['snapshot_id'],))
        if ROOT.exists():
            known = {r[0] for r in conn.execute('SELECT snapshot_id FROM camera_snapshot')}
            for file in ROOT.iterdir():
                if file.suffix in ('.mp4', '.jpg') and file.stem not in known and file.stat().st_mtime < time.time()-TTL_SECONDS:
                    try:
                        path(file.stem, file.suffix).unlink(missing_ok=True)
                        removed += 1
                    except (OSError, HTTPException):
                        pass
    return removed


def request(camera_id):
    maintain()
    ident = str(uuid4())
    created_file = None
    try:
        with get_connection() as conn:
            begin_write(conn)
            from backend.cameras.service import require_camera
            require_camera(conn, camera_id, active=True)
            previous = conn.execute("SELECT * FROM camera_snapshot WHERE camera_id=? AND status IN ('queued','running') AND expires_at>? ORDER BY created_at DESC LIMIT 1", (camera_id, time.time())).fetchone()
            if previous:
                return public(dict(previous))
            active = conn.execute("SELECT COUNT(*) FROM camera_snapshot WHERE status IN ('queued','running','ready') AND expires_at>?", (time.time(),)).fetchone()[0]
            if active >= 8 or conn.execute('SELECT COUNT(*) FROM camera_snapshot').fetchone()[0] >= 64:
                raise HTTPException(429, 'snapshot_capacity_reached')
            source = conn.execute('''SELECT c.*,s.node_id FROM video_chunk c JOIN video_stream s ON s.stream_id=c.stream_id
                WHERE s.camera_id=? AND s.status='active' AND c.expires_at>? ORDER BY c.received_at DESC LIMIT 1''',
                                  (camera_id, time.time())).fetchone()
            if not source:
                raise HTTPException(409, 'camera_frame_unavailable')
            video.source_allowed(conn, camera_id, source['node_id'])
            try:
                data = video.chunk_path(source['chunk_id']).read_bytes()
            except OSError:
                raise HTTPException(409, 'camera_frame_unavailable')
            if len(data) > video.MAX_BYTES:
                raise HTTPException(409, 'snapshot_source_too_large')
            ROOT.mkdir(parents=True, exist_ok=True)
            created_file = path(ident, '.mp4')
            with created_file.open('xb') as output:
                output.write(data)
            timestamp = time.time()
            conn.execute('''INSERT INTO camera_snapshot(snapshot_id,camera_id,source_node_id,capture_start_at,status,expires_at,created_at)
                VALUES(?,?,?,?,'queued',?,?)''', (ident, camera_id, source['node_id'], source['capture_start_at'], timestamp+TTL_SECONDS, timestamp))
            return public(row(conn, ident))
    except Exception:
        if created_file:
            created_file.unlink(missing_ok=True)
        raise


def claim(node_id):
    maintain()
    with get_connection() as conn:
        begin_write(conn)
        from backend.processing.priority import busy_until
        blocked = any(conn.execute(sql, (node_id,)).fetchone() for sql in (
            "SELECT 1 FROM processing_job WHERE worker_node_id=? AND local_cleanup_status='pending'",
            'SELECT 1 FROM host_ai_job WHERE worker_node_id=? AND cleanup_pending=1',
            "SELECT 1 FROM camera_snapshot WHERE worker_node_id=? AND local_cleanup_status='pending'"))
        if busy_until(conn) > time.time() or blocked:
            return {'job': None}
        for item in conn.execute("SELECT * FROM camera_snapshot WHERE status='queued' AND expires_at>? ORDER BY created_at", (time.time(),)).fetchall():
            try:
                video.source_allowed(conn, item['camera_id'], item['source_node_id'])
                if not path(item['snapshot_id'], '.mp4').is_file():
                    raise HTTPException(409, 'snapshot_source_missing')
            except (HTTPException, CameraStateError):
                conn.execute("UPDATE camera_snapshot SET status='failed',error_code='snapshot_source_unavailable' WHERE snapshot_id=?", (item['snapshot_id'],))
                continue
            token = secrets.token_urlsafe(32)
            until = min(time.time()+90, item['expires_at'])
            conn.execute("""UPDATE camera_snapshot SET status='running',worker_node_id=?,lease_hash=?,lease_until=?,local_cleanup_status='pending' WHERE snapshot_id=?""",
                         (node_id, hashlib.sha256(token.encode()).hexdigest(), until, item['snapshot_id']))
            return {'job': {'job_id': item['snapshot_id'], 'lease_token': token, 'lease_until': until,
                            'source_url': f"/api/processing/snapshots/{item['snapshot_id']}/source"}}
    return {'job': None}


def authorize(conn, ident, node_id, token, terminal=False):
    item = row(conn, ident)
    if item['worker_node_id'] != node_id or not token or not secrets.compare_digest(item['lease_hash'] or '', hashlib.sha256(token.encode()).hexdigest()):
        raise HTTPException(403, 'snapshot_lease_invalid')
    video.source_allowed(conn, item['camera_id'], item['source_node_id'])
    if item['expires_at'] <= time.time() or not (terminal and item['status'] == 'ready') and (
            item['status'] != 'running' or item['lease_until'] <= time.time()):
        raise HTTPException(409, 'snapshot_inactive')
    return item
