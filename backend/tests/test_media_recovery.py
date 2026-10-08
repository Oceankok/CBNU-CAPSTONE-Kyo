"""Recovery/lifecycle contracts; inference is stubbed so no model or CUDA is needed."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db.event_repository import get_connection
from backend.media.redaction import MediaError
from backend.media.service import ingest_file, get_media, reprocess_media
from backend.tests.support import isolated_api, login, ready_media


class MediaRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.context = isolated_api()
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.originals = root / "originals"
        self.originals.mkdir()
        self.processed = root / "processed"
        self.processed.mkdir()
        self.work = root / "work"
        self.work.mkdir()
        for module, name, value in [
            ("backend.media.paths", "SOURCE_ROOT", self.originals),
            ("backend.media.service", "SOURCE_ROOT", self.originals),
            ("backend.media.service", "WORK_ROOT", self.work),
            ("backend.media.service", "PROCESSED_ROOT", self.processed),
            ("backend.media.maintenance", "SOURCE_ROOT", self.originals),
            ("backend.media.maintenance", "WORK_ROOT", self.work),
            ("backend.media.maintenance", "PROCESSED_ROOT", self.processed),
        ]:
            patcher = patch(f"{module}.{name}", value)
            patcher.start()
            self.addCleanup(patcher.stop)
        def key(path):
            return "storage/" + path.name
        def owned(path):
            if not path.startswith("storage/") or Path(path).name != path.removeprefix("storage/"):
                raise ValueError("unsafe_storage_path")
            return self.processed / Path(path).name
        for name, value in [("storage_key", key), ("owned_path", owned)]:
            patcher = patch(f"backend.media.service.{name}", value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def redact(source, output, mode):
        output.write_bytes(b"redacted")
        return {"faces_detected":1,"processed_frames":1,"redaction_mode":mode,
                "inference_backend":"test-stub","processing_seconds":0.01}

    def ingest(self, error=None):
        source = self.work / "input.jpg"
        source.write_bytes(b"original")
        with patch("backend.media.service.redact_image", side_effect=error or self.redact):
            return ingest_file("EVT_0001", "CAM_001", "image", "reference", source, True)

    def test_failure_retains_private_source_and_retry_uses_original(self):
        media = self.ingest(MediaError("scrfd_model_missing"))
        self.assertTrue(media["can_reprocess"])
        self.assertIsNone(media["url"])
        self.assertNotIn("source_path", media)
        def retry(source, output, mode):
            self.assertEqual(source.read_bytes(), b"original")
            return self.redact(source, output, mode)
        with patch("backend.media.service.redact_image", side_effect=retry):
            result = reprocess_media(media["media_id"])
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["can_reprocess"])

    def test_missing_processing_and_expired_sources_disable_retry(self):
        media = self.ingest()
        with get_connection() as conn:
            conn.execute("UPDATE event_media SET source_expires_at=? WHERE media_id=?", (time.time()-1,media["media_id"]))
        self.assertEqual(get_media(media["media_id"])["reprocess_unavailable_reason"], "source_expired")
        with self.assertRaisesRegex(ValueError, "source_expired"):
            reprocess_media(media["media_id"])
        with get_connection() as conn:
            conn.execute("UPDATE event_media SET status='processing',processing_started_at=? WHERE media_id=?", (time.time(),media["media_id"]))
        self.assertEqual(get_media(media["media_id"])["reprocess_unavailable_reason"], "processing_in_progress")
        with get_connection() as conn:
            conn.execute("UPDATE event_media SET status='failed',processing_started_at=NULL,source_expires_at=? WHERE media_id=?", (time.time()+100,media["media_id"]))
        next(self.originals.iterdir()).unlink()
        self.assertFalse(get_media(media["media_id"])["can_reprocess"])

    def test_corrupt_source_is_distinct_from_redaction_failure(self):
        media = self.ingest(MediaError("invalid_frame"))
        self.assertEqual(media["failure_category"], "source_invalid")
        self.assertEqual(media["reprocess_unavailable_reason"], "source_invalid")

    def test_delete_removes_original_and_output(self):
        from backend.media.service import _request_delete, delete_event_media
        media = self.ingest()
        with get_connection() as conn:
            _request_delete(conn, "EVT_0001", "admin02")
        self.assertFalse(get_media(media["media_id"])["can_reprocess"])
        delete_event_media("EVT_0001")
        self.assertEqual(list(self.originals.iterdir()), [])
        self.assertEqual(list(self.processed.iterdir()), [])

    def test_expiry_cleans_original_without_deleting_redacted_output(self):
        from backend.media.maintenance import maintain_once
        media = self.ingest()
        with get_connection() as conn:
            conn.execute("UPDATE event_media SET source_expires_at=? WHERE media_id=?", (time.time()-1,media["media_id"]))
        maintain_once()
        self.assertEqual(list(self.originals.iterdir()), [])
        self.assertTrue(list(self.processed.iterdir()))
        self.assertFalse(get_media(media["media_id"])["can_reprocess"])

    def test_review_requires_usable_media_and_audits_unreviewable(self):
        headers = login(self.client)
        body = {"review_result":"confirmed","review_reason_code":"confirmed_no_helmet"}
        self.assertEqual(self.client.post("/api/events/EVT_0001/review",headers=headers,json=body).status_code, 409)
        body.update(review_result="unreviewable",review_reason_code="source_missing",review_comment="현장 PC에서도 원본 복구 불가 확인")
        result = self.client.post("/api/events/EVT_0001/review",headers=headers,json=body)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["review"]["reviewer_id"],"admin02")
        with ready_media():
            body.update(review_result="confirmed",review_reason_code="confirmed_no_helmet")
            result = self.client.put("/api/events/EVT_0001/review",headers=headers,json=body)
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json()["event"]["event_status"],"confirmed")

    def test_pending_clip_and_retryable_redaction_block_unreviewable(self):
        headers = login(self.client)
        body = {"review_result":"unreviewable","review_reason_code":"clip_unavailable","review_comment":"복구 확인"}
        request = self.client.post("/api/events/EVT_0001/clip-requests",headers=headers,json={"camera_id":"CAM_001"})
        self.assertEqual(request.status_code,201,request.text)
        self.assertEqual(self.client.post("/api/events/EVT_0001/review",headers=headers,json=body).status_code,409)
        with get_connection() as conn:
            conn.execute("UPDATE event_clip_request SET status='unavailable'")
        media = self.ingest(MediaError("scrfd_model_missing"))
        self.assertTrue(media["can_reprocess"])
        result = self.client.post("/api/events/EVT_0001/review",headers=headers,json=body)
        self.assertEqual(result.status_code,409)
        self.assertEqual(result.json()["detail"], "media_recovery_pending")

    def test_failed_delivery_requires_recovery_or_explicit_unavailable(self):
        headers = login(self.client)
        request = self.client.post("/api/events/EVT_0001/clip-requests",headers=headers,json={"camera_id":"CAM_001"})
        request_id = request.json()["request_id"]
        path = f"/api/events/EVT_0001/clip-requests/{request_id}"
        self.assertEqual(self.client.put(path,headers=headers,json={"status":"received"}).status_code,409)
        self.assertEqual(self.client.put(path,headers=headers,json={"status":"failed","error_code":"node_offline"}).status_code,200)
        body = {"review_result":"unreviewable","review_reason_code":"clip_unavailable","review_comment":"복구 불가 확인"}
        self.assertEqual(self.client.post("/api/events/EVT_0001/review",headers=headers,json=body).status_code,409)
        self.assertEqual(self.client.put(path,headers=headers,json={"status":"unavailable","error_code":"clip_evicted"}).status_code,200)
        self.assertEqual(self.client.post("/api/events/EVT_0001/review",headers=headers,json=body).status_code,200)

    def test_unreviewable_is_counted_separately(self):
        from backend.db.event_repository import generate_quarterly_stats, get_quarterly_stats
        headers = login(self.client)
        body={"review_result":"unreviewable","review_reason_code":"clip_unavailable","review_comment":"복구 불가 확인"}
        response = self.client.post("/api/events/EVT_0001/review",headers=headers,json=body)
        self.assertEqual(response.status_code,200,response.text)
        with get_connection() as conn:
            conn.execute("UPDATE candidate_event SET timestamp_start='2026-10-01 12:00:00' WHERE event_id='EVT_0001'")
        generate_quarterly_stats("2026-Q4")
        summary = get_quarterly_stats("2026-Q4")["summary"]
        self.assertEqual(summary["unreviewable_count"],1)
        self.assertEqual(summary["confirmed_count"],0)

    def test_v3_migration_preserves_media_and_adds_recovery_fields(self):
        from backend.db.migrations import apply_migrations
        media = self.ingest()
        with get_connection() as conn:
            conn.execute("ALTER TABLE event_media DROP COLUMN source_path")
            conn.execute("ALTER TABLE event_media DROP COLUMN source_expires_at")
            conn.execute("ALTER TABLE quarterly_summary DROP COLUMN unreviewable_count")
            conn.execute("PRAGMA user_version=3")
            conn.commit()
            apply_migrations(conn)
            row = conn.execute("SELECT * FROM event_media WHERE media_id=?", (media["media_id"],)).fetchone()
            self.assertEqual(row["status"],"ready")
            self.assertEqual(row["source_path"],"")
            self.assertIsNone(row["source_expires_at"])
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],4)
