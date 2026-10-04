import hashlib
import json
import sqlite3
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from backend.auth.service import bearer, require_admin
from backend.db.event_repository import get_connection
from backend.field.service import register_node, enqueue_event_broadcast, claim_command, queue_command

admin = APIRouter(prefix="/api", dependencies=[Depends(require_admin)], tags=["field management"])
field = APIRouter(prefix="/api/field", tags=["field node"])


def current_node(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None:
        raise HTTPException(401, "Node token required")
    digest = hashlib.sha256(credentials.credentials.encode()).hexdigest()
    with get_connection() as conn:
        node = conn.execute("SELECT * FROM field_node WHERE token_hash=? AND is_active=1", (digest,)).fetchone()
    if not node:
        raise HTTPException(401, "Invalid node token")
    return dict(node)


class NodeRequest(BaseModel):
    node_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=100)


@admin.post("/nodes", status_code=201)
def create_node(body: NodeRequest):
    try:
        return register_node(body.node_id, body.name)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Node ID already exists")


@admin.get("/nodes")
def list_nodes():
    with get_connection() as conn:
        rows = conn.execute("SELECT node_id,name,is_active,last_seen_at,language,voice_id FROM field_node").fetchall()
    return {"items": [{**dict(r), "online": bool(r["is_active"] and r["last_seen_at"] and time.time()-r["last_seen_at"]<=30)} for r in rows]}


class CameraLink(BaseModel):
    source_node_id: str
    output_node_id: str


@admin.put("/cameras/{camera_id}/node")
def link_camera(camera_id: str, body: CameraLink):
    try:
        with get_connection() as conn:
            conn.execute("""INSERT INTO camera_node(camera_id,source_node_id,output_node_id) VALUES(?,?,?)
                            ON CONFLICT(camera_id) DO UPDATE SET source_node_id=excluded.source_node_id,output_node_id=excluded.output_node_id""",
                         (camera_id, body.source_node_id, body.output_node_id))
    except sqlite3.IntegrityError:
        raise HTTPException(404, "Camera or node not found")
    return {"camera_id": camera_id, **body.model_dump()}


@admin.post("/events/{event_id}/broadcast")
def request_event_broadcast(event_id: str):
    return enqueue_event_broadcast(event_id)


@admin.get("/nodes/{node_id}/commands")
def list_commands(node_id: str):
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM field_command WHERE node_id=? ORDER BY created_at DESC LIMIT 100", (node_id,)).fetchall()
    return {"items": [{**dict(r), "payload": json.loads(r["payload"]), "result": json.loads(r["result"]) if r["result"] else None} for r in rows]}


@field.post("/heartbeat")
def heartbeat(node: dict = Depends(current_node)):
    now = time.time()
    with get_connection() as conn:
        conn.execute("UPDATE field_node SET last_seen_at=? WHERE node_id=?", (now, node["node_id"]))
    return {"node_id": node["node_id"], "server_time": now}


@field.post("/commands/claim")
def claim(node: dict = Depends(current_node)):
    return {"command": claim_command(node["node_id"])}


class CommandResult(BaseModel):
    status: Literal["completed", "failed", "skipped"]
    result: dict = Field(default_factory=dict)


@field.put("/commands/{command_id}/result")
def report(command_id: str, body: CommandResult, node: dict = Depends(current_node)):
    encoded = json.dumps(body.result)
    if len(encoded)>65536:
        raise HTTPException(413, "Result too large")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM field_command WHERE command_id=? AND node_id=?", (command_id, node["node_id"])).fetchone()
        if not row:
            raise HTTPException(404, "Command not found")
        if row["status"] == body.status and row["result"] == encoded:
            return {"status": "ok"}
        if row["status"] not in {"claimed", "unknown"}:
            raise HTTPException(409, "Command is not awaiting a result")
        conn.execute("UPDATE field_command SET status=?,result=?,finished_at=? WHERE command_id=?",
                     (body.status, encoded, time.time(), command_id))
    return {"status": "ok"}
