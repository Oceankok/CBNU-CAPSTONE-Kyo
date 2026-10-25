"""HTTP metadata boundary for the inference process; clips use the existing binary API."""
from datetime import timezone
import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from backend.cameras.service import require_camera
from backend.db.connection import begin_write
from backend.db.event_repository import get_connection, insert_candidate_event
from backend.field.service import enqueue_event_broadcast
from backend.zones.policy import ALLOWED_PPE


class EventSubmission(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    event_id: str = Field(pattern=r'^EVT_[0-9a-f]{32}$')
    camera_id: str = Field(min_length=1, max_length=100)
    tracking_id: str | None = Field(default=None, min_length=1, max_length=200)
    ppe_type: str
    timestamp_start: AwareDatetime
    timestamp_end: AwareDatetime
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    frame_sample_count: int = Field(ge=1, le=100000)
    model_version: str = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def validate_observation(self):
        seconds = (self.timestamp_end-self.timestamp_start).total_seconds()
        if self.ppe_type not in ALLOWED_PPE or not 0 <= seconds <= 3600:
            raise ValueError('Invalid PPE code or observation interval')
        return self


def submit(body):
    event = {'event_id': body.event_id, 'camera_id': body.camera_id, 'tracking_id': body.tracking_id,
             'ppe_type': body.ppe_type,
             'timestamp_start': body.timestamp_start.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'),
             'timestamp_end': body.timestamp_end.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'),
             'duration_sec': int((body.timestamp_end-body.timestamp_start).total_seconds()),
             'frame_sample_count': body.frame_sample_count, 'ai_confidence': body.confidence,
             'person_detected': 1, 'ppe_detected': 0, 'model_version': body.model_version,
             'thumbnail_path': '', 'video_clip_path': '', 'event_status': 'pending'}
    with get_connection() as conn:
        begin_write(conn)
        require_camera(conn, body.camera_id, active=True)
        previous = conn.execute('SELECT * FROM candidate_event WHERE event_id=?', (body.event_id,)).fetchone()
        created = previous is None
        if previous:
            for key in ('camera_id', 'tracking_id', 'ppe_type', 'timestamp_start', 'timestamp_end',
                        'frame_sample_count', 'ai_confidence', 'model_version'):
                if previous[key] != event[key]:
                    raise HTTPException(409, 'event_submission_conflict')
        else:
            insert_candidate_event(event, connection=conn)
            stamp = time.time()
            conn.execute('''INSERT INTO event_clip_request(request_id,event_id,camera_id,role,created_at,updated_at)
                VALUES(?,?,?,'clip',?,?)''', (str(uuid4()), body.event_id, body.camera_id, stamp, stamp))
    # Storage is committed first: retries recover a broadcast failure without duplicating the event.
    broadcast = ({'queued': False, 'reason': 'event_no_longer_pending'}
                 if previous and previous['event_status'] != 'pending' else enqueue_event_broadcast(body.event_id))
    with get_connection() as conn:
        requests = [dict(row) for row in conn.execute('SELECT * FROM event_clip_request WHERE event_id=? ORDER BY created_at,camera_id', (body.event_id,))]
        merged = conn.execute('SELECT target_event_id FROM event_merge WHERE source_event_id=?', (body.event_id,)).fetchone()
    return {'event_id': body.event_id, 'created': created, 'clip_requests': requests,
            'merged_into_event_id': merged[0] if merged else None, 'broadcast': broadcast}
