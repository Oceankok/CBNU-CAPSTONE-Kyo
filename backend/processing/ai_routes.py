from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from backend.db.event_repository import get_connection
from backend.processing import ai_jobs
from backend.processing.routes import processor

router = APIRouter(prefix='/api/processing/ai', tags=['host AI processing'])


class Capabilities(BaseModel):
    model_config = ConfigDict(extra='forbid')
    capabilities: dict[str, str] = Field(default_factory=dict, max_length=2)


@router.put('/capabilities')
def capabilities(body: Capabilities, node: dict = Depends(processor)):
    ai_jobs.advertise(node['node_id'], body.capabilities)
    return {'registered': True}


@router.post('/claim')
def claim(node: dict = Depends(processor)):
    return ai_jobs.claim(node['node_id'])


@router.get('/jobs/{job_id}/source')
def source(job_id: str, node: dict = Depends(processor), x_processing_lease: str = Header(default='')):
    with get_connection() as conn:
        row = ai_jobs.authorized(conn, job_id, node['node_id'], x_processing_lease)
        if row['kind'] != 'equipment':
            raise HTTPException(404, 'host_ai_source_unavailable')
        try:
            data = ai_jobs.source_path(job_id).read_bytes()
        except OSError:
            raise HTTPException(404, 'host_ai_source_unavailable') from None
    return Response(data, media_type='application/octet-stream', headers={'Cache-Control': 'no-store'})


@router.post('/jobs/{job_id}/result')
async def result(job_id: str, request: Request, node: dict = Depends(processor),
                 x_processing_lease: str = Header(default='')):
    import json
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 15 * 1024 * 1024:
            raise HTTPException(413, 'host_ai_result_too_large')
        data.extend(chunk)
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, 'host_ai_result_invalid') from None
    return await run_in_threadpool(ai_jobs.complete, job_id, node['node_id'], x_processing_lease, payload)


class Failure(BaseModel):
    model_config = ConfigDict(extra='forbid')
    error_code: str = Field(max_length=100)


@router.post('/jobs/{job_id}/failure')
def failure(job_id: str, body: Failure, node: dict = Depends(processor),
            x_processing_lease: str = Header(default='')):
    return ai_jobs.complete(job_id, node['node_id'], x_processing_lease, error=body.error_code)


@router.post('/jobs/{job_id}/cleanup')
def cleanup(job_id: str, node: dict = Depends(processor)):
    return ai_jobs.cleanup_local(job_id, node['node_id'])
