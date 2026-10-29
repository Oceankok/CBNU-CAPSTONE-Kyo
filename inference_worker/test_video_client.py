"""Exercise the real HTTP client against an isolated FastAPI instance."""
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from backend.tests.support import isolated_api, login
from backend.db.event_repository import get_connection
from inference_worker.video_client import VideoClient
from inference_worker.event_builder import build_event
from inference_worker.event_pipeline import submit_event_with_clip


class VideoClientContractTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        source = root / ".private_media" / "originals"
        source.mkdir(parents=True)
        for name, value in (
            ("backend.media.paths.PROJECT_ROOT", root),
            ("backend.media.paths.STORAGE_ROOT", root / "storage"),
            ("backend.media.paths.SOURCE_ROOT", source),
            ("backend.media.service.SOURCE_ROOT", source),
            ("backend.media.routes.WORK_ROOT", root / ".private_media"),
        ):
            self.enterContext(patch(name, value))
        self.enterContext(patch.dict(os.environ, {"PPE_PROCESSING_MODE": "remote"}))
        self.api = self.enterContext(isolated_api())
        self.admin = login(self.api)
        token = self.api.post("/api/nodes", headers=self.admin,
                             json={"node_id": "inference-contract", "name": "Test"}).json()["token"]
        self.api.put("/api/processing/workers/inference-contract", headers=self.admin,
                     json={"enabled": True}).raise_for_status()
        self.enterContext(patch.dict(os.environ, {
            "PPE_SERVER_URL": "http://testserver",
            "PPE_PROCESSING_NODE_TOKEN": token}))
        self.requests = []
        self.drop_upload_response = False

        def handle(request):
            self.requests.append((request.method, request.url.path))
            response = self.api.request(request.method, request.url.raw_path.decode(),
                                        headers=dict(request.headers), content=request.content)
            if self.drop_upload_response and request.url.path.endswith("/clip") and response.status_code == 201:
                self.drop_upload_response = False
                raise httpx.ReadTimeout("test: upload committed but response lost", request=request)
            return httpx.Response(response.status_code, content=response.content,
                                  headers=dict(response.headers))

        self.client = VideoClient(transport=httpx.MockTransport(handle))
        self.addCleanup(self.client.close)

    def event(self):
        now = datetime.now(timezone.utc)
        return build_event("CAM_001", "track-test", now, now, 0.9, 1)

    def test_event_clip_and_idempotent_retry_use_actual_routes(self):
        directory = self.enterContext(TemporaryDirectory())
        clip = Path(directory) / "clip.mp4"
        clip.write_bytes((Path(__file__).resolve().parents[1] /
                          "backend/db/demo_assets/sample.mp4").read_bytes())
        event = self.event()
        # Real ingestion queues processing; no GPU worker or redactor runs.
        self.drop_upload_response = True
        with patch("inference_worker.event_pipeline.time.sleep"):
            first = submit_event_with_clip(self.client, event, clip)
        second = submit_event_with_clip(self.client, event, clip)
        self.assertFalse(self.drop_upload_response)
        self.assertEqual(first["uploaded_count"], 1)
        with get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM event_media WHERE event_id=?",
                (event["event_id"],)).fetchone()[0], 1)
        self.assertEqual(second["event_id"], first["event_id"])
        with get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM candidate_event WHERE event_id=?",
                (event["event_id"],)).fetchone()[0], 1)
        self.assertEqual(self.client.get_clip_requests(event["event_id"])["event_id"],
                         event["event_id"])
        self.assertIn(("POST", "/api/processing/events/" + event["event_id"] + "/clip"),
                      self.requests)

    def test_worker_permission_and_priority_contract(self):
        self.client.set_inference_load(True)
        self.client.set_inference_load(False)
        self.assertFalse(self.api.get("/api/processing/inference-load",
            headers=self.client.client.headers).json()["busy"])
        self.assertEqual(self.client.list_streams(), [])
        self.assertIn("required_ppe", self.client.get_ppe_policy("CAM_001"))
        self.api.put("/api/processing/workers/inference-contract", headers=self.admin,
                     json={"enabled": False}).raise_for_status()
        with self.assertRaises(httpx.HTTPStatusError) as raised:
            self.client.submit_event(self.event())
        self.assertEqual(raised.exception.response.status_code, 403)

    def test_rejected_payload_is_not_retried(self):
        event = self.event()
        event["confidence"] = 2
        with self.assertRaises(httpx.HTTPStatusError) as raised:
            self.client.submit_event(event)
        self.assertEqual(raised.exception.response.status_code, 422)
