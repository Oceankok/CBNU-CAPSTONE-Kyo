"""Media publication and deletion use the same DB lock as retention decisions."""

import os
import json
import logging
import tempfile
import time
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.db.event_repository import get_connection
from backend.media.paths import PROCESSED_ROOT, WORK_ROOT, SOURCE_ROOT, private_source_path, owned_path, storage_key
from backend.media.redaction import MediaError, redact_image, redact_video


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reprocess_unavailable_reason(row: dict, decision: str = "pending") -> str | None:
    if decision == "delete" or row["status"] in {"delete_pending", "deleted"}:
        return "deletion_requested"
    if row["status"] == "processing" or row.get("processing_started_at") is not None:
        return "processing_in_progress"
    if failure_category(row.get("error_code")) == "source_invalid":
        return "source_invalid"
    if row.get("source_path"):
        if row.get("source_expires_at") is not None and row["source_expires_at"] <= time.time():
            return "source_expired"
        try:
            return None if private_source_path(row["source_path"]).is_file() else "source_file_missing"
        except (ValueError, OSError):
            return "unsafe_source_path"
    # Pre-migration unverified files can be imported once. Never re-redact a published output.
    if row["redaction_status"] == "legacy_unverified" and row.get("storage_path"):
        try:
            return None if owned_path(row["storage_path"]).is_file() else "source_file_missing"
        except (ValueError, OSError):
            return "unsafe_storage_path"
    return "source_file_missing"


def failure_category(code: str | None) -> str | None:
    if not code:
        return None
    if code in {"source_file_missing", "source_expired"}:
        return "source_unavailable"
    if code in {"invalid_frame", "video_decode_failed", "incomplete_video_decode", "invalid_video_fps", "video_resolution_changed"}:
        return "source_invalid"
    if code in {"file_delete_failed", "private_cleanup_pending"}:
        return "cleanup_failed"
    return "redaction_failed"


def public_media(row: dict, decision: str = "pending") -> dict:
    result = dict(row)
    path = result.pop("storage_path", "")
    result.pop("source_node_id", None)
    result.pop("request_id", None)
    result.pop("processing_started_at", None)
    result.pop("source_path", None)
    safe = False
    try:
        safe = owned_path(path).is_file()
    except (ValueError, OSError):
        pass
    result["url"] = "/" + path if safe and row["status"] == "ready" and row["redaction_status"] == "complete" and decision != "delete" else None
    reason = reprocess_unavailable_reason(row, decision)
    result["can_reprocess"] = reason is None
    result["reprocess_unavailable_reason"] = reason
    result["failure_category"] = failure_category(row.get("error_code"))
    return result


def review_availability(media: list[dict], requests: list[dict]) -> dict:
    available = [item for item in media if item.get("url")]
    latest = {}
    for request in requests:
        previous = latest.get(request["camera_id"])
        if previous is None or request["created_at"] > previous["created_at"]:
            latest[request["camera_id"]] = request
    waiting = any(item["status"] in {"pending", "failed"} for item in latest.values())
    processing = any(item["status"] == "processing" for item in media)
    retryable = any(item.get("can_reprocess") for item in media)
    if available:
        state = "available"
    elif waiting:
        state = "awaiting_clip"
    elif processing:
        state = "processing"
    elif retryable:
        state = "retryable_failure"
    else:
        state = "no_usable_media"
    return {"state": state, "can_review": bool(available),
            "can_mark_unreviewable": not (available or waiting or processing or retryable),
            "missing_media_count": sum(not item.get("url") for item in media)}


def validate_review(conn, review: dict):
    """Called inside the review write transaction, not only in the UI."""
    result = review["review_result"]
    if result not in {"confirmed", "false_positive", "hold", "unreviewable"}:
        raise ValueError("invalid_review_result")
    if result == "hold":
        return
    retention = conn.execute("SELECT decision FROM event_retention WHERE event_id=?", (review["event_id"],)).fetchone()
    media = [public_media(dict(row), retention[0] if retention else "pending") for row in conn.execute("SELECT * FROM event_media WHERE event_id=?", (review["event_id"],))]
    requests = [dict(row) for row in conn.execute("SELECT * FROM event_clip_request WHERE event_id=?", (review["event_id"],))]
    availability = review_availability(media, requests)
    if result in {"confirmed", "false_positive"} and not availability["can_review"]:
        raise ValueError("usable_redacted_media_required")
    if result == "unreviewable":
        if not availability["can_mark_unreviewable"]:
            raise ValueError("media_recovery_pending")
        if review.get("review_reason_code") not in {"source_missing", "source_corrupt", "clip_unavailable"} or not review.get("review_comment", "").strip():
            raise ValueError("unreviewable_reason_required")
        if review.get("confirmed_violation") or review.get("second_review_needed"):
            raise ValueError("invalid_unreviewable_flags")


def _preserve_source(media_id: str, source=None, frame=None, consume_source: bool = False) -> Path:
    """Persist raw input privately before inference so failures remain retryable."""
    import cv2
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    suffix = ".png" if frame is not None else Path(source).suffix
    key = media_id + suffix
    target = private_source_path(key)
    ttl = int(os.environ.get("PPE_MEDIA_SOURCE_TTL_SECONDS", "86400"))
    if ttl <= 0:
        raise ValueError("invalid_source_ttl")
    try:
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT m.status,r.decision FROM event_media m LEFT JOIN event_retention r ON r.event_id=m.event_id WHERE media_id=?", (media_id,)).fetchone()
            if not row or row["status"] != "processing" or row["decision"] == "delete":
                raise ValueError("event_media_deleted")
            if frame is not None:
                if not cv2.imwrite(str(target), frame):
                    raise MediaError("image_encode_failed")
            elif consume_source:
                if not Path(source).is_file():
                    raise MediaError("source_file_missing")
                os.replace(source, target)
            else:
                if not Path(source).is_file():
                    raise MediaError("source_file_missing")
                shutil.copyfile(source, target)
            conn.execute("UPDATE event_media SET source_path=?,source_expires_at=? WHERE media_id=?", (key, time.time() + ttl, media_id))
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


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
            source = _preserve_source(media_id, source, frame, consume_source)
            output = work / ("output.jpg" if kind == "image" else "output.mp4")
            if kind == "image":
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
        retention = conn.execute("SELECT decision FROM event_retention WHERE event_id=?", (row["event_id"],)).fetchone()
        if retention and retention["decision"] == "delete":
            raise ValueError("event_media_deleted")
        reason = reprocess_unavailable_reason(dict(row), retention["decision"] if retention else "pending")
        if reason:
            raise ValueError(reason)
        source = private_source_path(row["source_path"]) if row["source_path"] else owned_path(row["storage_path"])
        legacy = not row["source_path"]
        conn.execute("UPDATE event_media SET status='processing',redaction_status='unprocessed',processing_started_at=?,error_code=NULL,redaction_mode=?,inference_backend=NULL,processing_seconds=NULL WHERE media_id=?",
                     (time.time(), mode, media_id))
    work = None
    try:
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"ppe_{media_id}_", dir=WORK_ROOT) as folder:
            work = Path(folder)
            private_source = source
            if legacy:
                private_source = _preserve_source(media_id, source=source, consume_source=True)
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
            if row["source_path"]:
                private_source_path(row["source_path"]).unlink(missing_ok=True)
            with get_connection() as conn:
                conn.execute("""UPDATE event_media SET status='deleted',storage_path='',source_path='',source_expires_at=NULL,deleted_at=?,error_code=NULL
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
        if decision == "retain" and review["review_result"] == "unreviewable":
            raise ValueError("unreviewable_media_cannot_be_retained")
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
