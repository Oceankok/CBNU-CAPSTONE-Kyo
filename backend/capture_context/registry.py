"""Opt-in internal registry prototype. No public route or automatic migration.

Caller must authorize the camera/session, hold the shared write transaction for
DDL/writes and supply trusted server time. This module is not an authentication
boundary or a sender acknowledgement validator. Existing camera PATCH does not
use it: do not enable operational attribution before integrating that boundary.
"""
from datetime import datetime
from uuid import uuid4
from .validation import CaptureContext, resolve_capture_context, utc

POLICY = 'stop_acknowledge_reassign_restart_v1'


def _gate(policy):
    if policy != POLICY:
        raise ValueError('explicit_reassignment_policy_required')


def prepare_registry(conn, *, policy):
    _gate(policy)
    conn.execute('''CREATE TABLE IF NOT EXISTS capture_context_registry (
        context_id TEXT PRIMARY KEY,
        camera_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        assignment_version INTEGER NOT NULL CHECK(assignment_version>0),
        zone_name TEXT NOT NULL,
        valid_from TEXT NOT NULL,
        valid_until TEXT,
        policy_version TEXT NOT NULL,
        UNIQUE(camera_id, session_id),
        UNIQUE(camera_id, assignment_version)
    )''')
    conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS capture_context_active_camera
        ON capture_context_registry(camera_id) WHERE valid_until IS NULL''')


def _context(row):
    return CaptureContext(row['context_id'], row['camera_id'], row['session_id'],
                          row['assignment_version'], row['zone_name'],
                          datetime.fromisoformat(row['valid_from']),
                          datetime.fromisoformat(row['valid_until']) if row['valid_until'] else None)


def issue(conn, *, camera_id, session_id, server_now, policy):
    _gate(policy)
    if not all(isinstance(v, str) and v.strip() and len(v) <= 100 for v in (camera_id, session_id)):
        raise ValueError('invalid_camera_or_session')
    now = utc(server_now)
    camera = conn.execute('SELECT zone_name,status FROM camera_info WHERE camera_id=?', (camera_id,)).fetchone()
    if camera is None or camera['status'] != 'active':
        raise ValueError('active_camera_required')
    zone = camera['zone_name']
    if not isinstance(zone, str) or not zone.strip():
        raise ValueError('assigned_zone_required')
    old = conn.execute('SELECT * FROM capture_context_registry WHERE camera_id=? AND session_id=?',
                       (camera_id, session_id)).fetchone()
    if old:
        if old['valid_until'] is not None:
            raise ValueError('retired_session_cannot_restart')
        if old['zone_name'] != zone or old['policy_version'] != policy:
            raise ValueError('active_context_assignment_conflict')
        return _context(old)
    if conn.execute('SELECT 1 FROM capture_context_registry WHERE camera_id=? AND valid_until IS NULL',
                    (camera_id,)).fetchone():
        raise ValueError('active_context_must_be_retired')
    rows = conn.execute('SELECT assignment_version,valid_until FROM capture_context_registry WHERE camera_id=?',
                        (camera_id,)).fetchall()
    if any(now < utc(datetime.fromisoformat(r['valid_until'])) for r in rows):
        raise ValueError('server_time_precedes_retirement')
    version = max((r['assignment_version'] for r in rows), default=0) + 1
    context_id = str(uuid4())
    conn.execute('''INSERT INTO capture_context_registry
        (context_id,camera_id,session_id,assignment_version,zone_name,valid_from,policy_version)
        VALUES(?,?,?,?,?,?,?)''', (context_id,camera_id,session_id,version,zone,now.isoformat(),policy))
    return CaptureContext(context_id,camera_id,session_id,version,zone,now)


def retire(conn, *, context_id, camera_id, session_id, server_now, trusted_stop_acknowledged, policy):
    _gate(policy)
    if trusted_stop_acknowledged is not True:
        raise ValueError('verified_stop_acknowledgement_required')
    row = conn.execute('SELECT * FROM capture_context_registry WHERE context_id=? AND camera_id=? AND session_id=?',
                       (context_id,camera_id,session_id)).fetchone()
    if row is None:
        raise ValueError('context_binding_mismatch')
    if row['valid_until'] is not None:
        return _context(row)  # retries never move the original effective boundary
    now = utc(server_now)
    if now <= utc(datetime.fromisoformat(row['valid_from'])):
        raise ValueError('retirement_must_follow_issuance')
    conn.execute('UPDATE capture_context_registry SET valid_until=? WHERE context_id=?', (now.isoformat(),context_id))
    return _context(conn.execute('SELECT * FROM capture_context_registry WHERE context_id=?', (context_id,)).fetchone())


def resolve_registered_context(conn, **evidence):
    # Zone/version is always loaded from registry, never trusted from payload.
    row = conn.execute('SELECT * FROM capture_context_registry WHERE context_id=?',
                       (evidence.get('context_id'),)).fetchone()
    return resolve_capture_context(context=_context(row) if row else None, **evidence)
