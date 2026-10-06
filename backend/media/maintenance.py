"""Explicit housekeeping worker: expired retention, deletion retries and stale jobs."""

import argparse
import shutil
import time

from backend.db.event_repository import get_connection
from backend.media.paths import WORK_ROOT, PROCESSED_ROOT, SOURCE_ROOT, private_source_path, storage_key
from backend.media.service import _request_delete, delete_event_media


def maintain_once(stale_after: int = 3600) -> dict:
    cutoff = time.time() - stale_after
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        originals = conn.execute("SELECT media_id,source_path FROM event_media WHERE source_path!='' AND source_expires_at<=? AND processing_started_at IS NULL AND status!='processing'", (time.time(),)).fetchall()
        for original in originals:
            try:
                private_source_path(original["source_path"]).unlink(missing_ok=True)
                conn.execute("UPDATE event_media SET source_path='',source_expires_at=NULL WHERE media_id=?", (original["media_id"],))
            except (OSError, ValueError):
                pass  # Keep the reference so a later pass retries removal.
        expired = conn.execute("SELECT event_id FROM event_retention WHERE decision='retain' AND retain_until IS NOT NULL AND retain_until<=?", (time.time(),)).fetchall()
        for row in expired:
            _request_delete(conn, row["event_id"], "system:retention_expired")
        referenced = {row[0] for row in conn.execute("SELECT storage_path FROM event_media WHERE storage_path!=''")}
        referenced_sources = {row[0] for row in conn.execute("SELECT source_path FROM event_media WHERE source_path!=''")}
    cleaned = 0
    if SOURCE_ROOT.exists():
        for source in SOURCE_ROOT.iterdir():
            if source.is_file() and not source.is_symlink() and source.name not in referenced_sources and source.stat().st_mtime < cutoff:
                try:
                    source.unlink()
                    cleaned += 1
                except OSError:
                    pass
    if WORK_ROOT.exists():
        root = WORK_ROOT.resolve()
        for folder in WORK_ROOT.iterdir():
            # Delete only managed direct children whose resolved path stays inside the root.
            if folder.name.startswith("ppe_") and folder.is_dir() and not folder.is_symlink() and folder.resolve().is_relative_to(root) and folder.stat().st_mtime < cutoff:
                try:
                    shutil.rmtree(folder)
                    cleaned += 1
                except OSError:
                    pass  # Open files on Windows are retried on the next pass.
    with get_connection() as conn:
        stale = conn.execute("SELECT media_id,status FROM event_media WHERE processing_started_at<?", (cutoff,)).fetchall()
        for row in stale:
            if any(WORK_ROOT.glob(f"ppe_{row['media_id']}_*")):
                continue  # A private directory could not be removed yet.
            conn.execute("UPDATE event_media SET processing_started_at=NULL WHERE media_id=?", (row["media_id"],))
            if row["status"] == "processing":
                conn.execute("UPDATE event_media SET status='failed',redaction_status='failed',error_code='processing_interrupted' WHERE media_id=? AND status='processing'", (row["media_id"],))
        pending = conn.execute("SELECT event_id FROM event_retention WHERE decision='delete' AND deleted_at IS NULL").fetchall()
    for row in pending:
        delete_event_media(row["event_id"])
    if PROCESSED_ROOT.exists():
        root = PROCESSED_ROOT.resolve()
        for file in PROCESSED_ROOT.iterdir():
            if file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(root) and file.suffix in {".jpg", ".mp4"} and file.stat().st_mtime < cutoff and storage_key(file) not in referenced:
                try:
                    file.unlink()
                    cleaned += 1
                except OSError:
                    pass
    return {"expired_events": len(expired), "retried_events": len(pending), "cleaned_paths": cleaned}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("--interval must be positive")
    try:
        while True:
            print(maintain_once(), flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
