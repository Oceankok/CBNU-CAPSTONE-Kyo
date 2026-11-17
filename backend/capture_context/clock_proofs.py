"""Opt-in authenticated source clock evidence, never independent hardware proof."""
import json
import os
import threading
import time
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from backend.field.routes import current_node
from backend.processing.routes import processor
from backend.db.event_repository import get_connection
from backend.capture_context import service
from clock_contract import ClockPolicy, VERSION, sample_interval, covers

router = APIRouter(tags=['capture clock proof'])
_guard_lock = threading.Lock()
_guards = {}
SERVER_BOOT_ID = str(uuid4())  # v1 requires one API clock coordinator process.
MAX_STREAM_SAMPLES = 100000


def configuration():
    if os.environ.get('PPE_CLOCK_PROOF_ENABLED', '').lower() != 'true':
        raise HTTPException(503, 'clock_proof_disabled')
    try:
        policy = ClockPolicy(**{name: float(os.environ['PPE_CLOCK_'+name.upper()])
                               for name in ClockPolicy.__dataclass_fields__})
        epoch = os.environ['PPE_CLOCK_SERVER_EPOCH']
        maximum = float(os.environ['PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS'])
        if not epoch or len(epoch) > 100 or not policy.max_error_seconds <= maximum <= .25:
            raise ValueError()
    except (KeyError, ValueError): raise HTTPException(503, 'clock_proof_policy_required') from None
    return epoch, policy


def server_now(epoch, policy):
    """Sticky process-local step detection; deployment must rotate epoch on reset."""
    with _guard_lock:
        wall, mono = time.time(), time.monotonic()
        initial = _guards.setdefault(epoch, [wall, mono, False])
        elapsed = mono-initial[1]
        if elapsed < 0 or abs(wall-initial[0]-elapsed) > policy.step_tolerance_seconds + max(0, elapsed)*policy.drift_ppm/1e6:
            initial[2] = True
        if initial[2]: raise HTTPException(503, 'clock_server_step')
        initial[0],initial[1]=wall,mono
    return wall


def _stream(conn, stream_id, node_id=None):
    row = conn.execute('SELECT * FROM video_stream WHERE stream_id=?', (stream_id,)).fetchone()
    if row is None: raise HTTPException(404, 'video_stream_not_found')
    from backend.video.service import source_allowed
    source_allowed(conn, row['camera_id'], row['node_id'])
    extra = service.stream_fields(conn, stream_id)
    if not extra: raise HTTPException(409, 'capture_context_stream_required')
    context = service.owned(conn, extra['capture_context_id'], node_id, row['camera_id'], active=node_id is not None)
    return row, context


class Challenge(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: StrictInt = Field(ge=1, le=1)
    run_id: UUID
    epoch: UUID
    sample_sequence: StrictInt = Field(ge=1, le=2147483647)
    source_sent_at: float = Field(allow_inf_nan=False)
    monotonic_sent: float = Field(ge=0, allow_inf_nan=False)


class Publication(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: StrictInt = Field(ge=1, le=1)
    source_received_at: float = Field(allow_inf_nan=False)
    monotonic_received: float = Field(ge=0, allow_inf_nan=False)


def _challenge_response(row):
    return dict(version=VERSION,proof_id=row['proof_id'],server_time=row['server_sample'],
                server_epoch=row['server_epoch'],server_boot_id=row['server_boot_id'],policy=json.loads(row['policy']))


@router.post('/api/video/streams/{stream_id}/clock-challenges', status_code=201)
def challenge(stream_id: UUID, body: Challenge, node: dict = Depends(current_node)):
    epoch, policy = configuration(); now = server_now(epoch, policy); stream_id = str(stream_id)
    with get_connection() as conn:
        service.begin(conn); stream, context = _stream(conn, stream_id, node['node_id'])
        binding = conn.execute('SELECT * FROM capture_clock_stream WHERE stream_id=?', (stream_id,)).fetchone()
        if binding is None:
            if stream['last_sequence'] != -1: raise HTTPException(409, 'clock_binding_before_capture_required')
            conn.execute('INSERT INTO capture_clock_stream VALUES(?,?,?,?)', (stream_id, str(body.run_id), str(body.epoch), VERSION))
        elif binding['run_id'] != str(body.run_id) or binding['epoch'] != str(body.epoch):
            raise HTTPException(409, 'clock_epoch_changed')
        existing=conn.execute('SELECT * FROM capture_clock_sample WHERE stream_id=? AND sample_sequence=?',
                              (stream_id,body.sample_sequence)).fetchone()
        if existing:
            if existing['source_sent_at']!=body.source_sent_at or existing['monotonic_sent']!=body.monotonic_sent:
                raise HTTPException(409,'clock_sample_replayed')
            return _challenge_response(existing)  # Exact receipt; original nonce/time/expiry basis.
        last = conn.execute('SELECT MAX(sample_sequence) FROM capture_clock_sample WHERE stream_id=?', (stream_id,)).fetchone()[0]
        if body.sample_sequence != (last or 0)+1: raise HTTPException(409, 'clock_sample_replayed')
        # An active source may still own unuploaded chunks or an ambiguous
        # challenge response. Time alone proves neither obligation is settled.
        if conn.execute('SELECT COUNT(*) FROM capture_clock_sample WHERE stream_id=?',(stream_id,)).fetchone()[0]>=MAX_STREAM_SAMPLES:
            raise HTTPException(409,'clock_proof_capacity')
        ident = str(uuid4())
        conn.execute('INSERT INTO capture_clock_sample(proof_id,stream_id,sample_sequence,server_sample,server_epoch,server_boot_id,policy,source_sent_at,monotonic_sent) VALUES(?,?,?,?,?,?,?,?,?)',
                     (ident, stream_id, body.sample_sequence, now, epoch, SERVER_BOOT_ID,json.dumps(policy.public(),sort_keys=True),body.source_sent_at, body.monotonic_sent))
    return dict(version=VERSION, proof_id=ident, server_time=now, server_epoch=epoch, server_boot_id=SERVER_BOOT_ID, policy=policy.public())


@router.post('/api/video/streams/{stream_id}/clock-proofs/{proof_id}')
def publish(stream_id: UUID, proof_id: UUID, body: Publication, node: dict = Depends(current_node)):
    epoch, policy = configuration(); now = server_now(epoch, policy); stream_id, proof_id = str(stream_id), str(proof_id)
    raw = json.dumps(body.model_dump(), sort_keys=True, separators=(',', ':'), allow_nan=False)
    with get_connection() as conn:
        service.begin(conn); stream, context = _stream(conn, stream_id, node['node_id'])
        row = conn.execute('SELECT * FROM capture_clock_sample WHERE stream_id=? AND proof_id=?', (stream_id, proof_id)).fetchone()
        if row is None: raise HTTPException(404, 'clock_challenge_not_found')
        if row['proof'] is not None:
            if row['publication'] != raw: raise HTTPException(409, 'clock_proof_conflict')
            return json.loads(row['proof'])  # Exact retry cannot extend expiry.
        if (row['server_epoch'] != epoch or row['server_boot_id'] != SERVER_BOOT_ID or json.loads(row['policy'])!=policy.public()
                or not 0 <= now-row['server_sample'] <= policy.max_rtt_seconds):
            raise HTTPException(409, 'clock_challenge_expired')
        latest = conn.execute('SELECT MAX(sample_sequence) FROM capture_clock_sample WHERE stream_id=?', (stream_id,)).fetchone()[0]
        if row['sample_sequence'] != latest: raise HTTPException(409, 'clock_challenge_superseded')
        try:
            low, high = sample_interval(row['server_sample'], row['source_sent_at'], body.source_received_at,
                                        row['monotonic_sent'], body.monotonic_received, policy)
        except ValueError as error: raise HTTPException(422, str(error)) from None
        binding = conn.execute('SELECT * FROM capture_clock_stream WHERE stream_id=?', (stream_id,)).fetchone()
        proof = dict(version=VERSION, proof_id=proof_id, sample_sequence=row['sample_sequence'],
            source_node_id=node['node_id'], camera_id=stream['camera_id'], stream_id=stream_id,
            capture_context_id=context['context_id'], capture_session_id=context['session_id'],
            run_id=binding['run_id'], epoch=binding['epoch'], server_epoch=epoch, server_boot_id=SERVER_BOOT_ID,
            server_sample=row['server_sample'], source_sent_at=row['source_sent_at'],
            source_received_at=body.source_received_at, monotonic_sent=row['monotonic_sent'],
            monotonic_received=body.monotonic_received, offset_low=low, offset_high=high,
            valid_until=row['server_sample']+policy.ttl_seconds, policy=policy.public())
        conn.execute('UPDATE capture_clock_sample SET publication=?,proof=? WHERE proof_id=?',
                     (raw, json.dumps(proof, sort_keys=True, allow_nan=False), proof_id))
    return proof


@router.get('/api/processing/video/streams/{stream_id}/clock-proofs')
def read(stream_id: UUID, proof_id: UUID | None = None, node: dict = Depends(processor)):
    epoch, policy = configuration(); now = server_now(epoch, policy); stream_id = str(stream_id)
    with get_connection() as conn:
        _stream(conn, stream_id)
        query = 'SELECT proof FROM capture_clock_sample WHERE stream_id=? AND proof IS NOT NULL'
        values = [stream_id]
        if proof_id is not None: query += ' AND proof_id=?'; values.append(str(proof_id))
        row = conn.execute(query+' ORDER BY sample_sequence DESC LIMIT 1', tuple(values)).fetchone()
        if row is None: raise HTTPException(404, 'clock_proof_not_found')
    proof=json.loads(row['proof'])
    if proof['server_boot_id'] != SERVER_BOOT_ID:raise HTTPException(409,'clock_server_restarted')
    return dict(version=VERSION, server_time=now, server_epoch=epoch, server_boot_id=SERVER_BOOT_ID, proof=proof)


def bind_chunk(conn, stream_id, sequence, proof_id, capture_start, duration_ms):
    binding = conn.execute('SELECT * FROM capture_clock_stream WHERE stream_id=?', (stream_id,)).fetchone()
    if binding is None:
        if proof_id is not None: raise HTTPException(409, 'clock_stream_binding_required')
        return
    epoch, policy = configuration(); server_now(epoch, policy)
    old = conn.execute('SELECT proof_id FROM capture_clock_chunk WHERE stream_id=? AND sequence=?', (stream_id, sequence)).fetchone()
    if old:
        if old['proof_id'] != proof_id: raise HTTPException(409, 'clock_chunk_proof_conflict')
        return  # Immutable accepted association; later expiry cannot rewrite receipt.
    row = conn.execute('SELECT proof FROM capture_clock_sample WHERE stream_id=? AND proof_id=? AND proof IS NOT NULL', (stream_id, proof_id)).fetchone()
    if row is None: raise HTTPException(422, 'clock_chunk_proof_required')
    proof = json.loads(row['proof'])
    original_policy=ClockPolicy(**proof['policy'])
    from datetime import datetime
    start = datetime.fromisoformat(capture_start).timestamp()
    # Historical captured bytes retain their original authenticated proof and
    # policy. A new coordinator cannot renew that proof, but does not erase it.
    if (proof['stream_id']!=stream_id or proof['epoch']!=binding['epoch'] or proof['run_id']!=binding['run_id']
            or not covers(proof,start,start+duration_ms/1000,original_policy)):
        raise HTTPException(422, 'clock_chunk_outside_proof')
    conn.execute('INSERT INTO capture_clock_chunk VALUES(?,?,?)', (stream_id, sequence, proof_id))


@router.get('/api/video/streams/{stream_id}/clock-observation')
def source_observation(stream_id:UUID,node:dict=Depends(current_node)):
    """Read-only current server time for draining already captured media."""
    epoch,policy=configuration();now=server_now(epoch,policy);stream_id=str(stream_id)
    with get_connection() as conn:
        stream,context=_stream(conn,stream_id,node['node_id'])
        binding=conn.execute('SELECT * FROM capture_clock_stream WHERE stream_id=?',(stream_id,)).fetchone()
        if binding is None:raise HTTPException(409,'clock_stream_binding_required')
    return dict(version=VERSION,server_time=now,server_epoch=epoch,server_boot_id=SERVER_BOOT_ID,policy=policy.public(),
                source_node_id=node['node_id'],camera_id=stream['camera_id'],stream_id=stream_id,
                capture_context_id=context['context_id'],capture_session_id=context['session_id'],
                run_id=binding['run_id'],epoch=binding['epoch'])


def prune_settled_context(conn,context_id):
    """Only exact final-sequence source stop ACK proves no pending raw uploads."""
    conn.execute('''DELETE FROM capture_clock_sample WHERE stream_id IN (
        SELECT m.stream_id FROM capture_context_stream m JOIN capture_context_registry c ON c.context_id=m.context_id
        WHERE m.context_id=? AND c.valid_until IS NOT NULL)
        AND NOT EXISTS(SELECT 1 FROM capture_clock_chunk x WHERE x.proof_id=capture_clock_sample.proof_id)
        AND (proof IS NULL OR sample_sequence < (SELECT MAX(s.sample_sequence) FROM capture_clock_sample s
             WHERE s.stream_id=capture_clock_sample.stream_id AND s.proof IS NOT NULL))''',(context_id,))


def chunk_fields(conn, stream_id, sequence):
    row = conn.execute('SELECT proof_id FROM capture_clock_chunk WHERE stream_id=? AND sequence=?', (stream_id, sequence)).fetchone()
    return {'clock_proof_id': row['proof_id']} if row else {}
