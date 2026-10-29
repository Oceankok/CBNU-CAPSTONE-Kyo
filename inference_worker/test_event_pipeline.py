
"""Offline tests for event and clip submission coordination."""

import tempfile
import unittest
from unittest.mock import patch
import httpx
from pathlib import Path

from inference_worker.event_pipeline import submit_event_with_clip


class FakeClient:
    def __init__(self):
        self.events = []
        self.uploads = []

    def submit_event(self, event):
        self.events.append(event.copy())
        return {
            "event_id": event["event_id"],
            "created": True,
            "clip_requests": [
                {
                    "request_id": "REQ_001",
                    "camera_id": "CAM_001",
                    "role": "clip",
                }
            ],
        }

    def upload_clip(self, event_id, request_id, data):
        self.uploads.append((event_id, request_id, data))
        return {"status": "received"}


class EventPipelineTest(unittest.TestCase):
    def test_upload_retry_keeps_event_and_request_id(self):
        client = FakeClient()
        original_upload = client.upload_clip
        attempts = []
        def upload(event_id, request_id, data):
            attempts.append((event_id, request_id, data))
            if len(attempts) == 1:
                raise httpx.ReadTimeout("lost upload response")
            return original_upload(event_id, request_id, data)
        client.upload_clip = upload
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / "clip.mp4"
            clip.write_bytes(b"test")
            event = {"event_id": "EVT_" + "a" * 32, "camera_id": "CAM_001"}
            with patch("inference_worker.event_pipeline.time.sleep"):
                submit_event_with_clip(client, event, clip)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(len(client.events), 1)

    def test_event_retry_preserves_payload_and_id(self):
        client = FakeClient()
        original = client.submit_event
        attempts = []
        def submit(event):
            attempts.append(event.copy())
            if len(attempts) == 1:
                raise httpx.ReadTimeout("lost event response")
            return original(event)
        client.submit_event = submit
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / "clip.mp4"
            clip.write_bytes(b"test")
            with patch("inference_worker.event_pipeline.time.sleep"):
                submit_event_with_clip(client, {
                    "event_id": "EVT_" + "b" * 32, "camera_id": "CAM_001"}, clip)
        self.assertEqual(attempts[0], attempts[1])

    def test_permanent_error_is_not_retried(self):
        client = FakeClient()
        calls = []
        def denied(event):
            calls.append(event)
            request = httpx.Request("POST", "http://test/events")
            response = httpx.Response(403, request=request)
            response.raise_for_status()
        client.submit_event = denied
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / "clip.mp4"
            clip.write_bytes(b"test")
            with self.assertRaises(httpx.HTTPStatusError):
                submit_event_with_clip(client, {
                    "event_id": "EVT_" + "c" * 32, "camera_id": "CAM_001"}, clip)
        self.assertEqual(len(calls), 1)

    def test_event_and_clip(self):
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / "sample.mp4"
            clip.write_bytes(b"mock-mp4-data")

            client = FakeClient()
            event = {
                "event_id": "EVT_" + "a" * 32,
                "camera_id": "CAM_001",
            }

            result = submit_event_with_clip(client, event, clip)

            self.assertEqual(len(client.events), 1)
            self.assertEqual(len(client.uploads), 1)
            self.assertEqual(result["uploaded_count"], 1)
            self.assertEqual(
                client.uploads[0][1],
                "REQ_001",
            )


if __name__ == "__main__":
    unittest.main()
