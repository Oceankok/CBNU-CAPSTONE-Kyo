from uuid import uuid4
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.auth.service import require_admin
from backend.camera_groups import service
from backend.db.connection import begin_write
from backend.db.event_repository import get_connection


router = APIRouter(prefix='/api/camera-groups', tags=['camera groups'], dependencies=[Depends(require_admin)])


class GroupFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    camera_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(min_length=2, max_length=100)


class GroupUpdate(GroupFields):
    expected_version: int = Field(ge=0)


@router.post('', status_code=201)
def create(body: GroupFields, user: dict = Depends(require_admin)):
    group_id = 'GRP_' + uuid4().hex
    stamp = service.now()
    with get_connection() as conn:
        begin_write(conn)
        conn.execute('INSERT INTO camera_group(group_id,name,created_by,created_at,updated_by,updated_at) VALUES(?,?,?,?,?,?)',
                     (group_id, body.name, user['user_id'], stamp, user['user_id'], stamp))
        service.replace_members(conn, group_id, body.camera_ids)
        return service.serialize(conn, group_id)


@router.get('')
def groups(limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    with get_connection() as conn:
        rows = conn.execute('SELECT group_id FROM camera_group ORDER BY name,group_id LIMIT ? OFFSET ?', (limit, offset)).fetchall()
        return {'items': [service.serialize(conn, row['group_id']) for row in rows]}


@router.get('/{group_id}')
def group(group_id: str):
    with get_connection() as conn:
        return service.serialize(conn, group_id)


@router.put('/{group_id}')
def replace(group_id: str, body: GroupUpdate, user: dict = Depends(require_admin)):
    with get_connection() as conn:
        begin_write(conn)
        row = service.find(conn, group_id)
        if row['version'] != body.expected_version:
            raise HTTPException(409, 'camera_group_version_conflict')
        service.replace_members(conn, group_id, body.camera_ids)
        conn.execute('UPDATE camera_group SET name=?,updated_by=?,updated_at=?,version=version+1 WHERE group_id=?',
                     (body.name, user['user_id'], service.now(), group_id))
        return service.serialize(conn, group_id)


@router.delete('/{group_id}')
def delete(group_id: str, expected_version: int = Query(ge=0)):
    with get_connection() as conn:
        begin_write(conn)
        if service.find(conn, group_id)['version'] != expected_version:
            raise HTTPException(409, 'camera_group_version_conflict')
        conn.execute('DELETE FROM camera_group WHERE group_id=?', (group_id,))
    return {'group_id': group_id, 'status': 'deleted'}
