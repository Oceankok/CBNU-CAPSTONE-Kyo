from backend.db.connection import begin_write
"""Private evidence lifecycle and atomic administrator confirmation."""

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import cv2
import numpy as np
from fastapi import HTTPException
from pydantic import ValidationError

from backend.db.event_repository import get_connection
from backend.equipment import provider
from backend.equipment.routes import check_zone, find_camera, now, serialize
from backend.media.paths import WORK_ROOT
from backend.media.redaction import redact_frame

EVIDENCE_ROOT = WORK_ROOT / "equipment"
EVIDENCE_SECONDS = 86400
MAX_UPLOAD = 10 * 1024 * 1024


def evidence_path(key: str) -> Path:
    # Only server-generated direct children are accepted, even for damaged DB rows.
    if not key or Path(key).name != key or len(key) != 36 or not key.endswith(".jpg"):
        raise ValueError("unsafe_evidence_path")
    if any(char not in "0123456789abcdef" for char in key[:-4]):
        raise ValueError("unsafe_evidence_path")
    root = EVIDENCE_ROOT.resolve()
    target = (root / key).resolve()
    if target.parent != root:
        raise ValueError("unsafe_evidence_path")
    return target


def find(conn, recognition_id):
    row = conn.execute("SELECT * FROM equipment_recognition WHERE recognition_id=?", (recognition_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "recognition_not_found")
    return row


def evidence_available(row):
    if not row["evidence_path"] or not row["evidence_expires_at"] or row["evidence_expires_at"] <= time.time():
        return False
    try:
        return evidence_path(row["evidence_path"]).is_file()
    except (ValueError, OSError):
        return False


def public(row):
    from datetime import datetime, timezone
    expires = row["evidence_expires_at"]
    return {"recognition_id": row["recognition_id"], "camera_id": row["camera_id"],
            "status": row["status"], "candidates": json.loads(row["candidates_json"]),
            "evidence_url": f"/api/equipment/recognitions/{row['recognition_id']}/image" if evidence_available(row) else None,
            "evidence_expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat() if expires else None,
            "equipment_id": row["equipment_id"], "error_code": row["error_code"], "created_at": row["created_at"]}


def analyze(data: bytes, content_type: str, camera_id: str | None, actor: str):
    if camera_id is not None:
        with get_connection() as conn:
            find_camera(conn, camera_id)
    signature_ok = data.startswith(b"\xff\xd8\xff") if content_type == "image/jpeg" else data.startswith(b"\x89PNG\r\n\x1a\n")
    if not signature_ok:
        raise HTTPException(422, "source_invalid")
    try:
        frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error:
        raise HTTPException(422, "source_invalid")
    if frame is None or frame.shape[0] * frame.shape[1] > 16_000_000:
        raise HTTPException(422, "source_invalid")
    recognition_id = str(uuid4())
    key, expires, candidates, error = "", None, [], None
    adapter = provider.get_provider()
    if adapter is None:
        error = "provider_not_configured"
    else:
        EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
        stage = "redaction"
        try:
            with TemporaryDirectory(prefix="recognition_", dir=EVIDENCE_ROOT) as directory:
                output = Path(directory) / "redacted.jpg"
                redact_frame(frame, output, mode="scrfd")
                redacted = output.read_bytes()
                # Only this completed redaction output may cross the provider boundary.
                stage = "provider"
                result = provider.ProviderResult.model_validate(adapter.analyze(redacted, timeout_seconds=30))
                candidates = [{"candidate_id": str(uuid4()), **item.model_dump(),
                               "confidence_kind": "provider_estimate" if item.confidence is not None else "not_available"}
                              for item in result.candidates]
                stage = "evidence"
                key = uuid4().hex + ".jpg"
                with evidence_path(key).open("xb") as file:
                    file.write(redacted)
                expires = time.time() + EVIDENCE_SECONDS
        except provider.ProviderError as exc:
            error = exc.code
        except ValidationError:
            error = "provider_response_invalid"
        except Exception:
            error = "redaction_failed" if stage == "redaction" else "provider_unavailable" if stage == "provider" else "evidence_store_failed"
        if error:
            candidates = []
            # An unpublished output is reclaimed by maintenance if removal fails.
            if key:
                try:
                    evidence_path(key).unlink(missing_ok=True)
                except OSError:
                    pass
            key, expires = "", None
    try:
        with get_connection() as conn:
            conn.execute("INSERT INTO equipment_recognition(recognition_id,camera_id,status,candidates_json,evidence_path,evidence_expires_at,error_code,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
                         (recognition_id, camera_id, "failed" if error else "analyzed", json.dumps(candidates, ensure_ascii=False), key, expires, error, now(), actor))
            return public(find(conn, recognition_id))
    except Exception:
        if key:
            try:
                evidence_path(key).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def confirm(recognition_id, body, actor):
    with get_connection() as conn:
        begin_write(conn)
        row = find(conn, recognition_id)
        if row["status"] == "confirmed":
            raise HTTPException(409, "recognition_already_confirmed")
        if row["status"] != "analyzed":
            raise HTTPException(409, "recognition_not_confirmable")
        if not evidence_available(row):
            raise HTTPException(409, "recognition_evidence_expired")
        if body.candidate_id is not None and body.candidate_id not in {item["candidate_id"] for item in json.loads(row["candidates_json"])}:
            raise HTTPException(422, "candidate_not_in_recognition")
        check_zone(conn, body.zone_name)
        equipment_id, timestamp = str(uuid4()), now()
        conn.execute("INSERT INTO equipment(equipment_id,name,equipment_type,zone_name,required_ppe,status,created_at,updated_at) VALUES(?,?,?,?,?,'active',?,?)",
                     (equipment_id, body.name, body.equipment_type, body.zone_name, json.dumps(body.required_ppe), timestamp, timestamp))
        conn.execute("UPDATE equipment_recognition SET status='confirmed',equipment_id=?,decided_at=?,decided_by=? WHERE recognition_id=?",
                     (equipment_id, timestamp, actor, recognition_id))
        return serialize(conn.execute("SELECT * FROM equipment WHERE equipment_id=?", (equipment_id,)).fetchone())


def cleanup(stale_after=3600):
    import shutil
    cleaned = 0
    with get_connection() as conn:
        begin_write(conn)
        for row in conn.execute("SELECT recognition_id,evidence_path FROM equipment_recognition WHERE evidence_path!='' AND evidence_expires_at<=?", (time.time(),)).fetchall():
            try:
                evidence_path(row["evidence_path"]).unlink(missing_ok=True)
            except (ValueError, OSError):
                continue  # Preserve the reference for retry.
            conn.execute("UPDATE equipment_recognition SET evidence_path='' WHERE recognition_id=?", (row["recognition_id"],))
            cleaned += 1
        referenced = {row[0] for row in conn.execute("SELECT evidence_path FROM equipment_recognition WHERE evidence_path!=''")}
    if EVIDENCE_ROOT.exists():
        root = EVIDENCE_ROOT.resolve()
        for file in EVIDENCE_ROOT.iterdir():
            try:
                if file.is_symlink() or file.resolve().parent != root or file.stat().st_mtime >= time.time() - stale_after:
                    continue
                if file.is_file() and file.suffix == ".jpg" and file.name not in referenced:
                    evidence_path(file.name).unlink()
                    cleaned += 1
                elif file.is_dir() and file.name.startswith("recognition_"):
                    shutil.rmtree(file)
                    cleaned += 1
            except (ValueError, OSError):
                pass
    return cleaned
