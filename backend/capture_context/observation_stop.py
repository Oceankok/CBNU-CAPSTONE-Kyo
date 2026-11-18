"""Immutable, source-authenticated first frame excluded from PPE observation."""
import json
from fastapi import HTTPException
from backend.capture_context import service
from backend.db.event_repository import get_connection


def install(context_id,body,node_id):
    service.require_enabled()
    encoded=json.dumps(body,sort_keys=True,separators=(',',':'),allow_nan=False)
    with get_connection() as conn:
        service.begin(conn)
        context=service.owned(conn,context_id,node_id)
        row=conn.execute('''SELECT s.stream_id,s.node_id,s.camera_id,s.status,s.last_sequence,m.context_id
            FROM video_stream s JOIN capture_context_stream m ON m.stream_id=s.stream_id
            WHERE s.stream_id=?''',(body['stream_id'],)).fetchone()
        if row is None:raise HTTPException(404,'observation_stop_stream_not_found')
        if row['context_id']!=context_id or row['node_id']!=node_id or row['camera_id']!=context['camera_id']:
            raise HTTPException(403,'observation_stop_binding_mismatch')
        old=conn.execute('SELECT payload FROM capture_observation_stop WHERE stream_id=?',(body['stream_id'],)).fetchone()
        if old is not None:
            if old['payload']!=encoded:raise HTTPException(409,'observation_stop_conflict')
            return {'observation_stop':json.loads(old['payload'])}
        if context['valid_until'] is not None or row['status']=='closed':raise HTTPException(409,'capture_context_retired')
        if body['sequence']<=row['last_sequence']:raise HTTPException(409,'observation_stop_too_late')
        clock=conn.execute('SELECT run_id FROM capture_clock_stream WHERE stream_id=?',(body['stream_id'],)).fetchone()
        if clock is None or clock['run_id']!=body['run_id']:raise HTTPException(409,'observation_stop_run_mismatch')
        proof=conn.execute('SELECT proof FROM capture_clock_sample WHERE stream_id=? AND proof_id=? AND proof IS NOT NULL',
                           (body['stream_id'],body['proof_id'])).fetchone()
        if proof is None:raise HTTPException(422,'observation_stop_proof_required')
        data=json.loads(proof['proof'])
        if data.get('stream_id')!=body['stream_id'] or data.get('run_id')!=body['run_id'] or data.get('proof_id')!=body['proof_id']:
            raise HTTPException(409,'observation_stop_proof_binding_mismatch')
        conn.execute('INSERT INTO capture_observation_stop(stream_id,payload) VALUES(?,?)',(body['stream_id'],encoded))
        return {'observation_stop':body}


def verify_chunk_binding(conn,stream_id,sequence,proof_id):
    row=conn.execute('SELECT payload FROM capture_observation_stop WHERE stream_id=?',(stream_id,)).fetchone()
    if row is not None:
        stop=json.loads(row['payload'])
        if sequence==stop['sequence'] and proof_id!=stop['proof_id']:
            raise HTTPException(409,'observation_stop_chunk_proof_conflict')
