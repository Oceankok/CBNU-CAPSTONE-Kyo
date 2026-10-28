from backend.db.connection import begin_write
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from backend.auth.service import require_admin
from backend.db.event_repository import get_connection
from backend.equipment import recognition
from backend.equipment.routes import EquipmentInput, now
from backend.runtime import require_media_processing

router = APIRouter(prefix="/api/equipment/recognitions", dependencies=[Depends(require_admin)], tags=["equipment recognition"])


@router.post("", status_code=201, openapi_extra={"requestBody": {"required": True, "content": {
    mime: {"schema": {"type": "string", "format": "binary"}}
    for mime in ("image/jpeg", "image/png")
}}})
async def analyze_image(request: Request, camera_id: str | None = Query(default=None, min_length=1, max_length=100),
                        user: dict = Depends(require_admin)):
    require_media_processing()
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(415, "unsupported_image_type")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > recognition.MAX_UPLOAD:
            raise HTTPException(413, "image_too_large")
        data.extend(chunk)
    return await run_in_threadpool(recognition.analyze, bytes(data), content_type, camera_id, user["user_id"])


@router.get("")
def list_recognitions(status: Literal["analyzed", "failed", "confirmed", "dismissed"] | None = None):
    with get_connection() as conn:
        query = "SELECT * FROM equipment_recognition"
        args = ()
        if status is not None:
            query += " WHERE status=?"
            args = (status,)
        rows = conn.execute(query + " ORDER BY created_at DESC,recognition_id", args).fetchall()
        return {"items": [recognition.public(row) for row in rows]}


@router.get("/{recognition_id}")
def get_recognition(recognition_id: str):
    with get_connection() as conn:
        return recognition.public(recognition.find(conn, recognition_id))


@router.get("/{recognition_id}/image")
def get_evidence(recognition_id: str):
    with get_connection() as conn:
        begin_write(conn)  # Serialize expiry cleanup against this read.
        row = recognition.find(conn, recognition_id)
        if not recognition.evidence_available(row):
            raise HTTPException(404, "evidence_unavailable")
        try:
            data = recognition.evidence_path(row["evidence_path"]).read_bytes()
        except OSError:
            raise HTTPException(404, "evidence_unavailable")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class ConfirmRequest(EquipmentInput):
    candidate_id: str | None = Field(default=None, min_length=1, max_length=100)
    status: Literal["active"] = "active"


@router.post("/{recognition_id}/confirm")
def confirm_recognition(recognition_id: str, body: ConfirmRequest, user: dict = Depends(require_admin)):
    return recognition.confirm(recognition_id, body, user["user_id"])


@router.post("/{recognition_id}/dismiss")
def dismiss_recognition(recognition_id: str, user: dict = Depends(require_admin)):
    with get_connection() as conn:
        begin_write(conn)
        row = recognition.find(conn, recognition_id)
        if row["status"] == "confirmed":
            raise HTTPException(409, "recognition_already_confirmed")
        if row["status"] != "dismissed":
            conn.execute("UPDATE equipment_recognition SET status='dismissed',decided_at=?,decided_by=? WHERE recognition_id=?", (now(), user["user_id"], recognition_id))
        return recognition.public(recognition.find(conn, recognition_id))
