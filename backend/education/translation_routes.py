from fastapi import APIRouter, Depends, HTTPException, Query, Response
from backend.auth.service import require_admin, current_user
from backend.education.models import MaterialApproval, MaterialContent, MaterialVersion
from backend.education.translation_models import TranslationCreate, TranslationUpdate, TranslationGenerate, WorkerTranslationRequest
from backend.education import translations as service

router=APIRouter(prefix='/api/education',tags=['education translations'],dependencies=[Depends(require_admin)])
worker_router = APIRouter(prefix='/api/worker/education-translations', tags=['worker'])


@worker_router.post('/request', status_code=202)
def request_translation(body: WorkerTranslationRequest, response: Response, user=Depends(current_user)):
    if user['role'] != 'worker':
        raise HTTPException(403, 'Worker access required')
    from backend.education.auto_translation import request
    from backend.education.languages import protect_worker_response
    protect_worker_response(response)
    return request(user['user_id'], body)

@router.get('/translation-generation')
def availability():
    from backend.education.translation_provider import availability
    return availability()


@router.get('/materials/{material_id}/automatic-translations')
def material_auto_cache(material_id: str, source_version: int = Query(ge=0)):
    from backend.education.auto_translation import admin_cached
    return admin_cached('material', material_id, source_version)


@router.get('/bundles/{bundle_id}/automatic-translations')
def bundle_auto_cache(bundle_id: str, source_version: int = Query(ge=0)):
    from backend.education.auto_translation import admin_cached
    return admin_cached('bundle', bundle_id, source_version)

@router.get('/materials/{material_id}/translations')
def list_translations(material_id: str, limit: int=Query(50,ge=1,le=100), offset: int=Query(0,ge=0)):
    return {'items':service.list_for(material_id,limit,offset)}

@router.post('/materials/{material_id}/translations',status_code=201)
def create(material_id: str,body: TranslationCreate,user: dict=Depends(require_admin)):
    content=MaterialContent(title=body.title,body=body.body,checklist=body.checklist)
    return service.create(material_id,content,body.language,body.expected_source_version,user['user_id'])

@router.post('/materials/{material_id}/translations/generate',status_code=201)
def generate(material_id: str,body: TranslationGenerate,user: dict=Depends(require_admin)):
    return service.generate(material_id,body,user['user_id'])

@router.get('/translations/{translation_id}')
def get(translation_id: str):
    return service.get(translation_id)

@router.patch('/translations/{translation_id}')
def edit(translation_id: str,body: TranslationUpdate,user: dict=Depends(require_admin)):
    return service.edit(translation_id,body,user['user_id'])

@router.post('/translations/{translation_id}/publish')
def publish(translation_id: str,body: MaterialApproval,user: dict=Depends(require_admin)):
    if not body.approved:
        raise HTTPException(400,'education_translation_approval_required')
    return service.publish(translation_id,body.expected_version,user['user_id'])

@router.post('/translations/{translation_id}/unpublish')
def unpublish(translation_id: str,body: MaterialVersion,user: dict=Depends(require_admin)):
    return service.unpublish(translation_id,body.expected_version,user['user_id'])


@router.get('/bundles/{bundle_id}/translations')
def list_bundle_translations(bundle_id: str, limit: int=Query(50,ge=1,le=100), offset: int=Query(0,ge=0)):
    return {'items':service.list_for(bundle_id,limit,offset,kind='bundle')}

@router.post('/bundles/{bundle_id}/translations',status_code=201)
def create_bundle_translation(bundle_id: str,body: TranslationCreate,user: dict=Depends(require_admin)):
    content=MaterialContent(title=body.title,body=body.body,checklist=body.checklist)
    return service.create(bundle_id,content,body.language,body.expected_source_version,user['user_id'],kind='bundle')

@router.post('/bundles/{bundle_id}/translations/generate',status_code=201)
def generate_bundle_translation(bundle_id: str,body: TranslationGenerate,user: dict=Depends(require_admin)):
    return service.generate(bundle_id,body,user['user_id'],kind='bundle')
