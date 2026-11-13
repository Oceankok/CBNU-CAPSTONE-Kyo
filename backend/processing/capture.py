"""Camera-specific cumulative capture reports; no network collector or classifier."""
import json
import math
import os
import time
from datetime import timedelta
from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from backend.processing import lifecycle as lc


class Range(BaseModel):
    model_config=ConfigDict(extra='forbid')
    start_at: AwareDatetime
    end_at: AwareDatetime

    @model_validator(mode='after')
    def ordered(self):
        if self.end_at<=self.start_at:
            raise ValueError('capture_range_empty')
        return self


class Missing(Range):
    reason: str=Field(min_length=1,max_length=100)


class Report(BaseModel):
    model_config=ConfigDict(extra='forbid')
    session_id: str=Field(pattern=r'^[0-9a-f]{32}$')
    sequence: int=Field(ge=1,le=2147483647)
    state: Literal['collecting','complete','incomplete']
    captured_ranges: list[Range]=Field(max_length=10000)
    missing_ranges: list[Missing]=Field(max_length=10000)
    earliest_available_at: AwareDatetime | None=None
    time_basis: Literal['source_utc_corrected','shared_monotonic']
    clock_offset_seconds: float=Field(allow_inf_nan=False)
    clock_uncertainty_seconds: float=Field(ge=0,allow_inf_nan=False)
    reason: str | None=Field(default=None,min_length=1,max_length=100)

    @model_validator(mode='after')
    def ranges(self):
        if any(a.end_at>b.start_at for a,b in zip(self.captured_ranges,self.captured_ranges[1:])):
            raise ValueError('capture_ranges_must_be_sorted_disjoint')
        if self.state=='incomplete' and not self.reason and not self.missing_ranges:
            raise ValueError('incomplete_reason_required')
        return self


def cameras(conn,event_id):
    event=conn.execute('SELECT timestamp_start,timestamp_end FROM candidate_event WHERE event_id=?',(event_id,)).fetchone()
    rows=[]
    for camera in conn.execute('SELECT camera_id,role FROM processing_event_camera WHERE event_id=? ORDER BY camera_id',(event_id,)):
        report=conn.execute('SELECT payload FROM processing_camera_capture WHERE event_id=? AND camera_id=? ORDER BY sequence DESC LIMIT 1',(event_id,camera['camera_id'])).fetchone()
        payload=json.loads(report['payload']) if report else None
        if payload: payload.pop('session_id',None)
        rows.append({**dict(camera),'required_start_at':lc.stamp(lc.dt(event['timestamp_start'])-timedelta(seconds=2)),
            'required_end_at':lc.stamp(lc.dt(event['timestamp_end'])+timedelta(seconds=2)),
            'capture_report':payload})
    return rows


def _covers(ranges,start,end):
    cursor=start
    for r in ranges:
        if r.start_at>cursor: return False
        cursor=max(cursor,r.end_at)
        if cursor>=end:return True
    return False


def report(event_id,camera_id,body,node_id):
    lc.enabled();raw=lc.canonical(body)
    with lc.get_connection() as conn:
        lc.begin_write(conn)
        owner=lc._owner(conn,event_id,node_id,body.session_id)
        if not conn.execute('SELECT 1 FROM processing_event_camera WHERE event_id=? AND camera_id=?',(event_id,camera_id)).fetchone():
            raise HTTPException(404,'continuous_camera_not_found')
        prior=conn.execute('SELECT payload FROM processing_camera_capture WHERE event_id=? AND camera_id=? AND sequence=?',(event_id,camera_id,body.sequence)).fetchone()
        if prior:
            if prior['payload']!=raw:raise HTTPException(409,'capture_sequence_conflict')
            return lc.public(conn,event_id)
        lc._mutable(conn,event_id)
        last=conn.execute('SELECT payload,sequence FROM processing_camera_capture WHERE event_id=? AND camera_id=? ORDER BY sequence DESC LIMIT 1',(event_id,camera_id)).fetchone()
        if body.sequence!=(last['sequence']+1 if last else 1):
            raise HTTPException(409,'capture_sequence_conflict')
        if last:
            old=Report.model_validate_json(last['payload'])
            # Preserve reported evidence and missing intervals. Recovery needs a separate policy.
            if any(not _covers(body.captured_ranges,r.start_at,r.end_at) for r in old.captured_ranges):
                raise HTTPException(409,'capture_coverage_regressed')
            if any(r.model_dump(mode='json') not in [x.model_dump(mode='json') for x in body.missing_ranges] for r in old.missing_ranges):
                raise HTTPException(409,'capture_missing_history_required')
            if old.state=='incomplete' and body.state!='incomplete':
                raise HTTPException(409,'capture_recovery_policy_required')
        event=conn.execute('SELECT timestamp_start,timestamp_end FROM candidate_event WHERE event_id=?',(event_id,)).fetchone()
        start=lc.dt(event['timestamp_start'])-timedelta(seconds=2)
        end=lc.dt(event['timestamp_end'])+timedelta(seconds=2)
        if any(r.start_at<start or r.end_at>end for r in [*body.captured_ranges,*body.missing_ranges]):
            raise HTTPException(422,'capture_range_outside_event')
        if body.state=='complete':
            try:
                maximum=float(os.environ['PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS'])
                if not math.isfinite(maximum) or maximum<0:raise ValueError()
            except (KeyError,ValueError):
                raise HTTPException(503,'continuous_clock_policy_required')
            if body.clock_uncertainty_seconds>maximum:
                raise HTTPException(422,'capture_clock_uncertain')
            if (owner['phase']!='closed' or body.missing_ranges or not _covers(body.captured_ranges,start,end)
                    or (body.earliest_available_at is not None and body.earliest_available_at>start)):
                raise HTTPException(409,'continuous_capture_incomplete')
        conn.execute('INSERT INTO processing_camera_capture(event_id,camera_id,sequence,payload,created_at) VALUES(?,?,?,?,?)',(event_id,camera_id,body.sequence,raw,time.time()))
        conn.execute('UPDATE processing_event_lifecycle SET evidence_revision=evidence_revision+1 WHERE event_id=?',(event_id,))
        return lc.public(conn,event_id)


def discovery(node_id,after='',limit=50):
    """Open plus closed/interrupted evidence-pending sessions, paged by stable event ID."""
    lc.enabled()
    with lc.get_connection() as conn:
        rows=conn.execute('''SELECT l.event_id,l.session_id FROM processing_event_lifecycle l
            JOIN event_retention t ON t.event_id=l.event_id
            WHERE l.owner_node_id=? AND l.event_id>? AND t.decision!='delete'
            AND (t.decision!='retain' OR t.retain_until IS NULL OR t.retain_until>?)
            AND NOT EXISTS(SELECT 1 FROM event_review r WHERE r.event_id=l.event_id)
            AND NOT EXISTS(SELECT 1 FROM event_merge m WHERE m.source_event_id=l.event_id OR m.target_event_id=l.event_id)
            ORDER BY l.event_id LIMIT ?''',(node_id,after,time.time(),limit+1)).fetchall()
        items=[{**lc.public(conn,r['event_id']),'session_id':r['session_id']} for r in rows[:limit]]
        return {'items':items,'next_after':rows[limit-1]['event_id'] if len(rows)>limit else None}
