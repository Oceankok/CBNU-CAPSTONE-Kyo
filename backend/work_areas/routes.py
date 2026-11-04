import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.auth.service import require_admin
from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.equipment.routes import find_camera, now
from backend.processing.routes import processor
from backend.work_areas import service
from backend.work_areas.geometry import validate_points

router = APIRouter(tags=['work areas'])


class AreaInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    equipment_id: str = Field(min_length=1, max_length=100)
    points: list = Field(min_length=3, max_length=32)

    @field_validator('points')
    @classmethod
    def polygon(cls, value):
        return validate_points(value)


class AreaUpdate(AreaInput):
    expected_version: int = Field(ge=1)


class ModeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool
    expected_version: int = Field(ge=0)


@router.get('/api/cameras/{camera_id}/work-areas', dependencies=[Depends(require_admin)])
def list_areas(camera_id: str):
    with get_connection() as conn:
        conn.execute('BEGIN')
        return service.list_areas(conn, camera_id)


@router.put('/api/cameras/{camera_id}/work-area-config', dependencies=[Depends(require_admin)])
def set_mode(camera_id: str, body: ModeInput):
    with get_connection() as conn:
        begin_write(conn)
        find_camera(conn, camera_id)
        if service.config(conn, camera_id)['version'] != body.expected_version:
            raise HTTPException(409, 'work_area_version_conflict')
        if body.enabled and not service.areas(conn, camera_id):
            raise HTTPException(409, 'work_areas_empty')
        for area in service.areas(conn, camera_id) if body.enabled else []:
            service.linked_equipment(conn, camera_id, area['equipment_id'])
        service.touch(conn, camera_id, body.enabled)
        return service.list_areas(conn, camera_id)


@router.post('/api/cameras/{camera_id}/work-areas', status_code=201, dependencies=[Depends(require_admin)])
def create_area(camera_id: str, body: AreaInput):
    ident, timestamp = str(uuid4()), now()
    with get_connection() as conn:
        begin_write(conn)
        service.linked_equipment(conn, camera_id, body.equipment_id)
        if conn.execute('SELECT COUNT(*) FROM camera_work_area WHERE camera_id=?', (camera_id,)).fetchone()[0] >= 32:
            raise HTTPException(409, 'work_area_limit_reached')
        conn.execute('''INSERT INTO camera_work_area(area_id,camera_id,equipment_id,name,points_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)''', (ident, camera_id, body.equipment_id, body.name, json.dumps(body.points), timestamp, timestamp))
        service.touch(conn, camera_id)
        return next(item for item in service.areas(conn, camera_id) if item['area_id'] == ident)


def require_area(conn, camera_id, area_id, expected_version):
    row = conn.execute('SELECT * FROM camera_work_area WHERE area_id=? AND camera_id=?', (area_id, camera_id)).fetchone()
    if not row:
        raise HTTPException(404, 'work_area_not_found')
    if row['version'] != expected_version:
        raise HTTPException(409, 'work_area_version_conflict')
    return row


@router.patch('/api/cameras/{camera_id}/work-areas/{area_id}', dependencies=[Depends(require_admin)])
def update_area(camera_id: str, area_id: str, body: AreaUpdate):
    with get_connection() as conn:
        begin_write(conn)
        require_area(conn, camera_id, area_id, body.expected_version)
        service.linked_equipment(conn, camera_id, body.equipment_id)
        conn.execute('''UPDATE camera_work_area SET equipment_id=?,name=?,points_json=?,version=version+1,updated_at=?
            WHERE area_id=?''', (body.equipment_id, body.name, json.dumps(body.points), now(), area_id))
        service.touch(conn, camera_id)
        return next(item for item in service.areas(conn, camera_id) if item['area_id'] == area_id)


@router.delete('/api/cameras/{camera_id}/work-areas/{area_id}', dependencies=[Depends(require_admin)])
def delete_area(camera_id: str, area_id: str, expected_version: int = Query(ge=1)):
    with get_connection() as conn:
        begin_write(conn)
        require_area(conn, camera_id, area_id, expected_version)
        conn.execute('DELETE FROM camera_work_area WHERE area_id=?', (area_id,))
        service.touch(conn, camera_id)
        # Keep area mode enabled even when empty: fail closed until explicitly changed.
        return service.list_areas(conn, camera_id)


@router.get('/api/cameras/{camera_id}/work-area-policy', dependencies=[Depends(require_admin)])
@router.get('/api/processing/cameras/{camera_id}/work-area-policy', dependencies=[Depends(processor)])
def get_policy(camera_id: str):
    return service.policy(camera_id)
