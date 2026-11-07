"""Virtual-time live throughput; no real camera, GPU, network or waiting."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
import numpy as np

from inference_worker import run_server as runner
from inference_worker.test_server_mock import FakeVideoClient, FakeDetector, QualifiedCandidates


class LivePacingTests(unittest.TestCase):
    def exercise(self):
        clock = SimpleNamespace(now=0.0)
        client = FakeVideoClient(240)
        client.busy = False
        ages, expired, sleeps, delivered = [], [], [], []
        original_chunks = client.list_chunks
        original_streams = client.list_streams
        original_policy = client.get_ppe_policy

        def streams():
            clock.now += 0.02
            return original_streams()

        def chunks(stream, after_sequence=-1):
            clock.now += 0.02
            return [row for row in original_chunks(stream, after_sequence)
                    if int(row['chunk_id'])+1 <= clock.now]

        def policy(camera):
            clock.now += 0.02
            return original_policy(camera)

        def download(ident):
            clock.now += 0.02
            age = clock.now-(int(ident)+1)
            ages.append(age)
            client.downloads.append(ident)
            if age >= 30:
                expired.append(ident)
                httpx.Response(404, request=httpx.Request('GET', 'http://test/chunk')).raise_for_status()
            return b'synthetic'

        def load(busy, ttl_seconds=30, *, session_id=None):
            client.busy = busy
            client.loads.append(busy)
            clock.now += 0.005

        def sleep(seconds):
            sleeps.append((seconds, client.busy))
            clock.now += seconds
            if clock.now > 600:
                raise AssertionError('Live runner stalled')

        frame = np.zeros((64,64,3), dtype=np.uint8)

        class Detector(FakeDetector):
            def detect(self, image):
                clock.now += 0.25/15  # 250ms of analysis for each second of video.
                return super().detect(image)

        def clip(images, path, fps):
            Path(path).write_bytes(b'synthetic clip')
            return Path(path)

        def deliver(api, event, path):
            delivered.append(event['event_id'])
            self.assertFalse(client.busy)
            return {'event_id':event['event_id']}

        client.list_streams, client.list_chunks = streams, chunks
        client.get_ppe_policy, client.download_chunk = policy, download
        client.set_inference_load = load
        output = io.StringIO()
        with (patch.object(runner, 'VideoClient', return_value=client),
              patch.object(runner, 'create_detector', return_value=Detector()),
              patch.object(runner, 'ViolationTracker', side_effect=lambda **kw: QualifiedCandidates(client.start)),
              patch.object(runner, 'decode_mp4', side_effect=lambda *a,**kw: iter((i,15.0,frame) for i in range(15))),
              patch.object(runner, 'time', SimpleNamespace(sleep=sleep)),
              patch.object(runner, 'local_version', return_value={'revision':'fixture','dirty':False}),
              patch.object(runner, 'create_clip', side_effect=clip),
              patch.object(runner, 'submit_event_with_clip', side_effect=deliver),
              patch.object(sys, 'argv', ['runner','--camera-id','CAM_001','--max-chunks','240','--submit-events']),
              redirect_stdout(output)):
            runner.main()
        return clock, client, ages, expired, sleeps, delivered, output.getvalue()

    def test_four_minutes_of_live_video_do_not_accumulate_expired_chunks(self):
        clock, client, ages, expired, sleeps, delivered, output = self.exercise()
        self.assertEqual(expired, [], output[-1500:])
        self.assertEqual(client.downloads, [str(i) for i in range(240)])
        self.assertLess(max(ages), 3)
        self.assertLess(clock.now, 243)
        self.assertEqual(output.count('[OK]'), 240)
        self.assertEqual(delivered, [client.submitted_events[0]['event_id']])
        self.assertTrue(client.closed)

    def test_processing_yields_gpu_without_using_the_idle_poll_interval(self):
        _, client, _, _, sleeps, _, _ = self.exercise()
        self.assertTrue(any(delay == 1 for delay, _ in sleeps), 'No-video polling must remain bounded')
        short = [(delay,busy) for delay,busy in sleeps if delay < 1]
        self.assertEqual(len(short), 240)
        self.assertTrue(all(0 < delay <= 0.1 and not busy for delay,busy in short))
        self.assertFalse(client.busy)
