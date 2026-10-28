
"""Offline tests for event and clip submission coordination."""

import tempfile
import unittest
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
