"""Private consent documents; administrator confirmation is not signature verification."""
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
from uuid import uuid4

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.media.paths import WORK_ROOT
from backend.media.service import _request_delete, delete_event_media, now


CONSENT_ROOT = WORK_ROOT / 'consents'
MAX_BYTES = 10 * 1024 * 1024


class ConsentError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.status = status


def document_path(file_key):
    if not re.fullmatch(r'CONSENT_[0-9a-f]{32}\.pdf', file_key):
        raise ConsentError('consent_file_unavailable', 404)
    root = CONSENT_ROOT.resolve()
    path = root / file_key
    if path.is_symlink() or path.resolve().parent != root:
        raise ConsentError('consent_file_unavailable', 404)
    return path


def _public(row):
    return {key: value for key, value in dict(row).items() if key != 'file_key'}


def get_record(record_id):
    with get_connection() as conn:
        row = conn.execute('SELECT * FROM consent_record WHERE record_id=?', (record_id,)).fetchone()
    if not row:
        raise ConsentError('consent_record_not_found', 404)
    return _public(row)


def list_records(site_code=None, limit=50, offset=0):
    with get_connection() as conn:
        if site_code:
            rows = conn.execute('SELECT * FROM consent_record WHERE site_code=? ORDER BY created_at DESC,record_id LIMIT ? OFFSET ?', (site_code, limit, offset)).fetchall()
        else:
            rows = conn.execute('SELECT * FROM consent_record ORDER BY created_at DESC,record_id LIMIT ? OFFSET ?', (limit, offset)).fetchall()
    return [_public(row) for row in rows]


def create_record(file, metadata, actor):
    CONSENT_ROOT.mkdir(parents=True, exist_ok=True)
    record_id = 'CONSENT_' + uuid4().hex
    file_key = record_id + '.pdf'
    destination = document_path(file_key)
    published = False
    try:
        with tempfile.NamedTemporaryFile(prefix='consent_', suffix='.tmp', dir=CONSENT_ROOT, delete=False) as stage:
            stage_path = Path(stage.name)
            size = 0
            sha = hashlib.sha256()
            try:
                while chunk := file.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ConsentError('consent_file_too_large', 413)
                    sha.update(chunk)
                    stage.write(chunk)
            except Exception:
                stage.close()
                stage_path.unlink(missing_ok=True)
                raise
        try:
            if size == 0:
                raise ConsentError('empty_consent_file', 400)
            with stage_path.open('rb') as stream:
                if stream.read(5) != b'%PDF-':
                    raise ConsentError('invalid_consent_pdf', 400)
                stream.seek(0)
                try:
                    reader = PdfReader(stream, strict=True)
                    if reader.is_encrypted:
                        raise ConsentError('encrypted_consent_pdf', 400)
                    pages = len(reader.pages)
                    if not 1 <= pages <= 100:
                        raise ConsentError('invalid_consent_page_count', 400)
                    for page in reader.pages:
                        if float(page.mediabox.width) <= 0 or float(page.mediabox.height) <= 0:
                            raise ConsentError('invalid_consent_pdf', 400)
                except (PdfReadError, KeyError, TypeError, IndexError, RecursionError, OverflowError, ValueError) as exc:
                    if isinstance(exc, ConsentError):
                        raise
                    raise ConsentError('invalid_consent_pdf', 400) from exc
            with get_connection() as conn:
                begin_write(conn)
                conn.execute('''INSERT INTO consent_record(record_id,site_code,scope_description,form_version,
                    file_key,sha256,byte_size,page_count,confirmed_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (record_id, metadata.site_code, metadata.scope_description, metadata.form_version,
                     file_key, sha.hexdigest(), size, pages, actor, now()))
                os.replace(stage_path, destination)
                published = True
        finally:
            stage_path.unlink(missing_ok=True)
    except Exception:
        if published:
            destination.unlink(missing_ok=True)
        raise
    return get_record(record_id)


def verified_file(row):
    path = document_path(row['file_key'])
    try:
        with path.open('rb') as stream:
            if path.stat().st_size != row['byte_size'] or hashlib.file_digest(stream, 'sha256').hexdigest() != row['sha256']:
                raise ConsentError('consent_file_integrity_failed')
    except OSError as exc:
        raise ConsentError('consent_file_unavailable', 404) from exc
    return path


def file_for_download(record_id):
    with get_connection() as conn:
        row = conn.execute('SELECT * FROM consent_record WHERE record_id=?', (record_id,)).fetchone()
        if not row:
            raise ConsentError('consent_record_not_found', 404)
        return verified_file(row)


def link_retention(conn, event_id, record_id, actor):
    row = conn.execute('SELECT * FROM consent_record WHERE record_id=?', (record_id,)).fetchone()
    if not row:
        raise ConsentError('consent_record_not_found', 404)
    if row['status'] != 'active':
        raise ConsentError('consent_record_revoked')
    verified_file(row)
    conn.execute('''INSERT INTO event_consent(event_id,record_id,linked_by,linked_at) VALUES(?,?,?,?)
        ON CONFLICT(event_id) DO UPDATE SET record_id=excluded.record_id,linked_by=excluded.linked_by,linked_at=excluded.linked_at''',
        (event_id, record_id, actor, now()))
    return record_id


def revoke_record(record_id, actor, expected_version, reason):
    with get_connection() as conn:
        begin_write(conn)
        row = conn.execute('SELECT * FROM consent_record WHERE record_id=?', (record_id,)).fetchone()
        if not row:
            raise ConsentError('consent_record_not_found', 404)
        if row['status'] == 'revoked':
            return get_record(record_id)
        if row['version'] != expected_version:
            raise ConsentError('consent_version_conflict')
        conn.execute("UPDATE consent_record SET status='revoked',revoked_by=?,revoked_at=?,revocation_reason=?,version=version+1 WHERE record_id=?",
                     (actor, now(), reason, record_id))
        events = conn.execute("SELECT e.event_id FROM event_consent e JOIN event_retention r ON r.event_id=e.event_id WHERE e.record_id=? AND r.decision='retain'", (record_id,)).fetchall()
        for event in events:
            _request_delete(conn, event['event_id'], actor)
    for event in events:
        delete_event_media(event['event_id'])
    return get_record(record_id)


def cleanup(stale_after=3600):
    """Only abandoned uploads; registered evidence survives event media deletion."""
    if not CONSENT_ROOT.exists():
        return 0
    with get_connection() as conn:
        referenced = {row[0] for row in conn.execute('SELECT file_key FROM consent_record')}
    cutoff = time.time() - stale_after
    cleaned = 0
    for path in CONSENT_ROOT.iterdir():
        managed = re.fullmatch(r'consent_[A-Za-z0-9_-]+\.tmp|CONSENT_[0-9a-f]{32}\.pdf', path.name)
        try:
            if managed and path.name not in referenced and path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                path.unlink()
                cleaned += 1
        except OSError:
            pass  # Open files are retried on a later maintenance pass.
    return cleaned
