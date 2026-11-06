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

    def __init__(self, warmup_frames=8):
        self.frames_seen = 0
        self.warmup_frames = warmup_frames

    def detect(self, frame):
        self.frames_seen += 1

        # First 2 seconds (8 frames at 4 FPS): no person.
        # This provides the pre-roll needed before observation starts.
        if self.frames_seen <= self.warmup_frames:
            return []

        return [{"class_name": "person", "confidence": 0.95,
                 "bbox": [4, 2, 28, 60]}]


class WorkAreaVideoClient(FakeVideoClient):
    def __init__(self, *, required=True, invalid=False, roi=True, count=9):
        super().__init__(count=count)
        self.required = required
        self.invalid = invalid
        self.roi = roi

    def get_ppe_policy(self, camera_id):
        if not self.roi:
            return super().get_ppe_policy(camera_id)
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


class StopPolicyPolling(Exception):
    pass


class ROIRunnerEndToEndTests(unittest.TestCase):
    def run_case(self, *, required=True, invalid=False, present_from_start=False, roi=True, count=9):
        client = WorkAreaVideoClient(required=required, invalid=invalid, roi=roi, count=count)
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
                    "--max-chunks", str(count), "--submit-review-candidates"]
            with (
                patch.object(runner, "VideoClient", return_value=client),
                patch.object(runner, "create_detector", return_value=ReviewDetector(
                    warmup_frames=0 if present_from_start else 8)),
                patch.object(runner, "decode_mp4", side_effect=lambda *a, **k:
                             iter((i, 4.0, frame) for i in range(4))),
                patch.object(runner, "create_clip", side_effect=make_clip),
                patch.object(
                    runner.time, "sleep",
                    side_effect=(lambda _: (_ for _ in ()).throw(StopPolicyPolling()))
                    if invalid else None,
                ),
                patch.object(sys, "argv", args),
                patch.object(runner, "submit_event_with_clip",
                             return_value={"event_id": "EVT_fixture"}) as delivery,
                redirect_stdout(output),
            ):
                if invalid:
                    with self.assertRaises(StopPolicyPolling):
                        runner.main()
                else:
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

    def test_person_present_at_start_rearms_after_preroll(self):
        from datetime import datetime, timedelta
        for roi in (True, False):
            with self.subTest(roi=roi):
                output, delivery = self.run_case(present_from_start=True, roi=roi)
                delivery.assert_called_once()
                event = delivery.call_args.args[1]
                first_frame = FakeVideoClient().start
                start = datetime.fromisoformat(event["timestamp_start"])
                end = datetime.fromisoformat(event["timestamp_end"])
                self.assertGreaterEqual(start, first_frame + timedelta(seconds=2))
                self.assertGreaterEqual((end - start).total_seconds(), 3)
                self.assertEqual(event["frame_sample_count"], 13)
                self.assertTrue(event["review_only"])
                self.assertIsNone(event["confidence"])
                self.assertNotIn("[REVIEW BLOCKED] pre-roll unavailable", output)

    def test_person_present_at_start_still_requires_postroll(self):
        output, delivery = self.run_case(present_from_start=True, count=6)
        self.assertIn("[REVIEW CANDIDATE READY]", output)
        delivery.assert_not_called()

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
