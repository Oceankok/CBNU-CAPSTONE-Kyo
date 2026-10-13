from backend.db.connection import IntegrityError
from backend.db.connection import begin_write
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import sqlite3

from backend.auth.service import PASSWORDS, current_user, require_admin
from backend.db.event_repository import get_connection
from backend.zones.membership import zones_for, replace_user_zones

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_admin)])


def _public(conn, row) -> dict:
    return {"user_id": row["user_id"], "display_name": row["display_name"],
            "role": row["role"], "zone_name": row["zone_name"], "zone_names": zones_for(conn, row["user_id"]),
            "is_active": bool(row["is_active"])}


def _zone_exists(conn, zone_name: str | None) -> bool:
    return zone_name is None or conn.execute(
        "SELECT 1 FROM zone_rule WHERE zone_name=?", (zone_name,)
    ).fetchone() is not None


class UserCreate(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=100)
    role: str
    zone_name: str | None = None
    zone_names: list[str] | None = Field(default=None, max_length=100)
    password: str = Field(min_length=8, max_length=1024)


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    zone_name: str | None = None
    zone_names: list[str] | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=1024)


@router.get("")
def list_users():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT user_id,display_name,role,zone_name,is_active FROM app_user "
            "ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END,user_id"
        ).fetchall()
        return {"items": [_public(conn, row) for row in rows]}


@router.post("", status_code=201)
def add_user(body: UserCreate):
    user_id, display_name = body.user_id.strip(), body.display_name.strip()
    zone_name = body.zone_name.strip() if body.zone_name and body.zone_name.strip() else None
    if not user_id or not display_name:
        raise HTTPException(422, "user_id and display_name must not be blank")
    if body.role not in {"admin", "worker"}:
        raise HTTPException(422, "role must be admin or worker")
    if body.role == "admin" and zone_name is not None:
        raise HTTPException(422, "Administrators cannot be assigned to a worker zone")
    if "zone_names" in body.model_fields_set and "zone_name" in body.model_fields_set:
        raise HTTPException(422, "Use either zone_name or zone_names")
    if "zone_names" in body.model_fields_set and body.zone_names is None:
        raise HTTPException(422, "zone_names must be an array")
    with get_connection() as conn:
        begin_write(conn)
        if not _zone_exists(conn, zone_name):
            raise HTTPException(422, "zone_name must refer to an existing zone rule")
        try:
            conn.execute(
                "INSERT INTO app_user(user_id,password_hash,display_name,role,zone_name) VALUES(?,?,?,?,?)",
                (user_id, PASSWORDS.hash(body.password), display_name, body.role, zone_name),
            )
        except IntegrityError as exc:
            raise HTTPException(409, "user_id already exists") from exc
        replace_user_zones(conn, user_id, body.zone_names if body.zone_names is not None else ([zone_name] if zone_name else []))
        row = conn.execute(
            "SELECT user_id,display_name,role,zone_name,is_active FROM app_user WHERE user_id=?", (user_id,)
        ).fetchone()
        return _public(conn, row)


@router.patch("/{user_id}")
def edit_user(user_id: str, body: UserPatch, actor: dict = Depends(current_user)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "At least one field is required")
    if "zone_names" in changes and "zone_name" in changes:
        raise HTTPException(422, "Use either zone_name or zone_names")
    if "zone_names" in changes and changes["zone_names"] is None:
        raise HTTPException(422, "zone_names must be an array")
    if "display_name" in changes:
        changes["display_name"] = (changes["display_name"] or "").strip()
        if not changes["display_name"]:
            raise HTTPException(422, "display_name must not be blank")
    if "is_active" in changes and changes["is_active"] is None:
        raise HTTPException(422, "is_active must be true or false")
    if "zone_name" in changes and changes["zone_name"] is not None:
        changes["zone_name"] = changes["zone_name"].strip() or None

    with get_connection() as conn:
        begin_write(conn)
        row = conn.execute("SELECT * FROM app_user WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        if changes.get("is_active") is False and user_id == actor["user_id"] and row["is_active"]:
            raise HTTPException(400, "Administrators cannot deactivate their own account")
        if "zone_name" in changes and not _zone_exists(conn, changes["zone_name"]):
            raise HTTPException(422, "zone_name must refer to an existing zone rule")
        if row["role"] == "admin" and changes.get("zone_name"):
            raise HTTPException(422, "Administrators cannot be assigned to a worker zone")
        if "zone_names" in changes:
            replace_user_zones(conn, user_id, changes.pop("zone_names"))
        elif "zone_name" in changes:
            zone = changes.pop("zone_name")
            replace_user_zones(conn, user_id, [zone] if zone else [])
        assignments = [f"{field}=?" for field in changes]
        values = [int(value) if field == "is_active" else value for field, value in changes.items()]
        if changes.get("is_active") is False and row["is_active"]:
            assignments.append("auth_version=auth_version+1")
        if assignments:
            conn.execute(f"UPDATE app_user SET {','.join(assignments)} WHERE user_id=?", (*values, user_id))
        updated = conn.execute(
            "SELECT user_id,display_name,role,zone_name,is_active FROM app_user WHERE user_id=?", (user_id,)
        ).fetchone()
        return _public(conn, updated)


@router.post("/{user_id}/password")
def reset_user_password(user_id: str, body: PasswordReset):
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE app_user SET password_hash=?,auth_version=auth_version+1 WHERE user_id=?",
            (PASSWORDS.hash(body.password), user_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "User not found")
    return {"status": "ok"}
