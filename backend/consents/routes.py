from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.auth.service import require_admin
from backend.consents import service


router = APIRouter(prefix='/api/consent-records', tags=['consent records'], dependencies=[Depends(require_admin)])


class ConsentMetadata(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    site_code: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
    scope_description: str = Field(min_length=1, max_length=2000)
    form_version: str = Field(min_length=1, max_length=80)
    consent_confirmed: bool


class Revocation(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)


@router.post('', status_code=201)
def upload_record(metadata: str = Form(...), file: UploadFile = File(...), user: dict = Depends(require_admin)):
    try:
        body = ConsentMetadata.model_validate_json(metadata)
    except ValidationError as exc:
        raise HTTPException(422, 'invalid_consent_metadata') from exc
    if not body.consent_confirmed:
        raise HTTPException(400, 'consent_confirmation_required')
    if file.content_type not in {'application/pdf', 'application/octet-stream'} or not (file.filename or '').lower().endswith('.pdf'):
        raise HTTPException(400, 'invalid_consent_pdf')
    return service.create_record(file.file, body, user['user_id'])


@router.get('')
def records(site_code: str | None = Query(default=None, max_length=80), limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return {'items': service.list_records(site_code, limit, offset)}


@router.get('/{record_id}')
def record(record_id: str):
    return service.get_record(record_id)


@router.get('/{record_id}/file')
def download(record_id: str):
    path = service.file_for_download(record_id)
    return FileResponse(path, media_type='application/pdf', filename=path.name,
                        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@router.post('/{record_id}/revoke')
def revoke(record_id: str, body: Revocation, user: dict = Depends(require_admin)):
    return service.revoke_record(record_id, user['user_id'], body.expected_version, body.reason)
