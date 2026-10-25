"""Administrator-declared overlapping camera views; never deduplicate incidents."""
from datetime import datetime, timezone
import time
from uuid import uuid4

from fastapi import HTTPException

from backend.cameras.service import public_camera, require_camera


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def find(conn, group_id):
    row = conn.execute('SELECT * FROM camera_group WHERE group_id=?', (group_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'camera_group_not_found')
    return row


def serialize(conn, group_id):
    result = dict(find(conn, group_id))
    camera_ids = [row[0] for row in conn.execute('SELECT camera_id FROM camera_group_member WHERE group_id=? ORDER BY camera_id', (group_id,))]
    result['camera_ids'] = camera_ids
    result['cameras'] = [public_camera(conn, ident) for ident in camera_ids]
    return result


def replace_members(conn, group_id, camera_ids):
    if len(set(camera_ids)) != len(camera_ids):
        raise HTTPException(422, 'duplicate_group_camera')
    for camera_id in camera_ids:
        require_camera(conn, camera_id)
    conn.execute('DELETE FROM camera_group_member WHERE group_id=?', (group_id,))
    conn.executemany('INSERT INTO camera_group_member(group_id,camera_id) VALUES(?,?)', [(group_id, ident) for ident in camera_ids])


def register_references(conn, event_id, trigger_camera):
    """Called in the event transaction; each active peer gets one delivery request."""
    cameras = conn.execute('''SELECT DISTINCT other.camera_id FROM camera_group_member origin
        JOIN camera_group_member other ON other.group_id=origin.group_id
        JOIN camera_info c ON c.camera_id=other.camera_id
        WHERE origin.camera_id=? AND other.camera_id!=? AND c.status='active' ORDER BY other.camera_id''',
        (trigger_camera, trigger_camera)).fetchall()
    stamp = time.time()
    for camera in cameras:
        conn.execute('''INSERT INTO event_clip_request(request_id,event_id,camera_id,status,role,created_at,updated_at)
            VALUES(?,?,?,'pending','reference',?,?)''', (str(uuid4()), event_id, camera['camera_id'], stamp, stamp))
