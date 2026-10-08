import os
import tempfile
from pathlib import Path
from typing import Literal
from datetime import datetime
import time
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from backend.auth.service import require_admin
from backend.db.event_repository import get_candidate_event_by_id, get_connection
from backend.media.paths import WORK_ROOT
from backend.media.service import decide_retention, delete_event_media, get_retention, ingest_file, reprocess_media

router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)], tags=["event media"])


def state_error(exc: ValueError):
    code = str(exc)
    status = 404 if code.endswith("not_found") or code == "source_file_missing" else 409
    if code.startswith("invalid_") or code == "consent_confirmation_required" or code == "unsafe_storage_path" or code == "retention_expiry_must_be_future":
        status = 400
    raise HTTPException(status, code) from exc


@router.get("/events/{event_id}/media")
def list_media(event_id: str):
    event = get_candidate_event_by_id(event_id)
    if not event:
        raise HTTPException(404, "Event not found")
    return {"items": event["media"], "retention": get_retention(event_id),
            "review_availability": event["review_availability"], "clip_requests": event["clip_requests"]}


class ClipRequestRegistration(BaseModel):
    camera_id: str


@router.post("/events/{event_id}/clip-requests", status_code=201)
def register_clip_request(event_id: str, body: ClipRequestRegistration):
    """Record delivery progress; transport dispatch is owned by the field integration."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM candidate_event WHERE event_id=?", (event_id,)).fetchone():
            raise HTTPException(404, "event_not_found")
        if not conn.execute("SELECT 1 FROM camera_info WHERE camera_id=?", (body.camera_id,)).fetchone():
            raise HTTPException(404, "camera_not_found")
        if conn.execute("SELECT 1 FROM event_retention WHERE event_id=? AND decision='delete'", (event_id,)).fetchone():
            raise HTTPException(409, "event_media_deleted")
        pending = conn.execute("SELECT * FROM event_clip_request WHERE event_id=? AND camera_id=? AND status='pending'", (event_id, body.camera_id)).fetchone()
        if pending:
            return dict(pending)
        request_id = str(uuid4())
        timestamp = time.time()
        conn.execute("INSERT INTO event_clip_request(request_id,event_id,camera_id,created_at,updated_at) VALUES(?,?,?,?,?)", (request_id,event_id,body.camera_id,timestamp,timestamp))
    return {"request_id": request_id, "event_id": event_id, "camera_id": body.camera_id, "status": "pending"}


class ClipRequestResult(BaseModel):
    status: Literal["received", "failed", "unavailable"]
    error_code: str = Field(default="", max_length=200)
    media_id: str | None = None


@router.put("/events/{event_id}/clip-requests/{request_id}")
def report_clip_request(event_id: str, request_id: str, body: ClipRequestResult):
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM event_clip_request WHERE request_id=? AND event_id=?", (request_id,event_id)).fetchone()
        if not row:
            raise HTTPException(404, "clip_request_not_found")
        if row["status"] != "pending" and not (row["status"] == "failed" and body.status == "unavailable"):
            raise HTTPException(409, "clip_request_already_finished")
        if body.status != "received" and not body.error_code.strip():
            raise HTTPException(400, "clip_failure_reason_required")
        if body.status == "received":
            media = conn.execute("SELECT * FROM event_media WHERE media_id=? AND event_id=? AND camera_id=?", (body.media_id,event_id,row["camera_id"])).fetchone()
            if not media or not (media["source_path"] or media["storage_path"]) or media["request_id"] not in {None, request_id}:
                raise HTTPException(409, "received_media_required")
            if conn.execute("SELECT 1 FROM event_media WHERE request_id=? AND media_id!=?", (request_id,body.media_id)).fetchone():
                raise HTTPException(409, "clip_request_media_conflict")
            conn.execute("UPDATE event_media SET request_id=? WHERE media_id=?", (request_id,body.media_id))
        conn.execute("UPDATE event_clip_request SET status=?,error_code=?,updated_at=? WHERE request_id=?", (body.status,body.error_code or None,time.time(),request_id))
    return {"request_id":request_id,"status":body.status,"error_code":body.error_code or None}


class RetentionRequest(BaseModel):
    decision: Literal["retain", "delete"]
    expected_version: int = Field(ge=0)
    consent_confirmed: bool = False
    consent_reference: str = Field(default="", max_length=500)
    retain_until: datetime | None = None


@router.put("/events/{event_id}/retention")
def save_retention(event_id: str, body: RetentionRequest, user: dict = Depends(require_admin)):
    if body.retain_until and body.retain_until.tzinfo is None:
        raise HTTPException(400, "retain_until must include a timezone")
    try:
        return decide_retention(event_id, body.decision, user["user_id"], body.expected_version,
                                body.consent_confirmed, body.consent_reference,
                                body.retain_until.timestamp() if body.retain_until else None)
    except ValueError as exc:
        state_error(exc)


@router.post("/events/{event_id}/retention/retry")
def retry_deletion(event_id: str):
    try:
        return delete_event_media(event_id)
    except ValueError as exc:
        state_error(exc)


@router.post("/media/{media_id}/redact")
async def redact_existing(media_id: str, redaction_mode: Literal["enhanced", "scrfd"] = "scrfd"):
    try:
        return await run_in_threadpool(reprocess_media, media_id, redaction_mode)
    except ValueError as exc:
        state_error(exc)


@router.post("/events/{event_id}/media", status_code=201)
async def upload_media(event_id: str, request: Request, kind: Literal["image", "video"],
                       camera_id: str | None = None, role: Literal["thumbnail", "clip", "reference"] = "reference",
                       redaction_mode: Literal["enhanced", "scrfd"] = "scrfd"):
    """Raw request body; originals are retained privately until the short retry TTL expires."""
    event = get_candidate_event_by_id(event_id)
    if not event:
        raise HTTPException(404, "Event not found")
    if get_retention(event_id)["decision"] == "delete":
        raise HTTPException(409, "event_media_deleted")
    if (role == "thumbnail" and kind != "image") or (role == "clip" and kind != "video"):
        raise HTTPException(400, "invalid_media_role")
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    limit = int(os.environ.get("PPE_MEDIA_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
    with tempfile.TemporaryDirectory(prefix="ppe_", dir=WORK_ROOT) as folder:
        source = Path(folder) / ("upload.jpg" if kind == "image" else "upload.mp4")
        size = 0
        with source.open("wb") as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "media_upload_too_large")
                stream.write(chunk)
        if not size:
            raise HTTPException(400, "empty_media_upload")
        try:
            return await run_in_threadpool(ingest_file, event_id, camera_id or event["camera_id"], kind, role, source, True, redaction_mode)
        except ValueError as exc:
            state_error(exc)
