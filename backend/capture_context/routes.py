from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Depends
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictInt
from backend.field.routes import current_node
from . import service
router=APIRouter(prefix='/api/video/capture-sessions',tags=['capture sessions'])
class Start(BaseModel):
    model_config=ConfigDict(extra='forbid')
    camera_id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    session_id:UUID
class Stop(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    capture_stopped:StrictBool
    final_sequences:dict[str,StrictInt]=Field(max_length=1000)
@router.post('',status_code=201)
def start(body:Start,node:dict=Depends(current_node)):
    return service.start(body.camera_id,str(body.session_id),node['node_id'])
@router.post('/{context_id}/stop')
def stop(context_id:UUID,body:Stop,node:dict=Depends(current_node)):
    from fastapi import HTTPException
    if body.capture_stopped is not True or any(v < -1 or v>2147483647 for v in body.final_sequences.values()):
        raise HTTPException(422,'explicit_capture_stop_and_final_sequences_required')
    return service.stop(str(context_id),node['node_id'],body.final_sequences)

# Explicit source acknowledgement for a pre-protocol stream during migration.
# It is not reachable via an administrator's login token.
legacy_router=APIRouter(prefix='/api/video/streams',tags=['capture sessions'])
class LegacyStop(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    capture_stopped:StrictBool
    final_sequence:StrictInt
@legacy_router.post('/{stream_id}/stop')
def legacy_stop(stream_id:UUID,body:LegacyStop,node:dict=Depends(current_node)):
    from fastapi import HTTPException
    from backend.db.event_repository import get_connection
    service.require_enabled()
    if body.capture_stopped is not True:raise HTTPException(422,'explicit_capture_stop_required')
    with get_connection() as conn:
        service.begin(conn)
        row=conn.execute('SELECT * FROM video_stream WHERE stream_id=?',(str(stream_id),)).fetchone()
        if row is None:raise HTTPException(404,'video_stream_not_found')
        if row['node_id']!=node['node_id']:raise HTTPException(403,'video_stream_owner_required')
        if service.stream_fields(conn,str(stream_id)):raise HTTPException(409,'capture_session_stop_required')
        if body.final_sequence!=row['last_sequence'] or row['sequence_gap_count']:
            raise HTTPException(409,'capture_final_sequence_manifest_mismatch_or_gap')
        conn.execute("UPDATE video_stream SET status='closed' WHERE stream_id=?",(str(stream_id),))
        return {'stream_id':str(stream_id),'closed':True}
