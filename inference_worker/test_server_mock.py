
"""Offline integration test for the read-only server inference runner."""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import inference_worker.run_server as runner


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "video_sender" / "fixtures" / "sample.mp4"


class FakeVideoClient:
    def __init__(self):
        self.video = SAMPLE.read_bytes()
        self.start = datetime(
            2026, 10, 29, 10, 0, tzinfo=timezone(timedelta(hours=9))
        )
        self.closed = False

    def list_streams(self):
        return [{
            "stream_id": "TEST_STREAM",
            "camera_id": "CAM_001",
        }]

    def get_ppe_policy(self, camera_id):
        return {
            "configured": True,
            "configuration_issues": [],
            "required_ppe": ["helmet"],
            "detectable_ppe": ["helmet"],
        }

    def list_chunks(self, stream_id, after_sequence=-1):
        chunks = []

        for index in range(4):
            if index <= after_sequence:
                continue

            chunks.append({
                "chunk_id": f"TEST_CHUNK_{index}",
                "stream_id": stream_id,
                "sequence": index,
                "capture_start_at": (
                    self.start + timedelta(seconds=index)
                ).isoformat(),
                "duration_ms": 1000,
            })

        return chunks

    def download_chunk(self, chunk_id):
        return self.video

    def close(self):
        self.closed = True


class ServerInferenceTest(unittest.TestCase):
    def test_four_chunks_one_candidate(self):
        self.assertTrue(SAMPLE.is_file())

        output = io.StringIO()
        client = FakeVideoClient()

        with (
            patch.object(runner, "VideoClient", return_value=client),
            patch.object(
                sys,
                "argv",
                ["run_server", "--camera-id", "CAM_001", "--max-chunks", "4"],
            ),
            redirect_stdout(output),
        ):
            runner.main()

        result = output.getvalue()

        self.assertEqual(result.count("[OK]"), 4)
        self.assertEqual(result.count("[LOCAL CANDIDATE]"), 1)
        self.assertIn("Processed chunks: 4", result)
        self.assertTrue(client.closed)

    def test_policy_not_configured(self):
        from inference_worker.policy import helmet_policy_status

        enabled, reason = helmet_policy_status({
            "configured": False,
            "configuration_issues": ["zone_rule_missing"],
            "required_ppe": None,
            "detectable_ppe": ["helmet"],
        })

        self.assertFalse(enabled)
        self.assertEqual(reason, "policy_not_configured")


if __name__ == "__main__":
    unittest.main()
