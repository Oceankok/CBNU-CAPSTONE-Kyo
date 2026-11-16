"""CPU-only real H264 partial capture; no detector, camera or network."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import numpy as np

from inference_worker.multicamera_capture import MultiCameraCapture, CameraClock
from inference_worker.continuous_payloads import capture_payload


class ManualFlushGridTests(TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.frame = np.zeros((16, 16, 3), np.uint8)
        self.origin = 1794825522.363
        self.capture = MultiCameraCapture(root/'raw',
            cameras={c: CameraClock(0, 0, False) for c in ('main', 'ref')},
            max_clock_error=0, pre_seconds=0, post_seconds=0,
            discovery_delay_limit=3, prebuffer_seconds=5, prebuffer_bytes=100000,
            max_buffer_frames=100, max_events=2, global_spool_bytes=200000,
            manual_review=True,
            spool_config=dict(max_bytes=100000, max_segments=10, max_age_seconds=100,
                              max_attempts=2),
            writer_config=dict(segment_seconds=30, output_fps=15, max_frame_pixels=256,
                               max_input_gap_seconds=.25, encoder_timeout_seconds=5))
        self.addCleanup(self.capture.close)
        for camera in ('main', 'ref'):
            self.capture.ingest(camera, self.origin, self.frame)
        self.capture.discover('event', start=self.origin, discovered_at=self.origin,
                              camera_snapshot=['main', 'ref'])

    def test_irregular_reference_gap_seals_grid_and_keeps_later_frames(self):
        for offset in (1/15, .14, .4):
            self.capture.ingest('ref', self.origin+offset, self.frame)
        cap = self.capture.events['event']['captures']['ref']
        self.assertFalse(cap.done)
        self.assertEqual(len(cap.segments), 1)
        self.assertAlmostEqual(cap.segments[0].capture_end-self.origin, .2, places=5)
        self.assertIn('source_input_gap', cap.reasons)
        for offset in (.4+1/15, .54):
            self.capture.ingest('ref', self.origin+offset, self.frame)
        self.capture.end_source('ref')
        self.assertEqual(len(cap.segments), 2)
        self.assertAlmostEqual(cap.segments[1].capture_start-self.origin, .4, places=5)
        self.assertAlmostEqual(cap.segments[1].capture_end-self.origin, .6, places=5)
        self.assertEqual([s.frame_count for s in cap.segments], [3, 3])
        self.assertLess(cap.segments[0].capture_end, cap.segments[1].capture_start)
        self.assertFalse(self.capture.status('event')['complete'])
        report = capture_payload(self.capture.status('event'), camera_id='ref',
            clock=SimpleNamespace(offset=0, error_bound=0, verified=False), session_id='a'*32,
            sequence=1, time_basis='source_utc_unverified', required_end=self.origin+.8)
        self.assertEqual(len(report['captured_ranges']), 2)
        self.assertEqual([r['reason'] for r in report['missing_ranges']],
                         ['source_input_gap', 'missing_post_roll'])
        self.assertEqual(report['state'], 'incomplete')

    def test_irregular_final_partial_does_not_extend_past_one_observed_frame(self):
        for offset in (1/15, .14):
            self.capture.ingest('main', self.origin+offset, self.frame)
        self.capture.end_source('main')
        cap = self.capture.events['event']['captures']['main']
        self.assertTrue(cap.done)
        self.assertEqual(len(cap.segments), 1)
        end = cap.segments[0].capture_end
        self.assertGreater(end, self.origin+.14)
        self.assertLessEqual(end, self.origin+.14+1/15)
        self.assertAlmostEqual(end-self.origin, .2, places=5)

    def test_flush_failure_has_bounded_durable_discard_reason(self):
        self.capture.ingest('ref', self.origin+1/15, self.frame)
        cap = self.capture.events['event']['captures']['ref']
        with patch.object(cap.writer, 'finish', side_effect=ValueError(
                'end boundary must align to output frame grid')):
            self.capture.end_source('ref')
        reasons = cap.spool.discard_receipts()['items']
        self.assertEqual(len(reasons), 1)
        self.assertEqual(reasons[0]['reason'], 'capture_flush_grid_error')
        self.assertIn('capture_flush_grid_error', cap.reasons)
