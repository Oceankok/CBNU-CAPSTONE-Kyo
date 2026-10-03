"""Small persistent command queue. No automatic replay of an uncertain broadcast."""
import hashlib
import json
import secrets
import time
from uuid import uuid4

from fastapi import HTTPException

from backend.db.event_repository import get_connection, get_broadcast_settings, get_candidate_event_by_id
from backend.services.warning_broadcast_service import _select_broadcast_message


def register_node(node_id, name):
    token = secrets.token_urlsafe(32)
    with get_connection() as conn:
        conn.execute("INSERT INTO field_node(node_id,name,token_hash) VALUES(?,?,?)",
                     (node_id, name, hashlib.sha256(token.encode()).hexdigest()))
    return {"node_id": node_id, "token": token}


def queue_command(node_id, kind, payload, ttl=30, event_id=None):
    command_id = str(uuid4())
    now = time.time()
    with get_connection() as conn:
        node = conn.execute("SELECT * FROM field_node WHERE node_id=? AND is_active=1", (node_id,)).fetchone()
        if not node:
            raise HTTPException(404, "Active node not found")
        conn.execute("""INSERT INTO field_command(command_id,node_id,kind,payload,event_id,created_at,expires_at)
                        VALUES(?,?,?,?,?,?,?)""",
                     (command_id, node_id, kind, json.dumps(payload), event_id, now, now+ttl))
    return {"command_id": command_id, "status": "pending"}


def enqueue_event_broadcast(event_id):
    event = get_candidate_event_by_id(event_id)
    if not event:
        raise HTTPException(404, "Event not found")
    settings = get_broadcast_settings()
    if not settings.get("enabled"):
        return {"queued": False, "reason": "broadcast_disabled"}
    now = time.time()
    # Serialize duplicate/cooldown checks with insertion across concurrent requests.
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        node = conn.execute("""SELECT n.* FROM field_node n JOIN camera_node c ON c.output_node_id=n.node_id
                               WHERE c.camera_id=? AND n.is_active=1""", (event["camera_id"],)).fetchone()
        if not node:
            return {"queued": False, "reason": "output_node_not_configured"}
        old = conn.execute("SELECT command_id,status FROM field_command WHERE node_id=? AND event_id=? AND kind='broadcast'",
                           (node["node_id"], event_id)).fetchone()
        if old:
            return {"queued": False, "reason": "already_requested", **dict(old)}
        language = node["language"] or settings["default_language"]
        message = _select_broadcast_message(settings["templates"], event["ppe_type"], event["zone_name"] or "", language)
        if not message:
            return {"queued": False, "reason": "template_not_found"}
        if not node["last_seen_at"] or now-node["last_seen_at"] > 30:
            return {"queued": False, "reason": "node_offline"}
        key = json.dumps([node["node_id"], event["ppe_type"], event["zone_name"], language])
        previous = conn.execute("SELECT MAX(created_at) FROM field_command WHERE cooldown_key=?", (key,)).fetchone()[0]
        if previous is not None and now-previous < max(0, settings["cooldown_sec"]):
            return {"queued": False, "reason": "cooldown_active"}
        payload = {"message": message, "language": language, "voice_id": node["voice_id"]}
        command_id = str(uuid4())
        conn.execute("""INSERT INTO field_command(command_id,node_id,kind,payload,event_id,cooldown_key,created_at,expires_at)
                        VALUES(?,?,'broadcast',?,?,?,?,?)""",
                     (command_id, node["node_id"], json.dumps(payload), event_id, key, now, now+30))
    return {"queued": True, "reason": "broadcast_queued", "command_id": command_id, "node_id": node["node_id"]}


def claim_command(node_id):
    now = time.time()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE field_command SET status='expired' WHERE node_id=? AND status='pending' AND expires_at<=?", (node_id, now))
        conn.execute("UPDATE field_command SET status='unknown' WHERE node_id=? AND status='claimed' AND claimed_at<?", (node_id, now-1800))
        row = conn.execute("SELECT * FROM field_command WHERE node_id=? AND status='pending' ORDER BY created_at LIMIT 1", (node_id,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE field_command SET status='claimed',claimed_at=? WHERE command_id=?", (now, row["command_id"]))
    return {"command_id": row["command_id"], "kind": row["kind"], "payload": json.loads(row["payload"]), "expires_at": row["expires_at"]}
