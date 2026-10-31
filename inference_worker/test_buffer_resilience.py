"""Bounded frame retention and lazy MP4 decoding without a GPU model."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from inference_worker.clip_buffer import ClipBuffer
from inference_worker.video_decoder import decode_mp4


class BufferBudgetTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 31, tzinfo=timezone.utc)
        self.frame = np.zeros((4, 5, 3), dtype=np.uint8)

    def test_memory_eviction_keeps_newest_frames_with_exact_accounting(self):
        buffer = ClipBuffer(max_bytes=self.frame.nbytes*2)
        for index in range(20):
            dropped = buffer.add(self.start+timedelta(seconds=index), self.frame)
            self.assertEqual(dropped, int(index >= 2))
            self.assertLessEqual(buffer.bytes_used, buffer.max_bytes)
            self.assertEqual(buffer.bytes_used, sum(frame.nbytes for _, frame in buffer.frames))
        self.assertEqual([int((stamp-self.start).total_seconds()) for stamp,_ in buffer.frames], [18,19])

    def test_time_eviction_releases_bytes_and_retained_frame_is_independent(self):
        buffer = ClipBuffer(retention_seconds=1, max_bytes=1000)
        buffer.add(self.start, self.frame)
        self.frame[:] = 255
        self.assertFalse(buffer.frames[0][1].any())
        self.assertEqual(buffer.add(self.start+timedelta(seconds=2), self.frame), 0)
        self.assertEqual(buffer.bytes_used, self.frame.nbytes)

    def test_oversized_frame_does_not_destroy_existing_buffer(self):
        buffer = ClipBuffer(max_bytes=self.frame.nbytes)
        buffer.add(self.start, self.frame)
        with self.assertRaisesRegex(ValueError, 'Frame exceeds'):
            buffer.add(self.start+timedelta(seconds=1), np.zeros((100,100,3), dtype=np.uint8))
        self.assertEqual(len(buffer.frames), 1)
        self.assertEqual(buffer.bytes_used, self.frame.nbytes)

    def test_invalid_budget_and_reverse_timestamp_are_rejected(self):
        for budget in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                ClipBuffer(max_bytes=budget)
        buffer = ClipBuffer(max_bytes=1000)
        buffer.add(self.start, self.frame)
        with self.assertRaises(ValueError):
            buffer.add(self.start-timedelta(seconds=1), self.frame)


class StreamingDecoderTests(unittest.TestCase):
    def sample(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'sample.mp4'
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 6, (64,48))
            self.assertTrue(writer.isOpened())
            try:
                for _ in range(12):
                    writer.write(np.zeros((48,64,3), dtype=np.uint8))
            finally:
                writer.release()
            return path.read_bytes()

    def test_lazy_timing_uses_declared_capture_interval_and_close_erases_temp(self):
        with patch('inference_worker.video_decoder.os.remove', wraps=os.remove) as remove:
            frames = decode_mp4(self.sample(), with_timing=True, duration_ms=2100)
            index, fps, frame = next(frames)
            self.assertEqual(index, 0)
            self.assertAlmostEqual(fps, 12/2.1)
            self.assertEqual(frame.shape, (48,64,3))
            remove.assert_not_called()
            frames.close()
            remove.assert_called_once()
            self.assertFalse(Path(remove.call_args.args[0]).exists())

    def test_legacy_decoder_interface_is_preserved(self):
        frames = list(decode_mp4(self.sample()))
        self.assertEqual(len(frames), 12)
        self.assertEqual(frames[0].shape, (48,64,3))

    def test_metadata_failure_erases_temp_before_return(self):
        with patch('inference_worker.video_decoder.os.remove', wraps=os.remove) as remove:
            with self.assertRaisesRegex(RuntimeError, 'duration mismatch'):
                next(decode_mp4(self.sample(), with_timing=True, duration_ms=100))
            remove.assert_called_once()
            self.assertFalse(Path(remove.call_args.args[0]).exists())
