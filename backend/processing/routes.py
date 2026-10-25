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
from backend.media.paths import WORK_ROOT, PROCESSED_ROOT, private_source_path, storage_key, publish_file
from backend.processing import service
from backend.processing.events import EventSubmission, submit

router = APIRouter(prefix='/api/processing', tags=['processing'])


def processor(node: dict = Depends(current_node)):
    with get_connection() as conn:
        row = conn.execute('SELECT enabled FROM processing_worker WHERE node_id=?', (node['node_id'],)).fetchone()
    if not row or not row[0]:
        raise HTTPException(403, 'Processing worker access required')
    return node


class WorkerSettings(BaseModel):
    enabled: bool


class InferenceLoad(BaseModel):
    busy: bool
    ttl_seconds: int = Field(default=10, ge=1, le=30)


@router.put('/inference-load')
def set_inference_load(body: InferenceLoad, node: dict = Depends(processor)):
    until = time.time() + body.ttl_seconds if body.busy else 0
    with get_connection() as conn:
        begin_write(conn)
        conn.execute('''INSERT INTO processing_inference_load(node_id,busy_until,updated_at) VALUES(?,?,?)
            ON CONFLICT(node_id) DO UPDATE SET busy_until=excluded.busy_until,updated_at=excluded.updated_at''',
                     (node['node_id'], until, time.time()))
    return {'busy_until': until}


@router.get('/inference-load')
def get_inference_load(node: dict = Depends(processor)):
    with get_connection() as conn:
        row = conn.execute('SELECT MAX(busy_until) FROM processing_inference_load').fetchone()
    return {'busy': bool(row and row[0] and row[0] > time.time()), 'busy_until': row[0] or 0}


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


class ClipRequestInput(BaseModel):
    camera_id: str = Field(min_length=1, max_length=100)


@router.post('/events', status_code=201)
def submit_event(body: EventSubmission, response: Response, node: dict = Depends(processor)):
    result = submit(body)
    response.status_code = 201 if result['created'] else 200
    return result


@router.get('/cameras/{camera_id}/ppe-policy')
def read_inference_policy(camera_id: str, node: dict = Depends(processor)):
    from backend.equipment.routes import get_camera_policy
    return get_camera_policy(camera_id)


@router.post('/events/{event_id}/clip-requests', status_code=201)
def create_clip_request(event_id: str, body: ClipRequestInput, node: dict = Depends(processor)):
    from backend.media.routes import register_clip_request, ClipRequestRegistration
    return register_clip_request(event_id, ClipRequestRegistration(camera_id=body.camera_id))


@router.get('/events/{event_id}/clip-requests')
def read_clip_requests(event_id: str, node: dict = Depends(processor)):
    with get_connection() as conn:
        merged = conn.execute('SELECT target_event_id FROM event_merge WHERE source_event_id=?', (event_id,)).fetchone()
        if merged:
            raise HTTPException(409, {'code': 'event_merged', 'merged_into_event_id': merged[0]})
        event = conn.execute('SELECT timestamp_start,timestamp_end FROM candidate_event WHERE event_id=?', (event_id,)).fetchone()
        if not event:
            raise HTTPException(404, 'event_not_found')
        if conn.execute("SELECT 1 FROM event_retention WHERE event_id=? AND decision='delete'", (event_id,)).fetchone():
            raise HTTPException(409, 'event_media_deleted')
        rows = conn.execute('''SELECT r.*,n.source_node_id FROM event_clip_request r
            LEFT JOIN camera_node n ON n.camera_id=r.camera_id WHERE r.event_id=? ORDER BY r.created_at,r.camera_id,r.request_id''', (event_id,)).fetchall()
    return {'event_id': event_id, **dict(event), 'items': [dict(row) for row in rows]}


@router.post('/events/{event_id}/clip', status_code=201)
async def upload_clip(event_id: str, request: Request, request_id: str,
                      camera_id: str | None = None, node: dict = Depends(processor),
                      redaction_mode: Literal['enhanced','scrfd'] = 'scrfd'):
    from backend.media.routes import upload_media
    with get_connection() as conn:
        registration = conn.execute('SELECT camera_id,role FROM event_clip_request WHERE request_id=? AND event_id=?', (request_id, event_id)).fetchone()
        if not registration:
            raise HTTPException(404, 'clip_request_not_found')
    return await upload_media(event_id, request, kind='video', camera_id=camera_id or registration['camera_id'], role=registration['role'],
                              request_id=request_id, redaction_mode=redaction_mode)


class ClipDeliveryFailure(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    status: Literal['failed', 'unavailable']
    error_code: str = Field(min_length=1, max_length=200)


@router.put('/events/{event_id}/clip-requests/{request_id}')
def report_clip_failure(event_id: str, request_id: str, body: ClipDeliveryFailure,
                        node: dict = Depends(processor)):
    from backend.media.routes import ClipRequestResult, report_clip_request
    return report_clip_request(event_id, request_id, ClipRequestResult(**body.model_dump()))


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
            publish_file(path, target)
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
