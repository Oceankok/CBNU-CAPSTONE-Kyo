"""Trusted-worker verification records and post-cleanup private source deletion."""
import json
import os
import time

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal

from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.media.paths import private_source_path
from backend.media.redaction import MediaError


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: Literal['retinaface-resnet50']
    status: Literal['passed']
    passes: int = Field(ge=1, le=3)
    repairs: int = Field(ge=0, le=2)
    frames: int = Field(ge=1, le=100000)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def consistent_passes(self):
        if self.passes != self.repairs + 1:
            raise ValueError('inconsistent_verification_passes')
        return self


def enabled(name):
    value = os.environ.get(name, 'false').lower()
    if value not in {'true', 'false'}:
        raise MediaError('face_verifier_configuration_invalid')
    return value == 'true'


def record(report, frames, digest):
    required = enabled('PPE_MEDIA_REQUIRE_FACE_VERIFICATION') or enabled('PPE_MEDIA_DELETE_VERIFIED_SOURCE')
    if report is None:
        if required:
            raise MediaError('face_verification_required')
        return ''
    try:
        value = VerificationReport.model_validate(report)
    except ValueError as exc:
        raise MediaError('face_verification_failed') from exc
    if value.frames != frames or value.sha256 != digest:
        raise MediaError('face_verification_result_mismatch')
    return json.dumps({**value.model_dump(), 'verified_at': time.time()})


def purge_verified_source(media_id):
    if not enabled('PPE_MEDIA_DELETE_VERIFIED_SOURCE'):
        return False
    with get_connection() as conn:
        begin_write(conn)
        row = conn.execute('SELECT * FROM event_media WHERE media_id=?', (media_id,)).fetchone()
        if (not row or row['status'] != 'ready' or row['redaction_status'] != 'complete'
                or row['processing_started_at'] is not None or not row['source_path']):
            return False
        try:
            report = json.loads(row['verification_json'])
            if not isinstance(report, dict):
                return False
            VerificationReport.model_validate({k: v for k, v in report.items() if k != 'verified_at'})
        except (ValueError, TypeError):
            return False
        if conn.execute("SELECT 1 FROM processing_job WHERE media_id=? AND (status IN ('queued','running') OR local_cleanup_status!='confirmed') LIMIT 1", (media_id,)).fetchone():
            return False
        try:
            private_source_path(row['source_path']).unlink(missing_ok=True)
        except (OSError, ValueError):
            # Leave the reference and retry via housekeeping, without hiding the verified output.
            return False
        conn.execute("UPDATE event_media SET source_path='',source_expires_at=NULL,source_deleted_at=? WHERE media_id=?",
                     (time.time(), media_id))
    return True


def discard_failed_source(media_id, generation, actor, reason):
    """Explicit administrator disposition; never turns a failed result into ready."""
    if not reason.strip():
        raise ValueError('invalid_disposition_reason')
    with get_connection() as conn:
        begin_write(conn)
        row = conn.execute('SELECT * FROM event_media WHERE media_id=?', (media_id,)).fetchone()
        if not row:
            raise ValueError('media_not_found')
        if row['processing_generation'] != generation:
            raise ValueError('processing_generation_conflict')
        if row['status'] != 'failed' or row['error_code'] != 'face_verification_failed' or row['processing_started_at'] is not None:
            raise ValueError('failed_verification_required')
        if conn.execute("SELECT 1 FROM processing_job WHERE media_id=? AND (status IN ('queued','running') OR local_cleanup_status!='confirmed') LIMIT 1", (media_id,)).fetchone():
            raise ValueError('worker_cleanup_required')
        if not row['source_path']:
            return  # Same-generation repeat is harmless; no publication is granted.
        try:
            private_source_path(row['source_path']).unlink(missing_ok=True)
        except (OSError, ValueError) as exc:
            raise ValueError('source_delete_failed') from exc
        decided_at = time.time()
        audit = json.dumps({'status': 'failed', 'disposition': 'source_discarded',
                           'actor': actor, 'decided_at': decided_at, 'reason': reason.strip()})
        conn.execute("UPDATE event_media SET source_path='',source_expires_at=NULL,source_deleted_at=?,verification_json=? WHERE media_id=?",
                     (decided_at, audit, media_id))
