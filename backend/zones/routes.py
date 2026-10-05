import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth.service import current_user, require_admin
from backend.db.event_repository import get_connection
from backend.zones.policy import validate_required_ppe

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
    return {"items": [_serialize(row) for row in rows]}


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
        if not _find(conn, zone_name):
            raise HTTPException(404, "Zone rule not found")
        users = conn.execute("SELECT COUNT(*) FROM app_user WHERE zone_name=?", (zone_name,)).fetchone()[0]
        cameras = conn.execute("SELECT COUNT(*) FROM camera_info WHERE zone_name=?", (zone_name,)).fetchone()[0]
        if users or cameras:
            raise HTTPException(409, "Zone is assigned to users or cameras; reassign them before deleting")
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
