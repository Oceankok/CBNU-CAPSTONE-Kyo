from backend.db.connection import begin_write
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.auth.service import require_admin
from backend.db.event_repository import get_connection
from backend.zones.policy import combine_required_ppe, validate_required_ppe


router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)], tags=["equipment"])
# This describes the current PPE model interface, not whether inference is running.
DETECTABLE_PPE = ["helmet", "vest"]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class EquipmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    equipment_type: str | None = Field(default=None, max_length=100)
    zone_name: str = Field(min_length=1, max_length=100)
    required_ppe: list[str] = Field(max_length=8)
    status: Literal["active", "inactive"] = "active"

    @field_validator("name", "zone_name")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Value must not be blank")
        return value

    @field_validator("equipment_type")
    @classmethod
    def normalize_type(cls, value):
        return (value.strip() or None) if value is not None else None

    @field_validator("required_ppe")
    @classmethod
    def valid_ppe(cls, value):
        return validate_required_ppe(value)


class EquipmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    equipment_type: str | None = Field(default=None, max_length=100)
    zone_name: str | None = Field(default=None, min_length=1, max_length=100)
    required_ppe: list[str] | None = Field(default=None, max_length=8)
    status: Literal["active", "inactive"] | None = None

    @field_validator("name", "zone_name")
    @classmethod
    def nonblank(cls, value):
        return EquipmentInput.nonblank(value) if value is not None else value

    @field_validator("equipment_type")
    @classmethod
    def normalize_type(cls, value):
        return EquipmentInput.normalize_type(value)

    @field_validator("required_ppe")
    @classmethod
    def valid_ppe(cls, value):
        return validate_required_ppe(value) if value is not None else value


def serialize(row):
    result = dict(row)
    try:
        requirements = json.loads(result["required_ppe"])
        if not isinstance(requirements, list) or not all(isinstance(value, str) for value in requirements):
            raise ValueError("Invalid stored PPE list")
        result["required_ppe"] = validate_required_ppe(requirements)
    except (ValueError, TypeError):
        raise HTTPException(500, "stored_equipment_rule_invalid")
    return result


def find_equipment(conn, equipment_id):
    row = conn.execute("SELECT * FROM equipment WHERE equipment_id=?", (equipment_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "equipment_not_found")
    return row


def check_zone(conn, zone_name):
    if not conn.execute("SELECT 1 FROM zone_rule WHERE zone_name=?", (zone_name,)).fetchone():
        raise HTTPException(422, "zone_not_found")


@router.get("/equipment")
def list_equipment(zone_name: str | None = Query(default=None, max_length=100),
                   status: Literal["active", "inactive"] | None = None):
    clauses, args = [], []
    if zone_name is not None:
        clauses.append("zone_name=?")
        args.append(zone_name)
    if status is not None:
        clauses.append("status=?")
        args.append(status)
    query = "SELECT * FROM equipment"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    with get_connection() as conn:
        rows = conn.execute(query + " ORDER BY created_at,equipment_id", args).fetchall()
    return {"items": [serialize(row) for row in rows]}


@router.get("/equipment/{equipment_id}")
def get_equipment(equipment_id: str):
    with get_connection() as conn:
        return serialize(find_equipment(conn, equipment_id))


@router.post("/equipment", status_code=201)
def create_equipment(body: EquipmentInput):
    equipment_id, timestamp = str(uuid4()), now()
    with get_connection() as conn:
        begin_write(conn)
        check_zone(conn, body.zone_name)
        conn.execute("""INSERT INTO equipment(equipment_id,name,equipment_type,zone_name,
                     required_ppe,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                     (equipment_id,body.name,body.equipment_type,body.zone_name,
                      json.dumps(body.required_ppe),body.status,timestamp,timestamp))
        return serialize(find_equipment(conn, equipment_id))


@router.patch("/equipment/{equipment_id}")
def patch_equipment(equipment_id: str, body: EquipmentPatch):
    changes = body.model_dump(exclude_unset=True)
    if any(value is None for key, value in changes.items() if key != "equipment_type"):
        raise HTTPException(422, "equipment_required_field_null")
    with get_connection() as conn:
        begin_write(conn)
        previous = find_equipment(conn, equipment_id)
        if "zone_name" in changes:
            check_zone(conn, changes["zone_name"])
            if changes["zone_name"] != previous["zone_name"] and conn.execute(
                    "SELECT 1 FROM camera_equipment WHERE equipment_id=?", (equipment_id,)).fetchone():
                raise HTTPException(409, "equipment_has_camera_links")
        if changes:
            if "required_ppe" in changes:
                changes["required_ppe"] = json.dumps(changes["required_ppe"])
            changes["updated_at"] = now()
            conn.execute("UPDATE equipment SET " + ",".join(f"{key}=?" for key in changes) +
                         " WHERE equipment_id=?", [*changes.values(),equipment_id])
        return serialize(find_equipment(conn, equipment_id))


@router.delete("/equipment/{equipment_id}")
def deactivate_equipment(equipment_id: str):
    with get_connection() as conn:
        begin_write(conn)
        row = find_equipment(conn, equipment_id)
        if row["status"] != "inactive":
            conn.execute("UPDATE equipment SET status='inactive',updated_at=? WHERE equipment_id=?", (now(),equipment_id))
        return serialize(find_equipment(conn, equipment_id))


def find_camera(conn, camera_id):
    row = conn.execute("SELECT * FROM camera_info WHERE camera_id=?", (camera_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "camera_not_found")
    return row


def camera_links(conn, camera_id):
    config = conn.execute("SELECT updated_at FROM camera_equipment_config WHERE camera_id=?", (camera_id,)).fetchone()
    ids = [row[0] for row in conn.execute("SELECT equipment_id FROM camera_equipment WHERE camera_id=? ORDER BY sort_order,equipment_id", (camera_id,))]
    return {"camera_id":camera_id,"scope":"camera","configured":config is not None,
            "equipment_ids":ids,"updated_at":config[0] if config else None}


@router.get("/cameras/{camera_id}/equipment")
def get_camera_equipment(camera_id: str):
    with get_connection() as conn:
        conn.execute("BEGIN")
        find_camera(conn, camera_id)
        return camera_links(conn, camera_id)


class CameraEquipmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equipment_ids: list[str] = Field(max_length=100)

    @field_validator("equipment_ids")
    @classmethod
    def unique_ids(cls, value):
        if any(not item or item != item.strip() for item in value) or len(set(value)) != len(value):
            raise ValueError("Equipment IDs must be nonblank and unique")
        return value


@router.put("/cameras/{camera_id}/equipment")
def set_camera_equipment(camera_id: str, body: CameraEquipmentInput):
    with get_connection() as conn:
        begin_write(conn)
        camera = find_camera(conn, camera_id)
        for equipment_id in body.equipment_ids:
            equipment = find_equipment(conn, equipment_id)
            if equipment["zone_name"] != camera["zone_name"]:
                raise HTTPException(409, "equipment_zone_mismatch")
            if equipment["status"] != "active":
                raise HTTPException(409, "equipment_inactive")
        conn.execute("DELETE FROM camera_equipment WHERE camera_id=?", (camera_id,))
        conn.executemany("INSERT INTO camera_equipment(camera_id,equipment_id,sort_order) VALUES(?,?,?)",
                         [(camera_id,equipment_id,index) for index,equipment_id in enumerate(body.equipment_ids)])
        conn.execute("""INSERT INTO camera_equipment_config(camera_id,updated_at) VALUES(?,?)
                     ON CONFLICT(camera_id) DO UPDATE SET updated_at=excluded.updated_at""", (camera_id,now()))
        return camera_links(conn, camera_id)


@router.get("/cameras/{camera_id}/ppe-policy")
def get_camera_policy(camera_id: str):
    with get_connection() as conn:
        conn.execute("BEGIN")  # One snapshot for the camera, rules and equipment mappings.
        camera = find_camera(conn, camera_id)
        links = camera_links(conn, camera_id)
        rule = conn.execute("SELECT required_ppe FROM zone_rule WHERE zone_name=?", (camera["zone_name"],)).fetchone()
        equipment = [serialize(row) for row in conn.execute("""SELECT e.* FROM camera_equipment m
                     JOIN equipment e ON e.equipment_id=m.equipment_id
                     WHERE m.camera_id=? AND e.status='active' ORDER BY m.sort_order,e.equipment_id""", (camera_id,))]
    issues = []
    if not camera["zone_name"]:
        issues.append("camera_zone_unassigned")
    elif rule is None:
        issues.append("zone_rule_missing")
    if not links["configured"]:
        issues.append("equipment_scope_unconfigured")
    if any(item["zone_name"] != camera["zone_name"] for item in equipment):
        issues.append("equipment_zone_mismatch")
    try:
        baseline = json.loads(rule[0]) if rule else None
        if rule:
            if not isinstance(baseline, list) or not all(isinstance(value, str) for value in baseline):
                raise ValueError("Invalid stored zone PPE list")
            baseline = validate_required_ppe(baseline)
    except (TypeError, ValueError):
        raise HTTPException(500, "stored_zone_rule_invalid")
    required = combine_required_ppe(baseline or [], [item["required_ppe"] for item in equipment]) if not issues else None
    return {"camera_id":camera_id,"zone_name":camera["zone_name"],"scope":"camera",
            "configured":not issues,"configuration_issues":issues,"zone_required_ppe":baseline,
            "equipment_requirements":[{key:item[key] for key in ("equipment_id","name","required_ppe")} for item in equipment],
            "required_ppe":required,"detectable_ppe":list(DETECTABLE_PPE),
            "unsupported_ppe":[code for code in required or [] if code not in DETECTABLE_PPE]}
