"""Authenticated capture-session protocol; disabled by default.

Stop is an authenticated sender acknowledgement plus exact accepted-sequence
manifest, not proof of physical camera hardware state. Timeout never retires it.
"""
import os
from datetime import datetime, timezone
from fastapi import HTTPException
from .registry import POLICY, issue, retire
from .validation import utc


def enabled(): return os.environ.get('PPE_CAPTURE_CONTEXT_ENABLED','').lower() == 'true'


def require_enabled():
    if not enabled(): raise HTTPException(503,'capture_context_disabled')


def begin(conn):
    from backend.db.connection import begin_write
    if getattr(conn,'dialect','sqlite') == 'postgresql':
        conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
    begin_write(conn)


def public(c):
    return dict(context_id=c.context_id,capture_context_id=c.context_id,camera_id=c.camera_id,
                session_id=c.session_id,capture_session_id=c.session_id,assignment_version=c.assignment_version,
                zone_name=c.zone_name,valid_from=c.valid_from.isoformat(),
                valid_until=c.valid_until.isoformat() if c.valid_until else None,
                attribution_basis='administrative_capture_session')


def start(camera_id, session_id, node_id):
    require_enabled()
    from backend.db.event_repository import get_connection
    from backend.video.service import source_allowed
    with get_connection() as conn:
        begin(conn);source_allowed(conn,camera_id,node_id)
        # Legacy streams cannot establish a verified stop. Require explicit close
        # by their authenticated owner through the compatibility stop endpoint.
        if conn.execute('''SELECT 1 FROM video_stream s WHERE s.camera_id=? AND NOT EXISTS
            (SELECT 1 FROM capture_context_stream m WHERE m.stream_id=s.stream_id) AND s.status!='closed' ''', (camera_id,)).fetchone():
            raise HTTPException(409,'legacy_capture_stop_required')
        existing=conn.execute('''SELECT o.node_id FROM capture_context_owner o JOIN capture_context_registry c
            ON c.context_id=o.context_id WHERE c.camera_id=? AND c.session_id=?''',(camera_id,session_id)).fetchone()
        if existing and existing['node_id'] != node_id:raise HTTPException(403,'capture_context_owner_required')
        try:c=issue(conn,camera_id=camera_id,session_id=session_id,server_now=datetime.now(timezone.utc),policy=POLICY)
        except ValueError as e:raise HTTPException(409,str(e))
        conn.execute('INSERT INTO capture_context_owner(context_id,node_id) VALUES(?,?) ON CONFLICT(context_id) DO NOTHING',(c.context_id,node_id))
        return public(c)


def owned(conn, context_id, node_id=None, camera_id=None, active=False):
    row=conn.execute('''SELECT c.*,o.node_id FROM capture_context_registry c JOIN capture_context_owner o
        ON o.context_id=c.context_id WHERE c.context_id=?''',(context_id,)).fetchone()
    if row is None:raise HTTPException(404,'capture_context_not_found')
    if (node_id is not None and row['node_id']!=node_id) or (camera_id is not None and row['camera_id']!=camera_id):
        raise HTTPException(403,'capture_context_binding_mismatch')
    if active and row['valid_until'] is not None:raise HTTPException(409,'capture_context_retired')
    return row


def stream_fields(conn,stream_id):
    row=conn.execute('''SELECT c.context_id,c.session_id,c.assignment_version FROM capture_context_registry c
        JOIN capture_context_stream m ON m.context_id=c.context_id WHERE m.stream_id=?''',(stream_id,)).fetchone()
    return {} if row is None else dict(capture_context_id=row['context_id'],capture_session_id=row['session_id'],assignment_version=row['assignment_version'])


def assert_reassignment_allowed(conn,camera_id):
    # A persisted active context blocks movement even if feature flag later turns off.
    if conn.execute('SELECT 1 FROM capture_context_registry WHERE camera_id=? AND valid_until IS NULL',(camera_id,)).fetchone():
        raise HTTPException(409,'capture_stop_acknowledgement_required')
    if enabled() and conn.execute("SELECT 1 FROM video_stream WHERE camera_id=? AND status!='closed'",(camera_id,)).fetchone():
        raise HTTPException(409,'legacy_capture_stop_required')


def stop(context_id,node_id,final_sequences):
    require_enabled()
    from backend.db.event_repository import get_connection
    with get_connection() as conn:
        begin(conn);c=owned(conn,context_id,node_id)
        rows=conn.execute('''SELECT s.stream_id,s.last_sequence,s.sequence_gap_count FROM video_stream s
            JOIN capture_context_stream m ON m.stream_id=s.stream_id WHERE m.context_id=?''',(context_id,)).fetchall()
        expected={r['stream_id']:r['last_sequence'] for r in rows}
        if expected!=final_sequences or any(r['sequence_gap_count'] for r in rows):
            raise HTTPException(409,'capture_final_sequence_manifest_mismatch_or_gap')
        try:result=retire(conn,context_id=context_id,camera_id=c['camera_id'],session_id=c['session_id'],
            server_now=datetime.now(timezone.utc),trusted_stop_acknowledged=True,policy=POLICY)
        except ValueError as e:raise HTTPException(409,str(e))
        for ident in expected:conn.execute("UPDATE video_stream SET status='closed' WHERE stream_id=?",(ident,))
        return public(result)|{'retired':True}


def check_event(conn,body):
    context_id=getattr(body,'capture_context_id',None)
    if context_id is None:
        if enabled() or conn.execute('SELECT 1 FROM capture_context_registry WHERE camera_id=?',(body.camera_id,)).fetchone():raise HTTPException(422,'capture_context_required')
        return None
    require_enabled();c=owned(conn,context_id,camera_id=body.camera_id)
    start,end=utc(body.timestamp_start),utc(body.timestamp_end)
    if start < datetime.fromisoformat(c['valid_from']) or end > datetime.now(timezone.utc):
        raise HTTPException(422,'capture_interval_outside_context_or_future')
    if c['valid_until'] and end >= datetime.fromisoformat(c['valid_until']):
        raise HTTPException(422,'capture_interval_crosses_stop')
    return c


def bind_event(conn,body,c):
    if c is None:return
    old=conn.execute('SELECT context_id FROM event_capture_context WHERE event_id=?',(body.event_id,)).fetchone()
    if old and old['context_id']!=c['context_id']:raise HTTPException(409,'event_capture_context_conflict')
    conn.execute('INSERT INTO event_capture_context(event_id,context_id) VALUES(?,?) ON CONFLICT(event_id) DO NOTHING',(body.event_id,c['context_id']))
    conn.execute("UPDATE event_zone_snapshot SET zone_name=?,source='capture_context' WHERE event_id=?",(c['zone_name'],body.event_id))
