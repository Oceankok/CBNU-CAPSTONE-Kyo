"""Runner expiry and memory pressure checks with independent synthetic fixtures."""
from contextlib import nullcontext, redirect_stdout
import io
import sys
import unittest
from unittest.mock import patch

import httpx
import numpy as np

from inference_worker import run_server as runner
from inference_worker.test_server_mock import FakeVideoClient, FakeDetector, QualifiedCandidates


class RunnerResilienceTests(unittest.TestCase):
    def run_runner(self, client, *, submit=False, qualified=False, decoder=None,
                   detector=None, buffer_mib=256, frame_shape=(64,64,3)):
        frame = np.zeros(frame_shape, dtype=np.uint8)
        output = io.StringIO()
        args = ['run_server', '--camera-id', 'CAM_001', '--max-chunks', str(client.count),
                '--buffer-mib', str(buffer_mib)]
        if submit:
            args.append('--submit-events')
        candidates = patch.object(runner, 'ViolationTracker', side_effect=lambda **kwargs: QualifiedCandidates(client.start)) if qualified else nullcontext()
        with (
            patch.object(runner, 'VideoClient', return_value=client),
            patch.object(runner, 'create_detector', return_value=detector or FakeDetector()),
            patch.object(runner, 'decode_mp4', side_effect=decoder or (lambda *args, **kwargs: iter((index,4.0,frame) for index in range(4)))),
            patch.object(runner.time, 'sleep'),
            patch.object(sys, 'argv', args),
            patch.object(runner, 'submit_event_with_clip', return_value={'event_id': 'EVT_test'}) as delivered,
            candidates, redirect_stdout(output),
        ):
            try:
                runner.main()
            finally:
                self.assertTrue(client.closed)
                if client.loads:
                    self.assertFalse(client.loads[-1])
        return output.getvalue(), delivered

    def test_expired_chunk_resets_streak_and_continues_with_later_chunks(self):
        client = FakeVideoClient(8)
        def download(ident):
            if ident == '2':
                response = httpx.Response(404, request=httpx.Request('GET', 'http://test/chunk'))
                response.raise_for_status()
            return b'synthetic'
        client.download_chunk = download
        output, delivered = self.run_runner(client, submit=True, qualified=True)
        self.assertIn('[CHUNK EXPIRED]', output)
        self.assertIn('Expired chunks: 1', output)
        self.assertEqual(output.count('[OK]'), 7)
        # The pre-roll gap must not be hidden by combining frames across the expiry.
        delivered.assert_not_called()


    def test_permission_denial_is_not_treated_as_expired_video(self):
        client = FakeVideoClient(2)
        def denied(ident):
            httpx.Response(403, request=httpx.Request('GET', 'http://test/chunk')).raise_for_status()
        client.download_chunk = denied
        with self.assertRaises(httpx.HTTPStatusError):
            self.run_runner(client)
        self.assertTrue(client.closed)

    def test_inference_error_stops_lazy_decode_and_releases_its_files(self):
        state = {'consumed': 0, 'closed': False}
        def decode(*args, **kwargs):
            try:
                for index in range(100):
                    state['consumed'] += 1
                    yield index, 4.0, np.zeros((64,64,3), dtype=np.uint8)
            finally:
                state['closed'] = True
        class BrokenDetector:
            def detect(self, frame):
                raise RuntimeError('test detector failure')
        client = FakeVideoClient(2)
        with self.assertRaisesRegex(RuntimeError, 'test detector failure'):
            self.run_runner(client, decoder=decode, detector=BrokenDetector())
        self.assertEqual(state['consumed'], 1)
        self.assertTrue(state['closed'])
        self.assertTrue(client.closed)
        self.assertFalse(client.loads[-1])

    def test_memory_pressure_does_not_submit_partial_clips(self):
        output, delivered = self.run_runner(FakeVideoClient(8), submit=True, qualified=True,
            buffer_mib=8, frame_shape=(480,640,3))
        self.assertIn('[BUFFER LIMIT]', output)
        self.assertIn('[CLIP INCOMPLETE]', output)
        delivered.assert_not_called()
