import asyncio
from datetime import datetime, timezone
from threading import BoundedSemaphore
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.field.routes import current_node
from backend.processing.routes import processor
from backend.video import service

router = APIRouter(tags=['video ingest'])
uploads = BoundedSemaphore(8)


class StreamInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    camera_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')


@router.post('/api/video/streams', status_code=201)
def create_stream(body: StreamInput, node: dict = Depends(current_node)):
    return service.create_stream(body.camera_id, node['node_id'])


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
    if not -10 <= difference <= 60:
        raise HTTPException(422, 'capture_start_clock_or_backlog_invalid')
    with get_connection() as conn:
        service.stream(conn, stream_id, node['node_id'])
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
        rows = conn.execute('''SELECT s.stream_id,s.camera_id,s.last_seen_at,s.last_sequence,s.sequence_gap_count
            FROM video_stream s JOIN camera_info c ON c.camera_id=s.camera_id
            JOIN camera_node n ON n.camera_id=s.camera_id AND n.source_node_id=s.node_id
            JOIN field_node f ON f.node_id=s.node_id
            WHERE s.status='active' AND c.status='active' AND f.is_active=1 ORDER BY s.camera_id''').fetchall()
    return {'items':[dict(row) for row in rows]}


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
    return {'items':[dict(row) for row in rows]}


@router.get('/api/processing/video/chunks/{chunk_id}')
def download_chunk(chunk_id: str, node: dict = Depends(processor)):
    with get_connection() as conn:
        # Serialize file reads against cleanup; the bounded bytes outlive the DB lock.
        begin_write(conn)
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
