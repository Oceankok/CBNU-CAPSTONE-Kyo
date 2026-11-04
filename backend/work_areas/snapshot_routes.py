import asyncio
import hashlib
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from backend.auth.service import require_admin
from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.field.routes import current_node
from backend.processing.routes import processor
from backend.work_areas import snapshots as service

router = APIRouter(tags=['camera snapshots'])
PRIVATE = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}


@router.post('/api/cameras/{camera_id}/snapshots', status_code=202, dependencies=[Depends(require_admin)])
def request_snapshot(camera_id: str):
    return service.request(camera_id)


def camera_snapshot(conn, camera_id, ident):
    item = service.row(conn, ident)
    if item['camera_id'] != camera_id:
        raise HTTPException(404, 'snapshot_not_found')
    return item


@router.get('/api/cameras/{camera_id}/snapshots/{ident}', dependencies=[Depends(require_admin)])
def status(camera_id: str, ident: str):
    with get_connection() as conn:
        return service.public(camera_snapshot(conn, camera_id, ident))


@router.get('/api/cameras/{camera_id}/snapshots/{ident}/image', dependencies=[Depends(require_admin)])
def image(camera_id: str, ident: str):
    with get_connection() as conn:
        begin_write(conn)
        item = camera_snapshot(conn, camera_id, ident)
        if item['status'] != 'ready' or item['expires_at'] <= time.time():
            raise HTTPException(409, 'snapshot_not_ready')
        from backend.video.service import source_allowed
        source_allowed(conn, camera_id, item['source_node_id'])
        try:
            data = service.path(ident, '.jpg').read_bytes()
        except OSError:
            raise HTTPException(404, 'snapshot_file_missing')
    return Response(data, media_type='image/jpeg', headers=PRIVATE)


@router.post('/api/processing/snapshots/claim')
def claim(node: dict = Depends(processor)):
    return service.claim(node['node_id'])


@router.get('/api/processing/snapshots/{ident}/source')
def source(ident: str, node: dict = Depends(processor), x_processing_lease: str = Header(default='')):
    with get_connection() as conn:
        begin_write(conn)
        service.authorize(conn, ident, node['node_id'], x_processing_lease)
        try:
            data = service.path(ident, '.mp4').read_bytes()
        except OSError:
            raise HTTPException(404, 'snapshot_source_missing')
    return Response(data, media_type='video/mp4', headers=PRIVATE)


def publish(ident, node_id, token, data):
    import cv2
    import numpy as np
    if not data.startswith(b'\xff\xd8'):
        raise HTTPException(422, 'snapshot_jpeg_required')
    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None or frame.shape[0]*frame.shape[1] > 1920*1080:
        raise HTTPException(422, 'snapshot_image_invalid')
    digest = hashlib.sha256(data).hexdigest()
    with get_connection() as conn:
        begin_write(conn)
        item = service.authorize(conn, ident, node_id, token, terminal=True)
        if item['status'] == 'ready':
            if digest != item['result_sha256']:
                raise HTTPException(409, 'snapshot_result_conflict')
            return service.public(item)
        file = service.path(ident, '.jpg')
        try:
            with file.open('xb') as output:
                output.write(data)
            conn.execute("UPDATE camera_snapshot SET status='ready',result_sha256=? WHERE snapshot_id=?", (digest, ident))
        except Exception:
            file.unlink(missing_ok=True)
            raise
        return service.public(service.row(conn, ident))


@router.post('/api/processing/snapshots/{ident}/result')
async def result(ident: str, request: Request, node: dict = Depends(processor), x_processing_lease: str = Header(default='')):
    with get_connection() as conn:
        service.authorize(conn, ident, node['node_id'], x_processing_lease, terminal=True)
    if request.headers.get('content-type', '').split(';')[0] != 'image/jpeg':
        raise HTTPException(415, 'snapshot_jpeg_required')
    data = bytearray()
    try:
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 4*1024*1024:
                    raise HTTPException(413, 'snapshot_image_too_large')
    except TimeoutError:
        raise HTTPException(408, 'snapshot_upload_timeout')
    return await run_in_threadpool(publish, ident, node['node_id'], x_processing_lease, bytes(data))


class Failure(BaseModel):
    error_code: str = Field(pattern=r'^(snapshot_processing_failed|snapshot_source_missing|scrfd_model_missing|gpu_unavailable)$')


@router.post('/api/processing/snapshots/{ident}/failure')
def failure(ident: str, body: Failure, node: dict = Depends(processor), x_processing_lease: str = Header(default='')):
    with get_connection() as conn:
        begin_write(conn)
        service.authorize(conn, ident, node['node_id'], x_processing_lease)
        conn.execute("UPDATE camera_snapshot SET status='failed',error_code=? WHERE snapshot_id=?", (body.error_code, ident))
        return service.public(service.row(conn, ident))


@router.post('/api/processing/snapshots/{ident}/cleanup')
def cleanup(ident: str, node: dict = Depends(current_node)):
    with get_connection() as conn:
        begin_write(conn)
        try:
            item = service.row(conn, ident)
        except HTTPException as error:
            if error.status_code == 404:
                return {'cleaned': True}  # Expired payload already erased; a lost acknowledgement is safe to retry.
            raise
        if item['worker_node_id'] != node['node_id']:
            raise HTTPException(403, 'snapshot_worker_mismatch')
        # A late response or interruption may leave a running lease: don't release it early.
        if item['status'] == 'running' and item['lease_until'] > time.time() and item['expires_at'] > time.time():
            raise HTTPException(409, 'snapshot_still_running')
        conn.execute("UPDATE camera_snapshot SET local_cleanup_status='confirmed' WHERE snapshot_id=?", (ident,))
    service.maintain()
    return {'cleaned': True}
