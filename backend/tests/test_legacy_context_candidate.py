import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login
from inference_worker.event_builder import build_event
from inference_worker.run_server import (_log_event_submission_failure,
                                         _select_camera_stream,
                                         build_review_candidate_event)


class LegacyContextCandidateIntegrationTests(unittest.TestCase):
    def setup_nodes(self, client):
        admin = login(client)
        nodes = {}
        for ident in ("source", "processor"):
            response = client.post("/api/nodes", headers=admin,
                                   json={"node_id": ident, "name": ident})
            self.assertEqual(response.status_code, 201, response.text)
            nodes[ident] = {"Authorization": "Bearer " + response.json()["token"]}
        for camera in ("CAM_001", "CAM_002"):
            response = client.put(f"/api/cameras/{camera}/node", headers=admin,
                                  json={"source_node_id": "source", "output_node_id": "source"})
            self.assertEqual(response.status_code, 200, response.text)
        response = client.put("/api/processing/workers/processor", headers=admin,
                              json={"enabled": True})
        self.assertEqual(response.status_code, 200, response.text)
        return admin, nodes

    def capture(self, client, headers, camera_id, start, count, asset):
        response = client.post("/api/video/capture-sessions", headers=headers,
                               json={"camera_id": camera_id, "session_id": uuid4().hex})
        self.assertEqual(response.status_code, 201, response.text)
        context = response.json()
        with get_connection() as conn:
            conn.execute("UPDATE capture_context_registry SET valid_from=? WHERE context_id=?",
                         ((start - timedelta(seconds=1)).isoformat(), context["context_id"]))
        stream_id = str(uuid4())
        response = client.post("/api/video/streams", headers=headers,
                               json={"camera_id": camera_id,
                                     "capture_context_id": context["context_id"],
                                     "stream_id": stream_id})
        self.assertEqual(response.status_code, 201, response.text)
        data = asset.read_bytes()
        for sequence in range(count):
            response = client.post(
                f"/api/video/streams/{stream_id}/chunks?sequence={sequence}",
                headers={**headers, "Content-Type": "video/mp4",
                         "X-Capture-Start": (start + timedelta(seconds=sequence)).isoformat(),
                         "X-Duration-Ms": "1000"},
                content=data,
            )
            self.assertEqual(response.status_code, 202, response.text)
        return context, stream_id

    def test_builder_to_event_api_and_db_binding(self):
        with isolated_api() as client, patch.dict(os.environ, {"PPE_CAPTURE_CONTEXT_ENABLED": "true"}):
            admin, nodes = self.setup_nodes(client)
            start = datetime.now(timezone.utc) - timedelta(seconds=3)
            context, _ = self.capture(client, nodes["source"], "CAM_001", start, 3,
                                       Path("backend/db/demo_assets/sample.mp4"))
            payload = build_review_candidate_event(
                {"tracking_id": "track-clean", "ppe_type": "helmet", "frame_sample_count": 30},
                camera_id="CAM_001", start_time=start, end_time=start + timedelta(seconds=3),
                model_version="synthetic-clean", capture_context_id=context["context_id"],
            )
            response = client.post("/api/processing/events", headers=nodes["processor"], json=payload)
            self.assertIn(response.status_code, (200, 201), response.text)
            with get_connection() as conn:
                binding = conn.execute("SELECT context_id FROM event_capture_context WHERE event_id=?",
                                       (payload["event_id"],)).fetchone()
                status = conn.execute("SELECT event_status FROM candidate_event WHERE event_id=?",
                                      (payload["event_id"],)).fetchone()
            self.assertEqual(binding["context_id"], context["context_id"])
            self.assertEqual(status["event_status"], "pending")
            before_valid_from = build_review_candidate_event(
                {"tracking_id": "track-clock", "ppe_type": "helmet", "frame_sample_count": 3},
                camera_id="CAM_001", start_time=start - timedelta(seconds=2),
                end_time=start - timedelta(seconds=1), model_version="synthetic-clock",
                capture_context_id=context["context_id"],
            )
            boundary = client.post("/api/processing/events", headers=nodes["processor"],
                                  json=before_valid_from)
            self.assertEqual(boundary.status_code, 422, boundary.text)
            self.assertIn(boundary.json().get("detail"), {
                "capture_interval_outside_context_or_future",
                "capture_interval_crosses_stop",
            })

    def test_two_stream_reference_pending_bound_and_event_read(self):
        with isolated_api() as client, patch.dict(os.environ, {"PPE_CAPTURE_CONTEXT_ENABLED": "true"}):
            admin, nodes = self.setup_nodes(client)
            group = client.post("/api/camera-groups", headers=admin,
                                json={"name": "clean-overlap", "camera_ids": ["CAM_001", "CAM_002"]})
            self.assertEqual(group.status_code, 201, group.text)
            start = datetime.now(timezone.utc) - timedelta(seconds=8)
            asset = Path("backend/db/demo_assets/sample.mp4")
            source_context, source_stream = self.capture(client, nodes["source"], "CAM_001", start, 7, asset)
            reference_context, reference_stream = self.capture(client, nodes["source"], "CAM_002", start, 5, asset)
            event_start = start + timedelta(seconds=2)
            payload = build_review_candidate_event(
                {"tracking_id": "track-reference", "ppe_type": "helmet", "frame_sample_count": 30},
                camera_id="CAM_001", start_time=event_start, end_time=start + timedelta(seconds=5),
                model_version="synthetic-clean", capture_context_id=source_context["context_id"],
            )
            response = client.post("/api/processing/events", headers=nodes["processor"], json=payload)
            self.assertIn(response.status_code, (200, 201), response.text)
            event_id = payload["event_id"]
            pending = client.get("/api/processing/reference-clip-requests", headers=nodes["processor"])
            self.assertEqual(pending.status_code, 200, pending.text)
            item = next(row for row in pending.json()["items"] if row["event_id"] == event_id)
            self.assertEqual(item["capture_context_state"], "pending")
            self.assertEqual(item["stream_id"], reference_stream)
            self.assertEqual(item["capture_context_id"], reference_context["context_id"])
            data = asset.read_bytes()
            for sequence in (5, 6):
                response = client.post(
                    f"/api/video/streams/{reference_stream}/chunks?sequence={sequence}",
                    headers={**nodes["source"], "Content-Type": "video/mp4",
                             "X-Capture-Start": (start + timedelta(seconds=sequence)).isoformat(),
                             "X-Duration-Ms": "1000"},
                    content=data,
                )
                self.assertEqual(response.status_code, 202, response.text)
            bound = client.get("/api/processing/reference-clip-requests", headers=nodes["processor"])
            self.assertEqual(bound.status_code, 200, bound.text)
            item = next(row for row in bound.json()["items"] if row["event_id"] == event_id)
            self.assertEqual(item["capture_context_state"], "bound")
            self.assertEqual(item["stream_id"], reference_stream)
            self.assertEqual(item["capture_session_id"], reference_context["session_id"])
            visible = client.get(f"/api/events/{event_id}", headers=admin)
            self.assertEqual(visible.status_code, 200, visible.text)
            self.assertEqual(visible.json()["event"]["event_id"], event_id)

    def test_runner_stream_selection_never_uses_old_closed_order(self):
        old = {"camera_id": "CAM_001", "stream_id": "old", "capture_stopped": True,
               "capture_context_id": str(uuid4())}
        new = {"camera_id": "CAM_001", "stream_id": "new", "capture_stopped": False,
               "capture_context_id": str(uuid4())}
        self.assertEqual(_select_camera_stream([old, new], "CAM_001")["stream_id"], "new")

    def test_event_rejection_logging_is_allowlisted_and_redacted(self):
        class Response:
            status_code = 422

            def __init__(self, body):
                self.body = body

            def json(self):
                return self.body

        with patch("inference_worker.run_server.logger.warning") as warning:
            _log_event_submission_failure(Response({"detail": "capture_context_required"}))
            _log_event_submission_failure(Response({
                "detail": [{"loc": ["body", "capture_context_id", "secret-token"],
                            "type": "value_error.extra"}],
            }))
            _log_event_submission_failure(Response({
                "detail": [
                    {"loc": ["body", {"secret": "location"}], "type": ["malicious"]},
                    {"loc": ["body"], "type": {"malicious": "type"}},
                ],
            }))
            output = " ".join(str(call) for call in warning.call_args_list)
        self.assertIn("capture_context_required", output)
        self.assertIn("unknown", output)
        self.assertNotIn("secret-token", output)


if __name__ == "__main__":
    unittest.main()
