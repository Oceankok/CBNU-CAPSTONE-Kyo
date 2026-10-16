from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.auth.service import require_admin
from backend.cameras.service import require_camera, public_camera
from backend.db.connection import begin_write
from backend.db.event_repository import get_connection

router = APIRouter(prefix='/api/cameras', tags=['cameras'], dependencies=[Depends(require_admin)])


class CameraFields(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    camera_location: str | None = Field(default=None, max_length=200)
    zone_name: str | None = Field(default=None, min_length=1, max_length=100)
    process_type: str | None = Field(default=None, max_length=100)
    camera_angle_type: str | None = Field(default=None, max_length=100)
    status: Literal['active', 'inactive'] = 'active'


class CameraCreate(CameraFields):
    camera_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')


class CameraPatch(CameraFields):
    pass


def check_zone(conn, value):
    if value is not None and not conn.execute('SELECT 1 FROM zone_rule WHERE zone_name=?', (value,)).fetchone():
        raise HTTPException(422, 'zone_not_found')


@router.post('', status_code=201)
def create_camera(body: CameraCreate):
    with get_connection() as conn:
        begin_write(conn)
        if conn.execute('SELECT 1 FROM camera_info WHERE camera_id=?', (body.camera_id,)).fetchone():
            raise HTTPException(409, 'camera_already_exists')
        check_zone(conn, body.zone_name)
        conn.execute('''INSERT INTO camera_info(camera_id,camera_location,zone_name,process_type,camera_angle_type,status,install_date)
            VALUES(?,?,?,?,?,?,?)''', (body.camera_id, body.camera_location, body.zone_name, body.process_type,
                                      body.camera_angle_type, body.status, datetime.now(timezone.utc).date().isoformat()))
        return public_camera(conn, body.camera_id)


@router.patch('/{camera_id}')
def patch_camera(camera_id: str, body: CameraPatch):
    changes = body.model_dump(exclude_unset=True)
    with get_connection() as conn:
        begin_write(conn)
        previous = require_camera(conn, camera_id)
        if 'zone_name' in changes:
            check_zone(conn, changes['zone_name'])
            if changes['zone_name'] != previous['zone_name'] and conn.execute(
                    'SELECT 1 FROM camera_equipment WHERE camera_id=?', (camera_id,)).fetchone():
                raise HTTPException(409, 'camera_has_equipment_links')
        if changes:
            conn.execute('UPDATE camera_info SET ' + ','.join(f'{key}=?' for key in changes) + ' WHERE camera_id=?',
                         [*changes.values(), camera_id])
        return public_camera(conn, camera_id)


@router.delete('/{camera_id}')
def deactivate_camera(camera_id: str):
    return patch_camera(camera_id, CameraPatch(status='inactive'))
