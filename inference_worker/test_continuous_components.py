"""Synthetic, local component integration; not a live runner/API test."""
from collections import deque
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless

import cv2
import numpy as np

from inference_worker.event_lifecycle import EventLifecycle
from inference_worker.segment_spool import SegmentSpool, TimestampSegmentWriter


@skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
class ContinuousComponentsTests(TestCase):
    def test_long_event_and_short_detection_gap_keep_one_event_and_bounded_segments(self):
        # 64.9-second positive observation, followed by explicit positive PPE.
        # Complete evidence interval [-2,66.9) includes pre/post-roll. Frame
        # sampling is deliberately 10fps here so the boundary is exact.
        lifecycle = EventLifecycle('CAM', missing_grace_seconds=.5)
        person = {'tracking_id': 'track-1', 'policy_scope_key': ('v1', 'roi-A'),
                  'ppe': {'helmet': {'status': 'suspected_no_helmet'}}}
        frame = np.zeros((48,64,3), dtype=np.uint8)
        prebuffer = deque(maxlen=51)
        transitions, segments = [], []
        writer = None
        with TemporaryDirectory() as directory, SegmentSpool(
                Path(directory)/'raw-private', max_bytes=2*1024*1024,
                max_segments=4, max_age_seconds=120, max_attempts=3) as spool:
            for step in range(-20, 669):
                stamp = step/10
                rows = []
                if step >= 0:
                    if step >= 650:
                        person['ppe']['helmet']['status'] = 'helmet'
                    # A missing detection is not a missing source video frame.
                    rows = lifecycle.update([] if step == 100 else [person], stamp)
                transitions.extend(rows)
                if writer is None:
                    prebuffer.append((stamp, frame.copy()))
                    if any(row['kind']=='start' for row in rows):
                        writer = TimestampSegmentWriter(spool, segment_seconds=60,
                            output_fps=10, max_frame_pixels=640*480,
                            max_input_gap_seconds=.5, encoder_timeout_seconds=5)
                        for old_stamp, old_frame in prebuffer:
                            segments.extend(writer.push(old_stamp, old_frame))
                        prebuffer.clear()
                else:
                    segments.extend(writer.push(stamp, frame))
            segments.extend(writer.finish(66.9))
            starts = [row for row in transitions if row['kind']=='start']
            closes = [row for row in transitions if row['kind']=='close']
            self.assertEqual(len(starts),1)
            self.assertEqual(len(closes),1)
            self.assertEqual(starts[0]['event_id'], closes[0]['event_id'])
            self.assertEqual(closes[0]['end_time'],64.9)
            self.assertEqual(closes[0]['observation_gap_count'],1)
            self.assertEqual(len(segments),2)
            self.assertAlmostEqual(segments[0].capture_start,-2)
            self.assertAlmostEqual(segments[0].capture_end,58)
            self.assertAlmostEqual(segments[1].capture_start,58)
            self.assertAlmostEqual(segments[1].capture_end,66.9)
            for row in segments:
                capture = cv2.VideoCapture(str(spool.path_for_upload(row.segment_id)))
                try:
                    self.assertTrue(capture.isOpened())
                    actual = capture.get(cv2.CAP_PROP_FRAME_COUNT)/capture.get(cv2.CAP_PROP_FPS)
                    self.assertAlmostEqual(actual,row.capture_end-row.capture_start,places=4)
                    self.assertLessEqual(actual,60)
                finally:
                    capture.release()
                spool.mark_attempt(row.segment_id)
                spool.acknowledge(row.segment_id)
            self.assertEqual(spool.pending(),[])
            self.assertEqual(list((Path(directory)/'raw-private').glob('*.mp4')),[])

    def test_source_gap_cannot_be_reported_as_a_complete_clip(self):
        from inference_worker.segment_spool import SpoolError
        from inference_worker.continuous_payloads import update_payload
        lifecycle = EventLifecycle('CAM', missing_grace_seconds=1)
        person = {'tracking_id': 'track-1', 'ppe': {'helmet': {'status': 'suspected_no_helmet'}}}
        frame = np.zeros((48,64,3), dtype=np.uint8)
        with TemporaryDirectory() as directory, SegmentSpool(
                Path(directory)/'raw-private', max_bytes=2*1024*1024,
                max_segments=4, max_age_seconds=120, max_attempts=3) as spool:
            writer = TimestampSegmentWriter(spool, segment_seconds=60,
                output_fps=10, max_frame_pixels=640*480,
                max_input_gap_seconds=.5, encoder_timeout_seconds=5)
            for index in range(31):
                lifecycle.update([person], index/10)
                writer.push(index/10, frame)
            with self.assertRaisesRegex(SpoolError, 'input_gap'):
                writer.push(3.6, frame)
            close = lifecycle.close_all('source_gap')[0]
            body = update_payload(close, session_id='a'*32, sequence=1,
                                  complete=False, final_segment_count=0)
            self.assertFalse(body['complete'])
            self.assertEqual(body['close_reason'], 'source_gap')
            self.assertEqual(spool.pending(), [])
            self.assertFalse(list((Path(directory)/'raw-private').glob('*.part')))
