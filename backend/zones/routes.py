import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth.service import current_user, require_admin
from backend.db.event_repository import get_connection
from backend.zones.policy import validate_required_ppe
from backend.zones.membership import zones_for, sync_legacy

admin = APIRouter(prefix="/api/zones", tags=["zones"], dependencies=[Depends(require_admin)])
worker = APIRouter(prefix="/api/worker", tags=["worker"])


class ZoneRuleRequest(BaseModel):
    required_ppe: list[str] = Field(
        max_length=12, description="구역 전체의 기본 필수 PPE. 장비별 추가 PPE는 별도 설정에서 합산합니다.",
    )
    rules: list[str] = Field(max_length=100, description="작업자에게 표시할 안전 수칙 문구")


def _serialize(row) -> dict:
    try:
        required_ppe, rules = json.loads(row["required_ppe"]), json.loads(row["rules"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(500, "Stored zone rule is invalid") from exc
    return {"zone_name": row["zone_name"], "required_ppe": required_ppe,
            "rules": rules, "updated_at": row["updated_at"]}


def _find(conn, zone_name: str):
    return conn.execute("SELECT * FROM zone_rule WHERE zone_name=?", (zone_name,)).fetchone()


@admin.get("")
def list_zone_rules():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM zone_rule ORDER BY zone_name").fetchall()
        return {"items": [{**_serialize(row), "member_count": conn.execute(
            "SELECT COUNT(*) FROM user_zone WHERE zone_name=?", (row["zone_name"],)).fetchone()[0]} for row in rows]}


class ZoneMembersRequest(BaseModel):
    user_ids: list[str] = Field(max_length=1000)


@admin.put("/{zone_name}/members")
def replace_zone_members(zone_name: str, body: ZoneMembersRequest):
    user_ids = [user_id.strip() for user_id in body.user_ids]
    if any(not user_id or len(user_id) > 100 for user_id in user_ids) or len(set(user_ids)) != len(user_ids):
        raise HTTPException(422, "user_ids must contain distinct nonblank IDs")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _find(conn, zone_name):
            raise HTTPException(404, "Zone rule not found")
        for user_id in user_ids:
            row = conn.execute("SELECT role FROM app_user WHERE user_id=?", (user_id,)).fetchone()
            if row is None or row[0] != "worker":
                raise HTTPException(422, "Members must be existing worker accounts")
        affected = {row[0] for row in conn.execute("SELECT user_id FROM user_zone WHERE zone_name=?", (zone_name,))} | set(user_ids)
        conn.execute("DELETE FROM user_zone WHERE zone_name=?", (zone_name,))
        conn.executemany("INSERT INTO user_zone(user_id,zone_name) VALUES(?,?)", [(user_id, zone_name) for user_id in user_ids])
        for user_id in affected:
            sync_legacy(conn, user_id)
    return {"zone_name": zone_name, "user_ids": sorted(user_ids)}


@admin.put("/{zone_name}")
def save_zone_rule(zone_name: str, body: ZoneRuleRequest, user: dict = Depends(require_admin)):
    zone_name = zone_name.strip()
    if not zone_name or len(zone_name) > 100:
        raise HTTPException(422, "zone_name must contain 1 to 100 characters")
    try:
        required_ppe = validate_required_ppe(body.required_ppe)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    rules = [rule.strip() for rule in body.rules]
    if any(not rule or len(rule) > 500 for rule in rules):
        raise HTTPException(422, "Each rule must contain 1 to 500 characters")
    if len(set(rules)) != len(rules):
        raise HTTPException(422, "rules must not contain duplicates")
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute("""INSERT INTO zone_rule(zone_name,required_ppe,rules,updated_at,updated_by)
            VALUES(?,?,?,?,?) ON CONFLICT(zone_name) DO UPDATE SET
            required_ppe=excluded.required_ppe,rules=excluded.rules,
            updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
            (zone_name, json.dumps(required_ppe, ensure_ascii=False), json.dumps(rules, ensure_ascii=False),
             updated_at, user["user_id"]))
        return _serialize(_find(conn, zone_name))


@admin.delete("/{zone_name}")
def remove_zone_rule(zone_name: str):
    zone_name = zone_name.strip()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _find(conn, zone_name):
            raise HTTPException(404, "Zone rule not found")
        users = conn.execute("SELECT COUNT(*) FROM user_zone WHERE zone_name=?", (zone_name,)).fetchone()[0]
        cameras = conn.execute("SELECT COUNT(*) FROM camera_info WHERE zone_name=?", (zone_name,)).fetchone()[0]
        equipment = conn.execute("SELECT COUNT(*) FROM equipment WHERE zone_name=?", (zone_name,)).fetchone()[0]
        if users or cameras:
            raise HTTPException(409, "Zone is assigned to users or cameras; reassign them before deleting")
        if equipment:
            raise HTTPException(409, "zone_has_equipment")
        conn.execute("DELETE FROM zone_rule WHERE zone_name=?", (zone_name,))
    return {"status": "deleted", "zone_name": zone_name}


@worker.get("/zone")
def get_my_zone(user: dict = Depends(current_user)):
    if not user.get("zone_name"):
        raise HTTPException(404, "No zone is assigned to this account")
    with get_connection() as conn:
        row = _find(conn, user["zone_name"])
    if not row:
        raise HTTPException(404, "No safety rule exists for the assigned zone")
    return _serialize(row)


@worker.get("/zones")
def get_my_zones(user: dict = Depends(current_user)):
    if user["role"] != "worker":
        raise HTTPException(403, "Worker access required")
    with get_connection() as conn:
        rows = conn.execute("""SELECT z.* FROM zone_rule z JOIN user_zone u ON z.zone_name=u.zone_name
            WHERE u.user_id=? ORDER BY z.zone_name""", (user["user_id"],)).fetchall()
    return {"items": [_serialize(row) for row in rows]}


@worker.get("/education")
def get_my_education(user: dict = Depends(current_user)):
    """Expose only education content for the authenticated worker's assigned zone."""
    if user["role"] != "worker":
        raise HTTPException(403, "Worker access required")
    with get_connection() as conn:
        groups = []
        for zone in zones_for(conn, user["user_id"]):
            quarter = conn.execute("SELECT MAX(quarter) FROM education_recommendation WHERE zone_name=?", (zone,)).fetchone()[0]
            rows = conn.execute("""SELECT recommendation_id,ppe_type,zone_name,education_topic
                FROM education_recommendation WHERE zone_name=? AND quarter=?
                ORDER BY recommendation_rank,recommendation_id""", (zone, quarter)).fetchall()
            groups.append({"quarter": quarter, "zone_name": zone,
                           "items": [{**dict(row), "material": None} for row in rows],
                           "empty_reason": None if quarter else "recommendations_unavailable"})
    # Keep the original single-zone fields as the first group's projection.
    first = groups[0] if groups else {"quarter": None, "zone_name": None, "items": [], "empty_reason": "zone_unassigned"}
    return {**first, "groups": groups}
