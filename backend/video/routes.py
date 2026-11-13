import asyncio
from datetime import datetime, timezone
from threading import BoundedSemaphore
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from backend.db.event_repository import get_connection
from backend.field.routes import current_node
from backend.processing.routes import processor
from backend.video import service

router = APIRouter(tags=['video ingest'])
uploads = BoundedSemaphore(8)


def _validate_stream_access(stream_id, node_id):
    """Run the synchronous stream and camera checks outside the event loop."""
    with get_connection() as conn:
        service.stream(conn, stream_id, node_id)
        from backend.capture_context.service import stream_fields
        return stream_fields(conn,stream_id)


class StreamInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    camera_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    capture_context_id: str | None = Field(default=None,pattern=r'^[0-9a-f-]{36}$')
    stream_id: str | None = Field(default=None,pattern=r'^[0-9a-f-]{36}$')


@router.post('/api/video/streams', status_code=201)
def create_stream(body: StreamInput, node: dict = Depends(current_node)):
    return service.create_stream(body.camera_id, node['node_id'],capture_context_id=body.capture_context_id,requested_id=body.stream_id)


@router.post('/api/video/streams/{stream_id}/chunks', status_code=202)
async def upload_chunk(stream_id: str, request: Request,
                       sequence: int = Query(ge=0, le=2147483647),
                       x_capture_start: str = Header(max_length=50),
                       x_duration_ms: int = Header(ge=250, le=3000),
                       node: dict = Depends(current_node)):
    if request.headers.get('content-type', '').split(';')[0].strip() != 'video/mp4':
        raise HTTPException(415, 'video_mp4_required')
    captured = service.capture_time(x_capture_start)
    difference = (datetime.now(timezone.utc)-datetime.fromisoformat(captured)).total_seconds()
    context=await run_in_threadpool(_validate_stream_access, stream_id, node['node_id'])
    if (not context and not -10 <= difference <= 60) or (context and difference < 0):
        raise HTTPException(422, 'capture_start_clock_or_backlog_invalid')
    if not uploads.acquire(blocking=False):
        raise HTTPException(429, 'video_upload_capacity_reached', headers={'Retry-After':'1'})
    source = None
    try:
        service.ROOT.mkdir(parents=True, exist_ok=True)
        source = service.chunk_path(str(uuid4()))
        size = 0
        try:
            async with asyncio.timeout(15):
                with source.open('xb') as file:
                    async for data in request.stream():
                        size += len(data)
                        if size > service.MAX_BYTES:
                            raise HTTPException(413, 'video_chunk_too_large')
                        file.write(data)
        except TimeoutError:
            raise HTTPException(408, 'video_upload_timeout')
        if size == 0:
            raise HTTPException(422, 'empty_video_chunk')
        await run_in_threadpool(service.validate_video, source, x_duration_ms)
        return await run_in_threadpool(service.accept, stream_id, node['node_id'], sequence, captured, x_duration_ms, source)
    finally:
        try:
            if source:
                source.unlink(missing_ok=True)
        finally:
            uploads.release()


@router.get('/api/processing/video/streams')
def active_streams(node: dict = Depends(processor)):
    service.cleanup()
    with get_connection() as conn:
        rows = conn.execute('''SELECT s.stream_id,s.camera_id,s.last_seen_at,s.last_sequence,s.sequence_gap_count,s.status
            FROM video_stream s JOIN camera_info c ON c.camera_id=s.camera_id
            JOIN camera_node n ON n.camera_id=s.camera_id AND n.source_node_id=s.node_id
            JOIN field_node f ON f.node_id=s.node_id
            WHERE (s.status='active' OR (s.status='closed'
              AND EXISTS (SELECT 1 FROM capture_context_stream cs WHERE cs.stream_id=s.stream_id)
              AND EXISTS (SELECT 1 FROM video_chunk vc WHERE vc.stream_id=s.stream_id AND vc.expires_at>?)))
            AND c.status='active' AND f.is_active=1 ORDER BY s.camera_id''',(service.time.time(),)).fetchall()
        from backend.capture_context.service import stream_fields
        items=[]
        for row in rows:
            item=dict(row);context=stream_fields(conn,row['stream_id'])
            if context:
                item.update(context)
                item['capture_stopped']=row['status']=='closed'
                if item['capture_stopped']:item['final_sequence']=row['last_sequence']
            item.pop('status',None)
            items.append(item)
    return {'items':items}


@router.get('/api/processing/video/streams/{stream_id}/chunks')
def list_chunks(stream_id: str, after_sequence: int = Query(default=-1, ge=-1), node: dict = Depends(processor)):
    with get_connection() as conn:
        row = conn.execute('SELECT * FROM video_stream WHERE stream_id=?', (stream_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'video_stream_not_found')
        service.source_allowed(conn, row['camera_id'], row['node_id'])
        rows = conn.execute('''SELECT chunk_id,stream_id,sequence,capture_start_at,duration_ms,received_at,expires_at,byte_size
            FROM video_chunk WHERE stream_id=? AND sequence>? AND expires_at>? ORDER BY sequence LIMIT 40''',
                            (stream_id, after_sequence, service.time.time())).fetchall()
        from backend.capture_context.service import stream_fields
        extra=stream_fields(conn,stream_id)
        items=[dict(row)|extra for row in rows]
    return {'items':items}


@router.get('/api/processing/video/chunks/{chunk_id}')
def download_chunk(chunk_id: str, node: dict = Depends(processor)):
    with get_connection() as conn:
        row = conn.execute('''SELECT c.*,s.camera_id,s.node_id FROM video_chunk c
            JOIN video_stream s ON s.stream_id=c.stream_id WHERE c.chunk_id=?''', (chunk_id,)).fetchone()
        if not row or row['expires_at'] <= service.time.time():
            raise HTTPException(404, 'video_chunk_not_found')
        service.source_allowed(conn, row['camera_id'], row['node_id'])
        try:
            data = service.chunk_path(chunk_id).read_bytes()
        except FileNotFoundError:
            raise HTTPException(404, 'video_chunk_file_missing')
    return Response(data, media_type='video/mp4', headers={'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff'})


@router.get('/api/processing/video/streams/{stream_id}/availability')
def stream_availability(stream_id: str, after_sequence: int = Query(default=-1,ge=-1,le=2147483647), node: dict = Depends(processor)):
    """Read one expected sequence, never renew raw retention or guess EOS.

    Bound processors can distinguish missing media from an empty live poll even
    after a closed stream disappears from discovery. Receipts mean delivery only.
    """
    with get_connection() as conn:
        row=conn.execute('SELECT * FROM video_stream WHERE stream_id=?',(stream_id,)).fetchone()
        if row is None:raise HTTPException(404,'video_stream_not_found')
        service.source_allowed(conn,row['camera_id'],row['node_id'])
        from backend.capture_context.service import stream_fields
        extra=stream_fields(conn,stream_id)
        if not extra:raise HTTPException(409,'capture_context_stream_required')
        if after_sequence>row['last_sequence']:
            raise HTTPException(409,'capture_cursor_ahead_of_received_sequence')
        sequence=after_sequence+1
        result=dict(stream_id=stream_id,camera_id=row['camera_id'],last_sequence=row['last_sequence'],
                    capture_stopped=row['status']=='closed',next_sequence=sequence,**extra)
        if result['capture_stopped']:result['final_sequence']=row['last_sequence']
        if sequence>row['last_sequence']:
            result.update(next_chunk_state='not_received',reason='not_received')
            return result
        receipt=conn.execute('SELECT 1 FROM capture_chunk_receipt WHERE stream_id=? AND sequence=?',(stream_id,sequence)).fetchone()
        chunk=conn.execute('SELECT chunk_id,expires_at FROM video_chunk WHERE stream_id=? AND sequence=?',(stream_id,sequence)).fetchone()
        if receipt is None:
            result.update(next_chunk_state='unavailable',reason='receipt_missing')
        elif chunk is None:
            result.update(next_chunk_state='unavailable',reason='raw_unavailable')
        elif chunk['expires_at']<=service.time.time():
            result.update(next_chunk_state='unavailable',reason='raw_expired')
        else:
            try:exists=service.chunk_path(chunk['chunk_id']).is_file()
            except (OSError,HTTPException):exists=False
            result.update(next_chunk_state='available' if exists else 'unavailable',
                          reason='available' if exists else 'raw_missing')
        return result
