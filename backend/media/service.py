"""Media publication and deletion use the same DB lock as retention decisions."""

import os
import json
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.db.event_repository import get_connection
from backend.media.paths import PROCESSED_ROOT, WORK_ROOT, owned_path, storage_key
from backend.media.redaction import MediaError, redact_frame, redact_image, redact_video


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def public_media(row: dict, decision: str = "pending") -> dict:
    result = dict(row)
    path = result.pop("storage_path", "")
    result.pop("source_node_id", None)
    result.pop("request_id", None)
    result.pop("processing_started_at", None)
    safe = False
    try:
        owned_path(path)
        safe = True
    except ValueError:
        pass
    result["url"] = "/" + path if safe and row["status"] == "ready" and row["redaction_status"] == "complete" and decision != "delete" else None
    result["can_reprocess"] = bool(safe and path and row["status"] in {"registered", "failed", "missing"} and decision != "delete")
    return result


def get_retention(event_id: str) -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM event_retention WHERE event_id=?", (event_id,)).fetchone()
    return dict(row) if row else {"event_id": event_id, "decision": "pending", "version": 0}


def get_media(media_id: str) -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM event_media WHERE media_id=?", (media_id,)).fetchone()
        if not row:
            raise ValueError("media_not_found")
        retention = conn.execute("SELECT decision FROM event_retention WHERE event_id=?", (row["event_id"],)).fetchone()
    return public_media(dict(row), retention["decision"] if retention else "pending")


def register_media(event_id: str, camera_id: str, kind: str, role: str, mode: str = "scrfd") -> str:
    if mode not in {"enhanced", "scrfd"}:
        raise ValueError("invalid_redaction_mode")
    if kind not in {"image", "video"} or role not in {"thumbnail", "clip", "reference"}:
        raise ValueError("invalid_media_type")
    if (role == "thumbnail" and kind != "image") or (role == "clip" and kind != "video"):
        raise ValueError("invalid_media_role")
    media_id = str(uuid4())
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        event = conn.execute("SELECT timestamp_start,timestamp_end FROM candidate_event WHERE event_id=?", (event_id,)).fetchone()
        if not event:
            raise ValueError("event_not_found")
        if not conn.execute("SELECT 1 FROM camera_info WHERE camera_id=?", (camera_id,)).fetchone():
            raise ValueError("camera_not_found")
        conn.execute("INSERT OR IGNORE INTO event_retention(event_id) VALUES(?)", (event_id,))
        if conn.execute("SELECT decision FROM event_retention WHERE event_id=?", (event_id,)).fetchone()[0] == "delete":
            raise ValueError("event_media_deleted")
        conn.execute("""INSERT INTO event_media (
            media_id,event_id,camera_id,kind,role,status,redaction_status,created_at,processing_started_at,capture_start_at,capture_end_at,redaction_mode)
            VALUES (?,?,?,?,?,'processing','unprocessed',?,?,?,?,?)""",
            (media_id, event_id, camera_id, kind, role, now(), time.time(), event["timestamp_start"], event["timestamp_end"], mode))
    return media_id


def _fail(media_id: str, exc: Exception):
    code = str(exc) if isinstance(exc, MediaError) else "media_processing_failed"
    with get_connection() as conn:
        conn.execute("""UPDATE event_media SET status='failed',redaction_status='failed',error_code=?
            WHERE media_id=? AND status='processing'""", (code, media_id))


def _publish(media_id: str, output: Path, details: dict):
    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)
    target = PROCESSED_ROOT / (media_id + output.suffix)
    moved = False
    try:
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM event_media WHERE media_id=?", (media_id,)).fetchone()
            retention = conn.execute("SELECT decision FROM event_retention WHERE event_id=?", (row["event_id"],)).fetchone()
            if row["status"] != "processing" or retention["decision"] == "delete":
                return
            os.replace(output, target)
            moved = True
            conn.execute("""UPDATE event_media SET storage_path=?,status='ready',redaction_status='complete',
                error_code=NULL,faces_detected=?,processed_frames=?,redaction_mode=?,inference_backend=?,processing_seconds=? WHERE media_id=?""",
                (storage_key(target), details["faces_detected"], details["processed_frames"], details["redaction_mode"],
                 details["inference_backend"], details["processing_seconds"], media_id))
    except Exception:
        if moved:
            target.unlink(missing_ok=True)
        raise


def _process(media_id: str, kind: str, source=None, frame=None, consume_source: bool = False, mode: str = "scrfd") -> dict:
    work = None
    try:
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"ppe_{media_id}_", dir=WORK_ROOT) as folder:
            work = Path(folder)
            if consume_source:
                private_source = work / ("source" + Path(source).suffix)
                os.replace(source, private_source)
                source = private_source
            output = work / ("output.jpg" if kind == "image" else "output.mp4")
            if frame is not None:
                details = redact_frame(frame, output, mode)
            elif kind == "image":
                details = redact_image(Path(source), output, mode)
            else:
                details = redact_video(Path(source), output, work, mode)
            _publish(media_id, output, details)
            logging.getLogger("uvicorn.error").info("media_profile %s", json.dumps({"media_id": media_id, **details}))
    except Exception as exc:
        _fail(media_id, exc)
    _finish_if_clean(media_id, work)
    return get_media(media_id)


def _finish_processing(media_id: str):
    # A deletion is complete only after the processor's private files are gone.
    with get_connection() as conn:
        conn.execute("UPDATE event_media SET processing_started_at=NULL WHERE media_id=?", (media_id,))
        row = conn.execute("""SELECT m.event_id,r.decision FROM event_media m
            LEFT JOIN event_retention r ON r.event_id=m.event_id WHERE m.media_id=?""", (media_id,)).fetchone()
    if row and row["decision"] == "delete":
        delete_event_media(row["event_id"])


def _finish_if_clean(media_id: str, work: Path | None):
    if work is not None and work.exists():
        with get_connection() as conn:
            conn.execute("UPDATE event_media SET error_code='private_cleanup_pending' WHERE media_id=?", (media_id,))
    else:
        _finish_processing(media_id)


def ingest_frame(event_id: str, camera_id: str, frame) -> dict:
    media_id = register_media(event_id, camera_id, "image", "thumbnail")
    return _process(media_id, "image", frame=frame)


def ingest_file(event_id: str, camera_id: str, kind: str, role: str, source: Path, consume_source: bool = False, mode: str = "scrfd") -> dict:
    media_id = register_media(event_id, camera_id, kind, role, mode)
    return _process(media_id, kind, source=source, consume_source=consume_source, mode=mode)


def reprocess_media(media_id: str, mode: str = "scrfd") -> dict:
    if mode not in {"enhanced", "scrfd"}:
        raise ValueError("invalid_redaction_mode")
    # Only app-owned legacy material is moved. Arbitrary user input is never removed.
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM event_media WHERE media_id=?", (media_id,)).fetchone()
        if not row:
            raise ValueError("media_not_found")
        if row["status"] not in {"registered", "failed", "missing"}:
            raise ValueError("media_not_reprocessable")
        retention = conn.execute("SELECT decision FROM event_retention WHERE event_id=?", (row["event_id"],)).fetchone()
        if retention and retention["decision"] == "delete":
            raise ValueError("event_media_deleted")
        source = owned_path(row["storage_path"])
        if not source.is_file():
            raise ValueError("source_file_missing")
        conn.execute("UPDATE event_media SET status='processing',redaction_status='unprocessed',processing_started_at=?,error_code=NULL,redaction_mode=?,inference_backend=NULL,processing_seconds=NULL WHERE media_id=?",
                     (time.time(), mode, media_id))
    work = None
    try:
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"ppe_{media_id}_", dir=WORK_ROOT) as folder:
            work = Path(folder)
            private_source = work / ("source" + source.suffix)
            os.replace(source, private_source)
            with get_connection() as conn:
                conn.execute("UPDATE event_media SET storage_path='' WHERE media_id=?", (media_id,))
            output = work / ("output.jpg" if row["kind"] == "image" else "output.mp4")
            details = redact_image(private_source, output, mode) if row["kind"] == "image" else redact_video(private_source, output, work, mode)
            _publish(media_id, output, details)
            logging.getLogger("uvicorn.error").info("media_profile %s", json.dumps({"media_id": media_id, **details}))
    except Exception as exc:
        _fail(media_id, exc)
    _finish_if_clean(media_id, work)
    return get_media(media_id)


def delete_event_media(event_id: str) -> dict:
    """Idempotent file removal; DB records remain even on partial failure."""
    with get_connection() as conn:
        retention = conn.execute("SELECT decision FROM event_retention WHERE event_id=?", (event_id,)).fetchone()
        if not retention or retention["decision"] != "delete":
            raise ValueError("deletion_not_requested")
        rows = conn.execute("SELECT * FROM event_media WHERE event_id=? AND status='delete_pending'", (event_id,)).fetchall()
    for row in rows:
        if row["processing_started_at"] is not None:
            continue
        try:
            if row["storage_path"]:
                owned_path(row["storage_path"]).unlink(missing_ok=True)
            with get_connection() as conn:
                conn.execute("""UPDATE event_media SET status='deleted',storage_path='',deleted_at=?,error_code=NULL
                    WHERE media_id=? AND status='delete_pending'""", (now(), row["media_id"]))
        except (OSError, ValueError):
            with get_connection() as conn:
                conn.execute("UPDATE event_media SET error_code='file_delete_failed' WHERE media_id=? AND status='delete_pending'", (row["media_id"],))
    with get_connection() as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM event_media WHERE event_id=? AND status!='deleted'", (event_id,)).fetchone()[0]
        if not remaining:
            conn.execute("UPDATE event_retention SET deleted_at=COALESCE(deleted_at,?) WHERE event_id=?", (now(), event_id))
            conn.execute("UPDATE candidate_event SET thumbnail_path='',video_clip_path='' WHERE event_id=?", (event_id,))
    return {**get_retention(event_id), "pending_media": remaining}


def _request_delete(conn, event_id: str, actor: str):
    conn.execute("""UPDATE event_retention SET decision='delete',consent_confirmed=0,
        decided_by=?,decided_at=?,version=version+1 WHERE event_id=?""", (actor, now(), event_id))
    conn.execute("UPDATE event_media SET status='delete_pending' WHERE event_id=? AND status!='deleted'", (event_id,))
    conn.execute("INSERT INTO event_retention_history(history_id,event_id,decision,decided_by,decided_at) VALUES(?,?,'delete',?,?)",
                 (str(uuid4()), event_id, actor, now()))


def decide_retention(event_id: str, decision: str, actor: str, expected_version: int,
                     consent_confirmed: bool = False, consent_reference: str = "", retain_until: float | None = None) -> dict:
    if decision not in {"retain", "delete"}:
        raise ValueError("invalid_retention_decision")
    if decision == "retain" and (not consent_confirmed or not consent_reference.strip()):
        raise ValueError("consent_confirmation_required")
    if retain_until is not None and retain_until <= time.time():
        raise ValueError("retention_expiry_must_be_future")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        event = conn.execute("SELECT event_status FROM candidate_event WHERE event_id=?", (event_id,)).fetchone()
        if not event:
            raise ValueError("event_not_found")
        review = conn.execute("SELECT * FROM event_review WHERE event_id=?", (event_id,)).fetchone()
        if not review or event["event_status"] in {"pending", "hold"} or review["second_review_needed"]:
            raise ValueError("final_review_required")
        conn.execute("INSERT OR IGNORE INTO event_retention(event_id) VALUES(?)", (event_id,))
        current = conn.execute("SELECT * FROM event_retention WHERE event_id=?", (event_id,)).fetchone()
        if current["version"] != expected_version:
            raise ValueError("retention_version_conflict")
        if current["decision"] == "delete":
            raise ValueError("deletion_is_irreversible")
        if decision == "delete":
            _request_delete(conn, event_id, actor)
        else:
            conn.execute("""UPDATE event_retention SET decision='retain',consent_confirmed=1,consent_reference=?,
                decided_by=?,decided_at=?,retain_until=?,version=version+1 WHERE event_id=?""",
                (consent_reference.strip(), actor, now(), retain_until, event_id))
            conn.execute("""INSERT INTO event_retention_history(history_id,event_id,decision,decided_by,decided_at,consent_reference)
                VALUES(?,?,'retain',?,?,?)""", (str(uuid4()), event_id, actor, now(), consent_reference.strip()))
    return delete_event_media(event_id) if decision == "delete" else get_retention(event_id)
