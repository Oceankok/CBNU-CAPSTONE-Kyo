import hashlib
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from backend.auth.service import require_admin
from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.field.routes import current_node
from backend.media.paths import WORK_ROOT, PROCESSED_ROOT, private_source_path, storage_key
from backend.processing import service

router = APIRouter(prefix='/api/processing', tags=['processing'])


def processor(node: dict = Depends(current_node)):
    with get_connection() as conn:
        row = conn.execute('SELECT enabled FROM processing_worker WHERE node_id=?', (node['node_id'],)).fetchone()
    if not row or not row[0]:
        raise HTTPException(403, 'Processing worker access required')
    return node


class WorkerSettings(BaseModel):
    enabled: bool


@router.put('/workers/{node_id}', dependencies=[Depends(require_admin)])
def configure_worker(node_id: str, body: WorkerSettings):
    with get_connection() as conn:
        begin_write(conn)
        if not conn.execute('SELECT 1 FROM field_node WHERE node_id=?', (node_id,)).fetchone():
            raise HTTPException(404, 'Node not found')
        conn.execute('''INSERT INTO processing_worker(node_id,enabled) VALUES(?,?)
            ON CONFLICT(node_id) DO UPDATE SET enabled=excluded.enabled''', (node_id, int(body.enabled)))
    return {'node_id': node_id, 'enabled': body.enabled}


@router.get('/workers', dependencies=[Depends(require_admin)])
def list_workers():
    with get_connection() as conn:
        rows = conn.execute('''SELECT p.node_id,p.enabled,n.name,n.is_active,n.last_seen_at
            FROM processing_worker p JOIN field_node n ON n.node_id=p.node_id ORDER BY p.node_id''').fetchall()
    return {'items': [dict(row) for row in rows]}


@router.get('/jobs/{job_id}', dependencies=[Depends(require_admin)])
def read_job(job_id: str):
    with get_connection() as conn:
        return service.public(service.job(conn, job_id))


@router.post('/claim')
def claim(node: dict = Depends(processor)):
    return service.claim(node['node_id'])


@router.post('/jobs/{job_id}/lease')
def renew(job_id: str, node: dict = Depends(processor), x_processing_lease: str = Header(default='')):
    return service.renew(job_id, node['node_id'], x_processing_lease)


@router.get('/jobs/{job_id}/source')
def source(job_id: str, node: dict = Depends(processor), x_processing_lease: str = Header(default='')):
    with get_connection() as conn:
        begin_write(conn)
        row = service.authorized(conn, job_id, node['node_id'], x_processing_lease)
        media = conn.execute('SELECT * FROM event_media WHERE media_id=?', (row['media_id'],)).fetchone()
        if not media['source_path'] or media['source_expires_at'] <= time.time():
            raise HTTPException(404, 'source_expired')
        try:
            data = private_source_path(media['source_path']).read_bytes()
        except (ValueError, OSError):
            raise HTTPException(404, 'source_file_missing')
    return Response(data, media_type='application/octet-stream', headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


class Failure(BaseModel):
    error_code: Literal['source_file_missing', 'source_expired', 'invalid_frame', 'video_decode_failed',
                        'incomplete_video_decode', 'scrfd_model_missing', 'face_model_missing',
                        'media_processing_failed', 'gpu_unavailable']


@router.post('/jobs/{job_id}/failure')
def failure(job_id: str, body: Failure, node: dict = Depends(processor), x_processing_lease: str = Header(default='')):
    return service.finish_failure(job_id, node['node_id'], x_processing_lease, body.error_code)


@router.post('/jobs/{job_id}/cleanup')
def cleanup(job_id: str, node: dict = Depends(current_node)):
    # A revoked processing capability must not prevent an owner from acknowledging cleanup.
    return service.cleanup(job_id, node['node_id'])


class ResultMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid')
    faces_detected: int = Field(ge=0, le=10000000)
    processed_frames: int = Field(ge=1, le=100000)
    redaction_mode: Literal['enhanced', 'scrfd']
    inference_backend: str = Field(min_length=1, max_length=100)
    processing_seconds: float = Field(ge=0, le=86400, allow_inf_nan=False)


def validate_result(path, kind, meta):
    import cv2
    if kind == 'image':
        frame = cv2.imread(str(path))
        if frame is None or meta.processed_frames != 1 or frame.shape[0] * frame.shape[1] > 16000000:
            raise HTTPException(422, 'invalid_processed_image')
    else:
        cap = cv2.VideoCapture(str(path))
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            if not cap.isOpened() or not math.isfinite(fps) or fps <= 0:
                raise HTTPException(422, 'invalid_processed_video')
            count = 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                count += 1
                if count > 100000 or frame.shape[0] * frame.shape[1] > 16000000:
                    raise HTTPException(422, 'processed_video_too_large')
            if count != meta.processed_frames:
                raise HTTPException(422, 'processed_frame_count_mismatch')
        finally:
            cap.release()


def publish(job_id, node_id, token, path, meta):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    moved = None
    try:
        with get_connection() as conn:
            begin_write(conn)
            row = service.authorized(conn, job_id, node_id, token, terminal=True)
            if row['status'] == 'succeeded':
                if row['result_sha256'] != digest:
                    raise HTTPException(409, 'processing_result_conflict')
                return service.public(row)
            if row['redaction_mode'] != meta.redaction_mode:
                raise HTTPException(422, 'processing_mode_mismatch')
            PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)
            target = PROCESSED_ROOT / (row['media_id'] + path.suffix)
            if target.resolve().parent != PROCESSED_ROOT.resolve():
                raise HTTPException(409, 'unsafe_result_path')
            os.replace(path, target)
            moved = target
            conn.execute('''UPDATE event_media SET storage_path=?,status='ready',redaction_status='complete',
                processing_started_at=NULL,error_code=NULL,faces_detected=?,processed_frames=?,redaction_mode=?,
                inference_backend=?,processing_seconds=? WHERE media_id=?''',
                         (storage_key(target), meta.faces_detected, meta.processed_frames, meta.redaction_mode,
                          meta.inference_backend, meta.processing_seconds, row['media_id']))
            conn.execute("UPDATE processing_job SET status='succeeded',result_sha256=?,finished_at=?,updated_at=? WHERE job_id=?",
                         (digest, time.time(), time.time(), job_id))
            return service.public(service.job(conn, job_id))
    except Exception:
        if moved:
            moved.unlink(missing_ok=True)
        raise


@router.post('/jobs/{job_id}/result')
async def result(job_id: str, request: Request, node: dict = Depends(processor),
                 x_processing_lease: str = Header(default=''), x_processing_metadata: str = Header(default='')):
    if len(x_processing_metadata) > 4096:
        raise HTTPException(422, 'processing_metadata_too_large')
    try:
        meta = ResultMetadata.model_validate_json(x_processing_metadata)
    except ValueError:
        raise HTTPException(422, 'processing_metadata_invalid')
    with get_connection() as conn:
        row = service.authorized(conn, job_id, node['node_id'], x_processing_lease, terminal=True)
    mime = request.headers.get('content-type', '').split(';')[0]
    if mime != ('image/jpeg' if row['kind'] == 'image' else 'video/mp4'):
        raise HTTPException(415, 'processed_media_type_invalid')
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ppe_result_', dir=WORK_ROOT) as folder:
        path = Path(folder) / ('output.jpg' if row['kind'] == 'image' else 'output.mp4')
        size = 0
        with path.open('wb') as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > int(os.environ.get('PPE_MEDIA_MAX_UPLOAD_BYTES', 100 * 1024 * 1024)):
                    raise HTTPException(413, 'media_upload_too_large')
                output.write(chunk)
        await run_in_threadpool(validate_result, path, row['kind'], meta)
        return await run_in_threadpool(publish, job_id, node['node_id'], x_processing_lease, path, meta)
