"""Reference collection contracts with synthetic video; no model or external server."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

import cv2
import httpx
import numpy as np

from inference_worker.reference_collector import ReferenceCollector, ReferenceUnavailable, assemble, select_chunks


class ReferenceCollectorTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 31, tzinfo=timezone.utc)
        self.end = self.start+timedelta(seconds=2)
        self.chunks = [{'chunk_id': str(i), 'capture_start_at': (self.start+timedelta(seconds=i)).isoformat(),
                        'duration_ms': 1000} for i in range(2)]
        self.request = {'request_id': 'reference', 'event_id': 'event', 'camera_id': 'B',
                        'clip_start_at': self.start.isoformat(), 'clip_end_at': self.end.isoformat(),
                        'deadline_at': self.end.timestamp()+30}

    def video(self, folder):
        path = folder/'synthetic.mp4'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 5, (32, 24))
        self.assertTrue(writer.isOpened())
        try:
            for _ in range(5):
                writer.write(np.zeros((24, 32, 3), dtype=np.uint8))
        finally:
            writer.release()
        return path.read_bytes()

    def test_assembly_preserves_interval_and_cleans_chunk_file(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            client = Mock()
            client.download_chunk.return_value = self.video(folder)
            output = assemble(client, self.chunks, self.start, self.end, folder)
            cap = cv2.VideoCapture(str(output))
            try:
                self.assertEqual(cap.get(cv2.CAP_PROP_FPS), 5)
                self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT), 10)
            finally:
                cap.release()
            self.assertFalse((folder/'chunk.mp4').exists())

    def test_truncated_video_is_not_uploaded_as_complete(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            client = Mock()
            client.download_chunk.return_value = self.video(folder)
            chunks = [{**self.chunks[0], 'duration_ms': 2000}]
            with self.assertRaisesRegex(ReferenceUnavailable, 'reference_incomplete_decode'):
                assemble(client, chunks, self.start, self.end, folder)
            self.assertFalse((folder/'chunk.mp4').exists())

    def test_interval_selection_rejects_missing_head_middle_and_tail(self):
        self.assertEqual(select_chunks(self.chunks, self.start, self.end), self.chunks)
        for chunks in ([self.chunks[0]], [self.chunks[1]],
                       [self.chunks[0], {**self.chunks[1], 'capture_start_at': (self.start+timedelta(seconds=1.5)).isoformat()}]):
            self.assertEqual(select_chunks(chunks, self.start, self.end), [])

    def test_missing_camera_waits_then_reports_unavailable(self):
        client = Mock()
        clock = Mock(return_value=self.end.timestamp())
        collector = ReferenceCollector(client, clock=clock)
        collector.collect(self.request, [])
        client.report_clip_failure.assert_not_called()
        clock.return_value = self.request['deadline_at']
        collector.collect(self.request, [])
        client.report_clip_failure.assert_called_once_with('event', 'reference',
            'reference_interval_unavailable', status='unavailable')
        client.upload_clip.assert_not_called()

    def test_oversized_interval_has_explicit_reason(self):
        client = Mock()
        request = {**self.request, 'clip_end_at': (self.start+timedelta(seconds=21)).isoformat()}
        ReferenceCollector(client).collect(request, [])
        client.report_clip_failure.assert_called_once_with('event', 'reference',
            'reference_window_unsupported', status='unavailable')

    def test_one_source_failure_does_not_stop_other_requests(self):
        client = Mock()
        client.list_reference_requests.return_value = [self.request, {**self.request, 'request_id': 'other'}]
        collector = ReferenceCollector(client)
        collector.collect = Mock(side_effect=[httpx.ReadTimeout('offline'), None])
        collector.run_once()
        self.assertEqual(collector.collect.call_count, 2)
