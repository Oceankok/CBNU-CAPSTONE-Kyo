"""Offline runner checks: no model, external server, or private video."""
import io
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

import numpy as np

from inference_worker import run_server as runner


class FakeVideoClient:
    def __init__(self, count=4, last_sequence=-1):
        self.count = count
        self.last_sequence = last_sequence
        self.downloads = []
        self.start = datetime(2026, 10, 29, tzinfo=timezone.utc)
        self.closed = False
        self.loads = []

    def list_streams(self):
        return [{"stream_id": "TEST_STREAM", "camera_id": "CAM_001", "last_sequence": self.last_sequence}]

    def get_ppe_policy(self, camera_id):
        return {"configured": True, "configuration_issues": [],
                "required_ppe": ["helmet"], "detectable_ppe": ["helmet"]}

    def list_chunks(self, stream_id, after_sequence=-1):
        return [{"chunk_id": str(index), "sequence": index, "duration_ms": 1000,
                 "capture_start_at": (self.start + timedelta(seconds=index)).isoformat()}
                for index in range(self.count) if index > after_sequence]

    def download_chunk(self, chunk_id):
        self.downloads.append(chunk_id)
        return b"synthetic"

    def set_inference_load(self, busy, ttl_seconds=30, *, session_id=None):
        self.loads.append(busy)

    def close(self):
        self.closed = True


class FakeDetector:
    def detect(self, frame):
        return [{"class_name": "person", "confidence": 0.95,
                 "bbox": [0, 0, 30, 60]}]


class QualifiedCandidates:
    """Only a test fixture supplies verified PPE confidence."""
    def __init__(self, start):
        self.start = start.timestamp()
        self.reported = False

    def update(self, assessments, timestamp):
        if timestamp >= self.start + 5 and not self.reported:
            self.reported = True
            return [{"tracking_id": "track-1", "ppe_type": "helmet",
                     "start_time": self.start + 2, "end_time": self.start + 5,
                     "duration_seconds": 3, "verified_ppe_confidence": 0.9}]
        return []


class ServerInferenceTest(unittest.TestCase):
    def run_runner(self, client, *, submit=False, qualified=False, delivery_error=False, replay=False, max_chunks=None):
        roots = []
        original_tempdir = tempfile.TemporaryDirectory

        def temporary(*args, **kwargs):
            result = original_tempdir(*args, **kwargs)
            roots.append(Path(result.name))
            return result

        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        output = io.StringIO()
        args = ["run_server", "--camera-id", "CAM_001",
                "--max-chunks", str(max_chunks or (client.count if replay else client.count-client.last_sequence-1))]
        if submit:
            args.append("--submit-events")
        if replay:
            args.append("--replay-buffer")
        with (
            patch.object(runner, "VideoClient", return_value=client),
            patch.object(runner, "create_detector", return_value=FakeDetector()),
            patch.object(runner, "decode_mp4", side_effect=lambda *args, **kwargs: iter((index, 4.0, frame) for index in range(4))),
            patch.object(runner.time, "sleep"),
            patch.object(runner.tempfile, "TemporaryDirectory", side_effect=temporary),
            patch.object(sys, "argv", args),
            patch.object(runner, "submit_event_with_clip",
                         side_effect=runner.ClipDeliveryError("test delivery failure") if delivery_error else None,
                         return_value={"event_id": "EVT_test"}) as deliver,
            redirect_stdout(output),
        ):
            if qualified:
                candidate_scope = patch.object(runner, "ViolationTracker",
                    side_effect=lambda **kwargs: QualifiedCandidates(client.start))
            else:
                from contextlib import nullcontext
                candidate_scope = nullcontext()
            with candidate_scope:
                runner.main()
            if deliver.called:
                self.assertFalse(Path(deliver.call_args.args[2]).exists())
        self.assertTrue(client.closed)
        self.assertTrue(roots)
        self.assertTrue(all(not path.exists() for path in roots))
        self.assertFalse(client.loads[-1])
        return output.getvalue(), deliver

    def test_four_chunks_one_candidate_no_submission(self):
        output, deliver = self.run_runner(FakeVideoClient())
        self.assertEqual(output.count("[OK]"), 4)
        self.assertEqual(output.count("[LOCAL CANDIDATE]"), 1)
        self.assertIn("ppe_confidence_not_verified", output)
        deliver.assert_not_called()

    def test_qualified_candidate_delivery_and_cleanup(self):
        output, deliver = self.run_runner(FakeVideoClient(8), submit=True, qualified=True)
        deliver.assert_called_once()
        event = deliver.call_args.args[1]
        self.assertEqual(event["confidence"], 0.9)
        self.assertEqual(event["camera_id"], "CAM_001")
        self.assertIn("[EVENT DELIVERED]", output)

    def test_unverified_candidate_never_submits_even_when_enabled(self):
        _, deliver = self.run_runner(FakeVideoClient(8), submit=True)
        deliver.assert_not_called()

    def test_read_only_qualified_candidate_does_not_submit(self):
        _, deliver = self.run_runner(FakeVideoClient(8), qualified=True)
        deliver.assert_not_called()

    def test_failed_upload_removes_clip_and_releases_priority(self):
        output, _ = self.run_runner(FakeVideoClient(8), submit=True, qualified=True, delivery_error=True)
        self.assertIn('[EVENT DELIVERY FAILED]', output)
        self.assertEqual(output.count('[OK]'), 8)

    def test_incomplete_clip_not_submitted(self):
        _, deliver = self.run_runner(FakeVideoClient(6), submit=True, qualified=True)
        deliver.assert_not_called()


    def test_live_start_skips_existing_chunks_and_consumes_new_frames(self):
        client = FakeVideoClient(8, last_sequence=3)
        output, _ = self.run_runner(client)
        self.assertEqual(client.downloads, ['4', '5', '6', '7'])
        self.assertIn('after_sequence=3', output)

    def test_restart_does_not_reprocess_previously_delivered_footage(self):
        first = FakeVideoClient(8)
        _, delivered = self.run_runner(first, submit=True, qualified=True)
        delivered.assert_called_once()
        restarted = FakeVideoClient(12, last_sequence=7)
        _, repeated = self.run_runner(restarted, submit=True, qualified=True)
        self.assertEqual(restarted.downloads, ['8', '9', '10', '11'])
        repeated.assert_not_called()

    def test_inspection_replay_reads_buffer_but_does_not_submit(self):
        client = FakeVideoClient(8, last_sequence=3)
        _, delivered = self.run_runner(client, replay=True, qualified=True)
        self.assertEqual(client.downloads, [str(index) for index in range(8)])
        delivered.assert_not_called()

    def test_replay_cannot_enable_event_or_broadcast_submission(self):
        args = ['run_server', '--camera-id', 'CAM_001', '--replay-buffer', '--submit-events']
        with patch.object(sys, 'argv', args), patch.object(runner, 'create_detector') as detector:
            with self.assertRaises(SystemExit) as error, redirect_stdout(io.StringIO()):
                runner.main()
        self.assertEqual(error.exception.code, 2)
        detector.assert_not_called()

    def test_stream_replacement_establishes_new_live_boundary(self):
        class ReplacedStream(FakeVideoClient):
            def __init__(self):
                super().__init__(3)
                self.polls = 0

            def list_streams(self):
                self.polls += 1
                return [{'stream_id': 'old' if self.polls == 1 else 'new',
                         'camera_id': 'CAM_001', 'last_sequence': 3 if self.polls == 1 else 0}]

            def list_chunks(self, stream_id, after_sequence=-1):
                indices = [4] if stream_id == 'old' else [1, 2]
                return [{'chunk_id': stream_id + str(index), 'sequence': index, 'duration_ms': 1000,
                         'capture_start_at': (self.start + timedelta(seconds=index)).isoformat()}
                        for index in indices if index > after_sequence]

        client = ReplacedStream()
        self.run_runner(client, max_chunks=3)
        self.assertEqual(client.downloads, ['old4', 'new1', 'new2'])
