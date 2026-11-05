"""Offline runner contract: 3-class review delivery respects work-area policy.

No live camera, external HTTP server, model weights, or broadcast is used.
"""
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from inference_worker import run_server as runner
from inference_worker.test_server_mock import FakeVideoClient


class ReviewDetector:
    model_version = "fixture-3class"
    supports_no_helmet = False

    def detect(self, frame):
        return [{"class_name": "person", "confidence": 0.95,
                 "bbox": [4, 2, 28, 60]}]


class WorkAreaVideoClient(FakeVideoClient):
    def __init__(self, *, required=True, invalid=False):
        super().__init__(count=9)
        self.required = required
        self.invalid = invalid

    def get_ppe_policy(self, camera_id):
        return {
            "configured": True,
            "scope": "work_area",
            "policy_version": "test-roi-v1",
            "configuration_issues": ["invalid_area"] if self.invalid else [],
            "zone_required_ppe": [],
            "event_supported_ppe": ["helmet"],
            "areas": [{
                "area_id": "ROI-A",
                "points": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]],
                "equipment_required_ppe": ["helmet"] if self.required else [],
            }],
        }


class ROIRunnerEndToEndTests(unittest.TestCase):
    def run_case(self, *, required=True, invalid=False):
        client = WorkAreaVideoClient(required=required, invalid=invalid)
        output = io.StringIO()
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        with TemporaryDirectory() as temp:
            clip_paths = []

            def make_clip(frames, path, fps):
                path = Path(path)
                path.write_bytes(b"fake-mp4")
                clip_paths.append(path)
                return path

            args = ["run_server", "--camera-id", "CAM_001",
                    "--max-chunks", "9", "--submit-review-candidates"]
            with (
                patch.object(runner, "VideoClient", return_value=client),
                patch.object(runner, "create_detector", return_value=ReviewDetector()),
                patch.object(runner, "decode_mp4", side_effect=lambda *a, **k:
                             iter((i, 4.0, frame) for i in range(4))),
                patch.object(runner, "create_clip", side_effect=make_clip),
                patch.object(runner.time, "sleep"),
                patch.object(sys, "argv", args),
                patch.object(runner, "submit_event_with_clip",
                             return_value={"event_id": "EVT_fixture"}) as delivery,
                redirect_stdout(output),
            ):
                runner.main()
            self.assertTrue(client.closed)
            for path in clip_paths:
                self.assertFalse(path.exists(), "Temporary clip must be cleaned up")
        return output.getvalue(), delivery

    def test_helmet_required_roi_submits_review_only_candidate(self):
        output, delivery = self.run_case()
        self.assertIn("[REVIEW CANDIDATE READY]", output)
        delivery.assert_called_once()
        event = delivery.call_args.args[1]
        self.assertTrue(event["review_only"])
        self.assertIsNone(event["confidence"])
        self.assertEqual(event["ppe_type"], "helmet")
        self.assertIn("[REVIEW CANDIDATE DELIVERED]", output)

    def test_helmet_optional_roi_never_submits(self):
        output, delivery = self.run_case(required=False)
        delivery.assert_not_called()
        self.assertNotIn("[REVIEW CANDIDATE READY]", output)

    def test_invalid_roi_configuration_never_submits(self):
        output, delivery = self.run_case(invalid=True)
        delivery.assert_not_called()
        self.assertIn("[POLICY SKIP]", output)


if __name__ == "__main__":
    unittest.main()
