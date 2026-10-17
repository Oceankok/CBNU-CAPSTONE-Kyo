"""Explicit retained-media snapshots; never archive private originals or credentials."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tarfile
import time
from urllib.parse import urlsplit

from backend.db.connection import begin_write, schema_version
from backend.db.event_repository import get_connection
from backend.db.migrations import LATEST_VERSION
from backend.media.paths import PROCESSED_ROOT, owned_path

MAX_FILES = 1000
MAX_TOTAL = 2 * 1024**3
MAX_FILE = 100 * 1024**2


def valid_expiry(created, expires):
    return all(isinstance(value, (int, float)) and math.isfinite(value) for value in (created, expires)) and created <= time.time() < expires <= created + 86400


def digest(path):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def export_media(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / 'media.tar'
    manifest = directory / 'media.json'
    if archive.exists() or manifest.exists():
        raise ValueError('Snapshot output already exists')
    timestamp = time.time()
    records, total, paths = [], 0, set()
    try:
        with get_connection() as conn, tarfile.open(archive, 'x') as tar:
            begin_write(conn)
            if schema_version(conn) != LATEST_VERSION:
                raise ValueError('Snapshot requires current schema')
            rows = conn.execute('''SELECT m.media_id,m.storage_path,r.retain_until FROM event_media m
                JOIN event_retention r ON r.event_id=m.event_id
                WHERE m.status='ready' AND m.redaction_status='complete' AND r.decision='retain'
                AND r.consent_confirmed=1 AND r.retain_until>? ORDER BY m.media_id''', (timestamp,)).fetchall()
            if len(rows) > MAX_FILES:
                raise ValueError('Snapshot file count limit exceeded')
            for row in rows:
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', row['media_id']):
                    raise ValueError('Invalid media identifier')
                source = owned_path(row['storage_path'])
                if source in paths:
                    raise ValueError('Duplicate processed media path')
                paths.add(source)
                if not source.is_relative_to(PROCESSED_ROOT.resolve()) or not source.is_file() or source.is_symlink():
                    raise ValueError('Retained processed media missing or unsafe')
                size = source.stat().st_size
                total += size
                if not 0 < size <= MAX_FILE or total > MAX_TOTAL:
                    raise ValueError('Snapshot byte limit exceeded')
                entry = 'files/' + row['media_id']
                record = dict(row, entry=entry, size=size, sha256=digest(source))
                info = tarfile.TarInfo(entry)
                info.size, info.mode = size, 0o600
                with source.open('rb') as file:
                    tar.addfile(info, file)
                records.append(record)
        result = {'format':1, 'schema_version':LATEST_VERSION, 'created_at':timestamp,
                  'expires_at':min([timestamp+86400, *[r['retain_until'] for r in records]]),
                  'files':records, 'archive_sha256':digest(archive)}
        manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'archived_media':len(records), 'bytes':total, 'expires_at':result['expires_at']}
    except Exception:
        archive.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        raise


def verified_manifest(directory):
    directory = Path(directory)
    manifest = directory / 'media.json'
    if manifest.is_symlink() or manifest.stat().st_size > 2*1024**2:
        raise ValueError('Unsafe media manifest')
    body = json.loads(manifest.read_text(encoding='utf-8'))
    if body['format'] != 1 or body['schema_version'] != LATEST_VERSION or not valid_expiry(body['created_at'], body['expires_at']):
        raise ValueError('Expired or unsupported snapshot')
    archive = directory / 'media.tar'
    if archive.is_symlink() or archive.stat().st_size > MAX_TOTAL + 2*1024**2 or digest(archive) != body['archive_sha256']:
        raise ValueError('Media archive checksum mismatch')
    if len(body['files']) > MAX_FILES:
        raise ValueError('Snapshot file count limit exceeded')
    expected, paths = {}, set()
    for item in body['files']:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', item['media_id']) or item['entry'] != 'files/'+item['media_id']:
            raise ValueError('Unsafe archive entry')
        if item['entry'] in expected or not isinstance(item['size'], int) or not 0 < item['size'] <= MAX_FILE or not math.isfinite(item['retain_until']) or item['retain_until'] < body['expires_at']:
            raise ValueError('Duplicate, oversized or expired archive entry')
        target = owned_path(item['storage_path'])
        if not target.is_relative_to(PROCESSED_ROOT.resolve()) or target in paths:
            raise ValueError('Unsafe restored media path')
        paths.add(target)
        expected[item['entry']] = item
    if sum(item['size'] for item in expected.values()) > MAX_TOTAL:
        raise ValueError('Snapshot byte limit exceeded')
    seen = set()
    with tarfile.open(archive, 'r:') as tar:
        for member in tar:
            item = expected.get(member.name)
            if not member.isfile() or not item or member.name in seen or member.size != item['size']:
                raise ValueError('Unexpected archive member')
            with tar.extractfile(member) as file:
                if hashlib.file_digest(file, 'sha256').hexdigest() != item['sha256']:
                    raise ValueError('Media entry checksum mismatch')
            seen.add(member.name)
    if seen != set(expected):
        raise ValueError('Missing archive member')
    return body


def restore_media(directory, *, test_fixture=False):
    # CLI never permits restoring a running/development database.
    if not test_fixture and not re.fullmatch(r'/kyo_restore_[0-9a-f]{32}', urlsplit(os.environ.get('PPE_DATABASE_URL','')).path):
        raise ValueError('Restore requires an isolated kyo_restore_<uuid> database')
    body = verified_manifest(directory)
    if PROCESSED_ROOT.exists() and any(PROCESSED_ROOT.iterdir()):
        raise ValueError('Restore requires empty processed storage')
    files = {item['media_id']:item for item in body['files']}
    written = []
    try:
        with get_connection() as conn:
            begin_write(conn)
            if schema_version(conn) != LATEST_VERSION:
                raise ValueError('Restored schema/image mismatch')
            for ident, item in files.items():
                row = conn.execute('''SELECT m.storage_path,m.status,m.redaction_status,r.* FROM event_media m
                    JOIN event_retention r ON r.event_id=m.event_id WHERE m.media_id=?''', (ident,)).fetchone()
                if not row or row['storage_path'] != item['storage_path'] or row['status'] != 'ready' or row['redaction_status'] != 'complete' or row['decision'] != 'retain' or not row['consent_confirmed'] or not row['retain_until'] or row['retain_until'] <= time.time():
                    raise ValueError('Restored DB does not match retained media')
            # Copy verified regular members, never use tar.extract/extractall.
            with tarfile.open(Path(directory)/'media.tar', 'r:') as tar:
                for member in tar:
                    item = files[member.name.removeprefix('files/')]
                    target = owned_path(item['storage_path'])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as source, target.open('xb') as destination:
                        written.append(target)
                        shutil.copyfileobj(source, destination)
            conn.execute('DELETE FROM video_chunk')
            conn.execute('DELETE FROM video_stream')
            conn.execute('DELETE FROM processing_job')
            conn.execute("UPDATE field_command SET status='skipped',result=?,finished_at=? WHERE status IN ('pending','claimed','unknown')",
                         (json.dumps({'reason':'backup_restore_no_replay'}),time.time()))
            conn.execute('UPDATE field_node SET is_active=0')
            conn.execute('UPDATE app_user SET auth_version=auth_version+1')
            conn.execute("UPDATE event_clip_request SET status='unavailable',error_code='backup_source_excluded',updated_at=? WHERE status='pending'", (time.time(),))
            conn.execute("UPDATE event_media SET source_path='',source_expires_at=NULL,processing_started_at=NULL")
            rows = conn.execute('SELECT media_id,status FROM event_media').fetchall()
            for row in rows:
                if row['media_id'] not in files and row['status'] not in {'deleted','delete_pending'}:
                    conn.execute("UPDATE event_media SET storage_path='',status='missing',redaction_status='failed',error_code='source_file_missing' WHERE media_id=?", (row['media_id'],))
            return {'restored_media':len(files), 'events':conn.execute('SELECT COUNT(*) FROM candidate_event').fetchone()[0],
                    'users':conn.execute('SELECT COUNT(*) FROM app_user').fetchone()[0], 'nodes_disabled':True, 'pending_commands_replayed':False}
    except Exception:
        for target in written:
            target.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['export','verify','restore'])
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    if args.operation == 'export':
        result = export_media(args.directory)
    elif args.operation == 'restore':
        result = restore_media(args.directory)
    else:
        body = verified_manifest(args.directory)
        result = {'verified':True,'media':len(body['files'])}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
