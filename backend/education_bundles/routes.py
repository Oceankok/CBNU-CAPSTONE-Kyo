from fastapi import Response, Header, APIRouter, Depends, HTTPException, Query
from backend.auth.service import current_user, require_admin
from backend.education.models import MaterialUpdate, MaterialVersion, MaterialApproval
from .models import Selection, SelectedSnapshot, BundleCreate
from . import service

router = APIRouter(prefix='/api/education/bundles', tags=['education-bundles'], dependencies=[Depends(require_admin)])
worker = APIRouter(prefix='/api/worker', tags=['worker'])


@router.post('/preview')
def preview(payload: Selection):
    return service.preview(payload.recommendation_ids)


@router.post('/generate', status_code=201)
def generate(payload: SelectedSnapshot, user=Depends(require_admin)):
    return service.generate(payload.recommendation_ids, payload.expected_snapshot, user['user_id'])


@router.post('', status_code=201)
def create(payload: BundleCreate, user=Depends(require_admin)):
    return service.create(payload.recommendation_ids, payload.expected_snapshot, payload, user['user_id'])


@router.get('')
def list_bundles(quarter: str = Query(min_length=1, max_length=100), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return {'items': service.list_bundles(quarter, limit, offset)}


@router.get('/{bundle_id}')
def get(bundle_id: str):
    return service.get(bundle_id)


@router.patch('/{bundle_id}')
def edit(bundle_id: str, payload: MaterialUpdate, user=Depends(require_admin)):
    return service.change(bundle_id, payload.expected_version, user['user_id'], action='edit', content=payload)


@router.post('/{bundle_id}/publish')
def publish(bundle_id: str, payload: MaterialApproval, user=Depends(require_admin)):
    if not payload.approved:
        raise HTTPException(400, 'education_approval_required')
    return service.change(bundle_id, payload.expected_version, user['user_id'], action='publish')


@router.post('/{bundle_id}/unpublish')
def unpublish(bundle_id: str, payload: MaterialVersion, user=Depends(require_admin)):
    return service.change(bundle_id, payload.expected_version, user['user_id'], action='unpublish')


@worker.get('/education-bundles')
def worker_bundles(language: str | None = Query(default=None, pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$", max_length=6), accept_language: str | None = Header(default=None), user=Depends(current_user), response: Response = None):
    from backend.education.languages import protect_worker_response
    protect_worker_response(response)
    if user['role'] != 'worker':
        raise HTTPException(403, 'Worker access required')
    from backend.education.languages import preferences
    return {'items': service.for_worker(user['user_id'], preferences(language, accept_language))}
