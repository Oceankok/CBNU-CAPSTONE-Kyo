from fastapi import APIRouter, Depends, HTTPException, Query

from backend.auth.service import require_admin
from backend.education.models import MaterialApproval, MaterialContent, MaterialUpdate
from backend.education import service


router = APIRouter(prefix='/api', tags=['education materials'], dependencies=[Depends(require_admin)])


@router.post('/recommendations/{recommendation_id}/materials', status_code=201)
def create(recommendation_id: str, body: MaterialContent, user: dict = Depends(require_admin)):
    return service.create_draft(recommendation_id, body, user['user_id'])


@router.get('/recommendations/{recommendation_id}/materials')
def list_materials(recommendation_id: str, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return {'items': service.list_materials(recommendation_id, limit, offset)}


@router.get('/education/materials/{material_id}')
def get_material(material_id: str):
    return service.get_material(material_id)


@router.patch('/education/materials/{material_id}')
def edit(material_id: str, body: MaterialUpdate, user: dict = Depends(require_admin)):
    return service.edit_draft(material_id, body, user['user_id'], body.expected_version)


@router.post('/education/materials/{material_id}/publish')
def publish(material_id: str, body: MaterialApproval, user: dict = Depends(require_admin)):
    if not body.approved:
        raise HTTPException(400, 'education_approval_required')
    return service.publish(material_id, user['user_id'], body.expected_version)
