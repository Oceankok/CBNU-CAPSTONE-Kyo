"""Capture obligations change explicitly; event observations do not."""
from datetime import datetime, timedelta, timezone
import json
import os
import unittest
from unittest.mock import patch

from backend.capture_grid import required_capture_end, validate_output_fps
from backend.processing.lifecycle import Start, canonical
from backend.tests import test_continuous_events as fixtures


class CaptureGridMathTests(unittest.TestCase):
    def test_actual_seven_event_ends_and_microsecond_wire(self):
        # Four distinct intervals among seven failed CAM02 reports, UTC.
        for start, end, count in [
            ('16:00:51.614161', '16:01:03.144494', 173),
            ('16:01:22.728494', '16:01:37.736494', 226),
            ('16:01:22.728494', '16:01:37.736494', 226),
            ('16:01:46.136494', '16:02:04.604161', 278),
            ('16:01:46.136494', '16:02:04.604161', 278),
            ('16:02:03.061827', '16:02:19.530494', 248),
            ('16:02:03.061827', '16:02:19.530494', 248),
        ]:
            a = datetime.fromisoformat('2026-11-15T' + start + '+00:00')
            b = datetime.fromisoformat('2026-11-15T' + end + '+00:00')
            with self.subTest(start=start, end=end):
                actual = required_capture_end(a + timedelta(seconds=2), b - timedelta(seconds=2), 15)
                self.assertGreaterEqual(actual, b)
                self.assertLess((actual-b).total_seconds(), 1/15)
                self.assertAlmostEqual((actual-a).total_seconds()*15, count, delta=15e-6)
                self.assertEqual(required_capture_end(a+timedelta(seconds=2), b-timedelta(seconds=2)), b)

    def test_exact_grid_has_no_extra_frame_at_every_supported_rate(self):
        a = datetime(2026, 11, 15, tzinfo=timezone.utc)
        for fps in range(1, 121):
            self.assertEqual(required_capture_end(a, a+timedelta(seconds=3), fps), a+timedelta(seconds=5))
        origin=datetime.fromtimestamp(1794758493.8698273,tz=timezone.utc)
        nominal=datetime.fromtimestamp(1794758502.003161,tz=timezone.utc)
        result=required_capture_end(origin+timedelta(seconds=2),nominal-timedelta(seconds=2),15)
        self.assertAlmostEqual((result-origin).total_seconds()*15,122,delta=15e-6)
        for invalid in (True, 0, -1, 121, 15.0, float('inf'), float('nan'), '15'):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                validate_output_fps(invalid)


class CaptureGridApiTests(unittest.TestCase):
    setUp = fixtures.ContinuousEventTests.setUp
    ts = fixtures.ContinuousEventTests.ts
    start = fixtures.ContinuousEventTests.start
    progress = fixtures.ContinuousEventTests.progress
    segment = fixtures.ContinuousEventTests.segment
    capture_report = fixtures.ContinuousEventTests.capture_report

    def test_legacy_bytes_retry_and_strict_rate(self):
        legacy = canonical(Start.model_validate(self.body))
        self.assertNotIn('capture_output_fps', json.loads(legacy))
        self.assertEqual(legacy, canonical(Start.model_validate({**self.body, 'capture_output_fps':None})))
        for rate in (0, 121, True, 15.0, '15'):
            self.assertEqual(self.start(capture_output_fps=rate).status_code, 422)
        self.start().raise_for_status()
        self.assertEqual(self.start().status_code, 200)
        self.assertEqual(self.start(capture_output_fps=15).status_code, 409)
        self.assertEqual(self.segment(end=5.01).status_code, 422)

    def test_opted_in_interval_advertised_manifest_close_report_consistent(self):
        response = self.start(capture_output_fps=15, timestamp_end=self.ts(3.008), observed_seconds=3.008)
        response.raise_for_status()
        expected = self.ts(5.066667)
        for camera in response.json()['cameras']:
            self.assertEqual(datetime.fromisoformat(camera['required_end_at']), datetime.fromisoformat(expected))
        self.assertEqual(self.start(capture_output_fps=15,timestamp_end=self.ts(3.008),observed_seconds=3.008).status_code,200)
        self.assertEqual(self.segment(end=5.066669).status_code,422)
        self.segment(end=5.066667).raise_for_status()
        closed = self.progress(kind='close', end=3.008, observed_seconds=3.008,
            close_reason='ppe_present', complete=True, final_segment_count=1)
        closed.raise_for_status()
        with patch.dict(os.environ, {'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS':'0'}):
            short = [{'start_at':self.ts(-2), 'end_at':self.ts(5.008)}]
            self.assertEqual(self.capture_report(state='complete', captured_ranges=short).status_code,409)
            full = [{'start_at':self.ts(-2), 'end_at':expected}]
            self.capture_report(state='complete', captured_ranges=full).raise_for_status()
        event = self.client.get('/api/events/'+self.ident,headers=self.admin).json()['event']
        self.assertEqual(datetime.fromisoformat(event['timestamp_end']), self.t+timedelta(seconds=3.008))
