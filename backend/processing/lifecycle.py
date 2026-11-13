"""Opt-in continuous-event metadata. No automatic classifier is enabled here.

Legacy immutable submissions remain unchanged. Producers must durably journal
start/update/segment operations before sending them; process memory is not an outbox.
"""
from datetime import timezone, timedelta
import json
import os
import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from typing import Literal

from backend.db.connection import begin_write
from backend.db.event_repository import get_connection, insert_candidate_event
from backend.event_time import parse
from backend.processing.events import EventSubmission
from backend.field.service import enqueue_event_broadcast


def enabled():
    if os.environ.get('PPE_CONTINUOUS_EVENTS_ENABLED', '').lower() != 'true':
        raise HTTPException(503, 'continuous_events_disabled')


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def dt(value):
    parsed = parse(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def canonical(body):
    payload=body.model_dump(mode='json')
    # Preserve the pre-context serialized form for legacy start retries.
    if payload.get('capture_context_id') is None:payload.pop('capture_context_id',None)
    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


class Observation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    observed_seconds: float = Field(ge=0, le=3600, allow_inf_nan=False)
    gap_seconds: float = Field(ge=0, le=3600, allow_inf_nan=False)
    gap_count: int = Field(ge=0, le=100000)


class Start(EventSubmission, Observation):
    session_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    policy_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def observation_span(self):
        span = (self.timestamp_end-self.timestamp_start).total_seconds()
        if span < 3 or self.observed_seconds < 3 or self.observed_seconds+self.gap_seconds > span+1e-6:
            raise ValueError('invalid_continuous_observation')
        if not self.tracking_id:
            raise ValueError('tracking_id_required')
        return self


class Update(Observation):
    session_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    sequence: int = Field(ge=1, le=2147483647)
    kind: Literal['progress','close']
    timestamp_end: AwareDatetime
    frame_sample_count: int = Field(ge=1, le=100000)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    close_reason: str | None = Field(default=None, min_length=1, max_length=100)
    complete: bool | None = None
    final_segment_count: int | None = Field(default=None, ge=0, le=10000)

    @model_validator(mode='after')
    def close_fields(self):
        if self.kind == 'close':
            if self.close_reason is None or self.complete is None or self.final_segment_count is None:
                raise ValueError('close_manifest_required')
        elif any(x is not None for x in (self.close_reason,self.complete,self.final_segment_count)):
            raise ValueError('progress_close_fields_forbidden')
        return self


class Segment(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    session_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    sequence: int = Field(ge=0, le=9999)
    capture_start_at: AwareDatetime
    capture_end_at: AwareDatetime

    @model_validator(mode='after')
    def length(self):
        if not 0 < (self.capture_end_at-self.capture_start_at).total_seconds() <= 60:
            raise ValueError('segment_must_be_at_most_60_seconds')
        return self


def _owner(conn, event_id, node_id, session_id):
    row = conn.execute('SELECT * FROM processing_event_lifecycle WHERE event_id=?', (event_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'continuous_event_not_found')
    if row['owner_node_id'] != node_id or row['session_id'] != session_id:
        raise HTTPException(403, 'continuous_event_owner_required')
    return row


def _mutable(conn, event_id):
    if conn.execute('SELECT 1 FROM event_merge WHERE source_event_id=? OR target_event_id=?', (event_id,event_id)).fetchone():
        raise HTTPException(409, 'event_merged')
    retention = conn.execute('SELECT decision,retain_until FROM event_retention WHERE event_id=?', (event_id,)).fetchone()
    if retention and (retention['decision']=='delete' or
                      (retention['decision']=='retain' and retention['retain_until'] is not None and retention['retain_until']<=time.time())):
        raise HTTPException(409, 'event_media_deleted_or_expired')
    if conn.execute('SELECT 1 FROM event_review WHERE event_id=?', (event_id,)).fetchone():
        raise HTTPException(409, 'reviewed_event_is_immutable')


def public(conn, event_id):
    row = dict(conn.execute('SELECT * FROM processing_event_lifecycle WHERE event_id=?', (event_id,)).fetchone())
    for key in ('start_payload','owner_node_id','session_id'):
        row.pop(key)
    segments = [dict(r) for r in conn.execute('''SELECT s.*,r.status,r.error_code,r.role
        FROM processing_event_segment s JOIN event_clip_request r ON r.request_id=s.request_id
        WHERE s.event_id=? ORDER BY s.sequence,s.camera_id''', (event_id,))]
    from backend.processing.capture import cameras
    return {'event_id': event_id, 'lifecycle': row, 'segments': segments, 'cameras': cameras(conn,event_id), 'auto_decision_enabled': False}


def read(event_id, node_id, session_id):
    enabled()
    with get_connection() as conn:
        _owner(conn,event_id,node_id,session_id)
        return public(conn,event_id)


def start(body, node_id):
    enabled()
    raw = canonical(body)
    with get_connection() as conn:
        begin_write(conn)
        from backend.capture_context.service import check_event,bind_event
        capture_context=check_event(conn,body)
        previous = conn.execute('SELECT * FROM processing_event_lifecycle WHERE event_id=?',(body.event_id,)).fetchone()
        created = previous is None
        if previous:
            _owner(conn,body.event_id,node_id,body.session_id)
            if previous['start_payload'] != raw:
                raise HTTPException(409,'continuous_start_conflict')
        else:
            if conn.execute('SELECT 1 FROM candidate_event WHERE event_id=?',(body.event_id,)).fetchone():
                raise HTTPException(409,'legacy_event_cannot_be_claimed')
            event = dict(event_id=body.event_id,camera_id=body.camera_id,tracking_id=body.tracking_id,
                ppe_type=body.ppe_type,timestamp_start=stamp(body.timestamp_start),timestamp_end=stamp(body.timestamp_end),
                duration_sec=int((body.timestamp_end-body.timestamp_start).total_seconds()),
                frame_sample_count=body.frame_sample_count,ai_confidence=0.0 if body.review_only else body.confidence,
                person_detected=1,ppe_detected=0,model_version=body.model_version,thumbnail_path='',video_clip_path='',event_status='pending')
            insert_candidate_event(event,connection=conn)
            bind_event(conn,body,capture_context)
            conn.execute('INSERT INTO processing_review_only_event(event_id,review_only) VALUES(?,?)',(body.event_id,int(body.review_only)))
            conn.execute('''INSERT INTO processing_event_lifecycle(event_id,owner_node_id,session_id,policy_key,phase,start_payload,
                observed_seconds,gap_seconds,gap_count,updated_at) VALUES(?,?,?,?,'open',?,?,?,?,?)''',
                (body.event_id,node_id,body.session_id,body.policy_key,raw,body.observed_seconds,body.gap_seconds,body.gap_count,time.time()))
            conn.execute("INSERT INTO processing_event_camera(event_id,camera_id,role) VALUES(?,?,'clip')",(body.event_id,body.camera_id))
            refs=conn.execute('SELECT camera_id,role FROM event_clip_request WHERE event_id=?',(body.event_id,)).fetchall()
            for ref in refs:
                conn.execute('INSERT INTO processing_event_camera(event_id,camera_id,role) VALUES(?,?,?)',(body.event_id,ref['camera_id'],ref['role']))
            # Replace the legacy fixed-window requests atomically; no raw evidence exists yet.
            conn.execute('DELETE FROM event_clip_request WHERE event_id=?',(body.event_id,))
        result=public(conn,body.event_id)
    # Retry uses existing preventive-warning deduplication; no classifier runs here.
    result['broadcast'] = (enqueue_event_broadcast(body.event_id) if result['lifecycle']['phase']=='open'
                           else {'queued':False,'reason':'continuous_event_closed'})
    return {'created':created,**result}


def update(event_id, body, node_id):
    enabled()
    raw=canonical(body)
    with get_connection() as conn:
        begin_write(conn)
        row=_owner(conn,event_id,node_id,body.session_id)
        prior=conn.execute('SELECT payload FROM processing_event_operation WHERE event_id=? AND sequence=?',(event_id,body.sequence)).fetchone()
        if prior:
            if prior['payload']!=raw:
                raise HTTPException(409,'continuous_sequence_conflict')
            return public(conn,event_id)
        _mutable(conn,event_id)
        if row['phase']!='open' or body.sequence!=row['revision']+1:
            raise HTTPException(409,'continuous_revision_conflict')
        event=conn.execute('SELECT * FROM candidate_event WHERE event_id=?',(event_id,)).fetchone()
        from types import SimpleNamespace
        from backend.capture_context.service import check_event
        context=conn.execute('SELECT context_id FROM event_capture_context WHERE event_id=?',(event_id,)).fetchone()
        if context:
            check_event(conn,SimpleNamespace(capture_context_id=context[0],camera_id=event['camera_id'],timestamp_start=dt(event['timestamp_start']),timestamp_end=body.timestamp_end))
        review_only = bool(conn.execute('SELECT review_only FROM processing_review_only_event WHERE event_id=?',(event_id,)).fetchone()[0])
        if review_only:
            if body.confidence is not None:
                raise HTTPException(422,'review_only_confidence_forbidden')
            confidence=0.0
        else:
            if body.confidence is None or body.confidence>event['ai_confidence']:
                raise HTTPException(422,'cumulative_minimum_confidence_required')
            confidence=body.confidence
        span=(body.timestamp_end-dt(event['timestamp_start'])).total_seconds()
        if not 0<=span<=3600 or body.timestamp_end<dt(event['timestamp_end']) or body.frame_sample_count<event['frame_sample_count']:
            raise HTTPException(422,'invalid_continuous_progress')
        if body.observed_seconds+body.gap_seconds>span+1e-6 or any(getattr(body,k)<row[k] for k in ('observed_seconds','gap_seconds','gap_count')):
            raise HTTPException(422,'invalid_continuous_observation')
        phase='open'
        if body.kind=='close':
            count=conn.execute("SELECT COUNT(*) FROM processing_event_segment s JOIN processing_event_camera c ON c.event_id=s.event_id AND c.camera_id=s.camera_id WHERE s.event_id=? AND c.role='clip'",(event_id,)).fetchone()[0]
            if body.final_segment_count!=count:
                raise HTTPException(409,'segment_manifest_incomplete')
            if body.complete:
                bounds=conn.execute('SELECT MIN(capture_start_at) AS first,MAX(capture_end_at) AS last FROM processing_event_segment WHERE event_id=?',(event_id,)).fetchone()
                if count==0 or dt(bounds['first'])>dt(event['timestamp_start'])-timedelta(seconds=2) or dt(bounds['last'])<body.timestamp_end+timedelta(seconds=2):
                    raise HTTPException(409,'segment_coverage_incomplete')
            phase='closed' if body.complete else 'interrupted'
        conn.execute('''UPDATE candidate_event SET timestamp_end=?,duration_sec=?,frame_sample_count=?,ai_confidence=? WHERE event_id=?''',
                     (stamp(body.timestamp_end),int(span),body.frame_sample_count,confidence,event_id))
        conn.execute('''UPDATE processing_event_lifecycle SET phase=?,revision=?,observed_seconds=?,gap_seconds=?,gap_count=?,
                     final_segment_count=?,close_reason=?,updated_at=? WHERE event_id=?''',
                     (phase,body.sequence,body.observed_seconds,body.gap_seconds,body.gap_count,body.final_segment_count,body.close_reason,time.time(),event_id))
        conn.execute('INSERT INTO processing_event_operation(event_id,sequence,payload) VALUES(?,?,?)',(event_id,body.sequence,raw))
        if phase == 'closed':
            from backend.auto_decision.writer import on_event_ready
            on_event_ready(conn,event_id)  # Dormant until a trusted validator is integrated.
        return public(conn,event_id)


def segment(event_id, body, node_id):
    enabled()
    with get_connection() as conn:
        begin_write(conn)
        row=_owner(conn,event_id,node_id,body.session_id)
        existing=conn.execute('SELECT * FROM processing_event_segment WHERE event_id=? AND sequence=?',(event_id,body.sequence)).fetchall()
        start_at,end_at=stamp(body.capture_start_at),stamp(body.capture_end_at)
        if existing:
            if any(r['capture_start_at']!=start_at or r['capture_end_at']!=end_at for r in existing):
                raise HTTPException(409,'segment_content_conflict')
            return public(conn,event_id)
        _mutable(conn,event_id)
        if row['phase']!='open':
            raise HTTPException(409,'continuous_event_closed')
        event=conn.execute('SELECT timestamp_start,timestamp_end FROM candidate_event WHERE event_id=?',(event_id,)).fetchone()
        from datetime import timedelta
        if body.capture_start_at<dt(event['timestamp_start'])-timedelta(seconds=2) or body.capture_end_at>dt(event['timestamp_end'])+timedelta(seconds=2):
            raise HTTPException(422,'segment_outside_observation')
        previous=conn.execute('SELECT sequence,capture_end_at FROM processing_event_segment WHERE event_id=? ORDER BY sequence DESC LIMIT 1',(event_id,)).fetchone()
        if (previous is None and body.sequence!=0) or (previous is not None and (body.sequence!=previous['sequence']+1 or body.capture_start_at!=dt(previous['capture_end_at']))):
            raise HTTPException(409,'segment_sequence_or_boundary_conflict')
        # This configurable delivery deadline is separate from the capture interval.
        try:
            delivery=float(os.environ.get('PPE_CONTINUOUS_CLIP_DELIVERY_SECONDS','300'))
            if not 30<=delivery<=3600:
                raise ValueError()
        except ValueError:
            raise HTTPException(503,'invalid_continuous_delivery_timeout')
        now=time.time()
        for camera in conn.execute('SELECT camera_id,role FROM processing_event_camera WHERE event_id=?',(event_id,)).fetchall():
            request_id=str(uuid4())
            conn.execute('''INSERT INTO event_clip_request(request_id,event_id,camera_id,role,created_at,updated_at) VALUES(?,?,?,?,?,?)''',
                         (request_id,event_id,camera['camera_id'],camera['role'],now,now))
            conn.execute('''INSERT INTO processing_event_segment(event_id,camera_id,sequence,request_id,capture_start_at,capture_end_at,deadline_at)
                         VALUES(?,?,?,?,?,?,?)''',(event_id,camera['camera_id'],body.sequence,request_id,start_at,end_at,now+delivery))
        conn.execute('UPDATE processing_event_lifecycle SET evidence_revision=evidence_revision+1,updated_at=? WHERE event_id=?',(now,event_id))
        return public(conn,event_id)


def require_finalizable(conn,event_id):
    """Fail closed for new continuous events only; partial-evidence policy is unresolved."""
    row=conn.execute('SELECT phase FROM processing_event_lifecycle WHERE event_id=?',(event_id,)).fetchone()
    if row is None:
        return
    if row['phase']!='closed':
        raise ValueError('continuous_event_not_closed')
    retention=conn.execute('SELECT decision,retain_until FROM event_retention WHERE event_id=?',(event_id,)).fetchone()
    if retention and (retention['decision']=='delete' or
                      (retention['decision']=='retain' and retention['retain_until'] is not None and retention['retain_until']<=time.time())):
        raise ValueError('event_media_deleted_or_expired')
    parts=conn.execute("""SELECT s.request_id,s.capture_start_at AS segment_start,s.capture_end_at AS segment_end,
        r.status AS delivery_status,m.* FROM processing_event_segment s
        JOIN event_clip_request r ON r.request_id=s.request_id
        LEFT JOIN event_media m ON m.request_id=s.request_id
        WHERE s.event_id=?""",(event_id,)).fetchall()
    if not parts:
        raise ValueError('continuous_evidence_incomplete')
    from backend.processing.capture import cameras
    if any(not camera['capture_report'] or camera['capture_report']['state']!='complete' for camera in cameras(conn,event_id)):
        raise ValueError('continuous_evidence_incomplete')
    from backend.media.paths import owned_path
    for part in parts:
        valid = (part['delivery_status']=='received' and part['media_id'] is not None
                 and part['status']=='ready' and part['redaction_status']=='complete'
                 and part['kind']=='video' and part['capture_start_at']==part['segment_start']
                 and part['capture_end_at']==part['segment_end'])
        proof=conn.execute('SELECT generation FROM processing_segment_validation WHERE media_id=?',(part['media_id'],)).fetchone()
        valid = valid and proof is not None and proof['generation']==part['processing_generation']
        try:
            valid = valid and owned_path(part['storage_path']).is_file()
        except (ValueError,OSError,TypeError):
            valid=False
        if not valid:
            raise ValueError('continuous_evidence_incomplete')


def reference_requests(node_id):
    """Only upgraded collectors call this endpoint; legacy max-20s collectors never see these."""
    enabled()
    with get_connection() as conn:
        rows=conn.execute("""SELECT s.*,r.role FROM processing_event_segment s
            JOIN processing_event_lifecycle l ON l.event_id=s.event_id
            JOIN event_clip_request r ON r.request_id=s.request_id
            JOIN event_retention t ON t.event_id=s.event_id
            WHERE l.owner_node_id=? AND r.role='reference' AND r.status='pending'
            AND t.decision!='delete' AND (t.decision!='retain' OR t.retain_until IS NULL OR t.retain_until>?)
            AND s.deadline_at>? AND NOT EXISTS(SELECT 1 FROM event_merge x WHERE x.source_event_id=s.event_id)
            AND NOT EXISTS(SELECT 1 FROM event_media m WHERE m.request_id=s.request_id)
            ORDER BY s.deadline_at,s.event_id,s.sequence,s.camera_id LIMIT 20""",(node_id,time.time(),time.time())).fetchall()
        return {'items':[{'request_id':r['request_id'],'event_id':r['event_id'],'camera_id':r['camera_id'],
                         'role':r['role'],'segment_sequence':r['sequence'],'clip_start_at':r['capture_start_at'],
                         'clip_end_at':r['capture_end_at'],'deadline_at':r['deadline_at']} for r in rows]}


def require_segment_upload(conn,event_id,request_id):
    """Check again inside registration's write transaction after a potentially slow body."""
    row=conn.execute('SELECT 1 FROM processing_event_lifecycle WHERE event_id=?',(event_id,)).fetchone()
    if not row:
        return
    retention=conn.execute('SELECT decision,retain_until FROM event_retention WHERE event_id=?',(event_id,)).fetchone()
    if retention and (retention['decision']=='delete' or
                      (retention['decision']=='retain' and retention['retain_until'] is not None and retention['retain_until']<=time.time())):
        raise ValueError('event_media_deleted_or_expired')
    segment=conn.execute('SELECT deadline_at FROM processing_event_segment WHERE event_id=? AND request_id=?',(event_id,request_id)).fetchone()
    if segment is None:
        raise ValueError('continuous_segment_request_required')
    if segment['deadline_at']<=time.time():
        raise ValueError('continuous_segment_delivery_expired')


def validate_segment_measurement(segment,measurement):
    """Actual decoded output duration, not producer metadata, must cover the segment."""
    import math
    if not measurement:
        raise HTTPException(422,'continuous_segment_measurement_required')
    fps=measurement['fps']; duration=measurement['duration_seconds']
    expected=(dt(segment['capture_end_at'])-dt(segment['capture_start_at'])).total_seconds()
    if not math.isfinite(fps) or fps<=0 or not math.isfinite(duration) or duration<=0 or duration>60+1e-6:
        raise HTTPException(422,'continuous_segment_duration_invalid')
    # Legacy clip generation uses 15 FPS. A low reported FPS must never
    # expand evidence coverage tolerance beyond one legacy output frame.
    if abs(duration-expected)>min(1/fps,1/15)+1e-6:
        raise HTTPException(422,'continuous_segment_duration_mismatch')
