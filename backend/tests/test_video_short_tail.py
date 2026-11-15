"""Real H264 tails use the ordinary upload and acknowledged-stop protocol."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import cv2
import httpx

from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login
from backend.video import service
from video_sender.capture_control import publish_result
from video_sender.context_sender import ContextClient, SessionRunner
from video_sender.live_sender import get_video_duration_ms


class ASGISession:
    """Adapt requests' file upload to TestClient without replacing API behavior."""
    def __init__(self, api):
        self.api = api
        self.headers = {}

    def post(self, url, *, data=None, headers=None, allow_redirects=False, **kwargs):
        return self.api.post(url, content=data.read() if data is not None else None,
                             headers=self.headers | (headers or {}),
                             follow_redirects=allow_redirects, **kwargs)


class ShortTailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            raise unittest.SkipTest('FFmpeg and ffprobe are required for real H264 tail tests')
        cls.fixtures = TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        cls.videos = {}
        for frames in (1, 2, 3, 15):
            path = Path(cls.fixtures.name) / f'{frames}-frames.mp4'
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                            '-f', 'lavfi', '-i', 'color=c=blue:s=160x120:r=15',
                            '-frames:v', str(frames), '-an', '-c:v', 'libx264',
                            '-pix_fmt', 'yuv420p', str(path)], check=True)
            cls.videos[frames] = path

    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.dict('os.environ', {
            'PPE_CAPTURE_CONTEXT_ENABLED': 'true',
            'VIDEO_BACKPRESSURE_ENABLED': 'false'}))
        self.enterContext(patch.object(service, 'ROOT', self.root / 'ingest'))
        self.api = self.enterContext(isolated_api())
        self.admin = login(self.api)
        token = self.api.post('/api/nodes', headers=self.admin,
                              json={'node_id': 'source', 'name': 'source'}).json()['token']
        self.headers = {'Authorization': 'Bearer ' + token}
        self.api.put('/api/cameras/CAM_001/node', headers=self.admin,
                     json={'source_node_id': 'source', 'output_node_id': 'source'}).raise_for_status()
        self.client = ContextClient('http://127.0.0.1', token, session=ASGISession(self.api))

    def runner(self, name):
        runner = SessionRunner.create(self.root / name, self.client, 'CAM_001')
        runner.connect()
        return runner

    def put_chunk(self, runner, sequence, frames, captured):
        path = runner.root / f'chunk_{sequence:05d}.mp4'
        shutil.copyfile(self.videos[frames], path)
        path.with_suffix('.txt').write_text(captured.isoformat(), encoding='utf-8')
        path.with_suffix('.ready').touch()
        return path

    def assert_frames(self, data, expected):
        path = self.root / 'check.mp4'
        path.write_bytes(data)
        cap = cv2.VideoCapture(str(path))
        try:
            count = 0
            while cap.read()[0]:
                count += 1
            self.assertEqual(count, expected)
        finally:
            cap.release()

    def assert_tail_preserves_bytes_time_and_normal_stop(self, frames):
        runner = self.runner(f'tail-{frames}')
        captured = datetime.now(timezone.utc)
        self.put_chunk(runner, 0, 15, captured)
        tail_time = captured + timedelta(seconds=1)
        self.put_chunk(runner, 1, frames, tail_time)
        # Model a completed physical capture before ordinary outbox draining.
        time.sleep(1.25)
        publish_result(runner.root, runner.state['run_id'], stopped_by_request=True,
                       ffmpeg_exit=0, start_sequence=0, frame_count=15 + frames, fps=15)
        runner.finish()
        self.assertTrue(runner.state['closed'])
        self.assertEqual(runner.state['acknowledged_sequence'], 1)
        self.assertEqual(list(runner.root.glob('chunk_*')), [])
        with get_connection() as conn:
            rows = conn.execute('SELECT * FROM video_chunk WHERE stream_id=? ORDER BY sequence',
                                (runner.state['stream_id'],)).fetchall()
            self.assertEqual(len(rows), 2)
            for row, original_frames, stamp in zip(rows, (15, frames), (captured, tail_time)):
                original = self.videos[original_frames]
                data = service.chunk_path(row['chunk_id']).read_bytes()
                self.assertEqual(data, original.read_bytes())
                self.assertEqual(row['capture_start_at'], service.capture_time(stamp.isoformat()))
                self.assertEqual(row['duration_ms'], get_video_duration_ms(original))
                self.assert_frames(data, original_frames)
            status = conn.execute('SELECT status,last_sequence,sequence_gap_count FROM video_stream WHERE stream_id=?',
                                  (runner.state['stream_id'],)).fetchone()
            self.assertEqual((status['status'], status['last_sequence'], status['sequence_gap_count']),
                             ('closed', 1, 0))
            context = conn.execute('SELECT valid_until FROM capture_context_registry WHERE context_id=?',
                                   (runner.state['context_id'],)).fetchone()
            self.assertIsNotNone(context['valid_until'])

    def test_one_frame_tail_preserves_bytes_time_and_normal_stop(self):
        self.assert_tail_preserves_bytes_time_and_normal_stop(1)

    def test_two_frame_tail_preserves_bytes_time_and_normal_stop(self):
        self.assert_tail_preserves_bytes_time_and_normal_stop(2)

    def test_three_frame_tail_preserves_bytes_time_and_normal_stop(self):
        self.assert_tail_preserves_bytes_time_and_normal_stop(3)

    def test_nonpositive_and_overmaximum_duration_still_rejected(self):
        runner = self.runner('invalid-duration')
        captured = datetime.now(timezone.utc).isoformat()
        for duration in (0, -1, 3001):
            with self.subTest(duration=duration):
                response = self.api.post(f"/api/video/streams/{runner.state['stream_id']}/chunks?sequence=0",
                    headers=self.headers | {'Content-Type': 'video/mp4', 'X-Capture-Start': captured,
                                            'X-Duration-Ms': str(duration)}, content=self.videos[1].read_bytes())
                self.assertEqual(response.status_code, 422, response.text)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM video_chunk').fetchone()[0], 0)

    def test_short_tail_keeps_size_quota_and_unacknowledged_stop_guards(self):
        runner = self.runner('guarded-tail')
        path = self.put_chunk(runner, 0, 1, datetime.now(timezone.utc))
        time.sleep(.1)
        publish_result(runner.root, runner.state['run_id'], stopped_by_request=True,
                       ffmpeg_exit=0, start_sequence=0, frame_count=1, fps=15)
        for field, expected in (('MAX_BYTES', 413), ('TOTAL_BYTES', 429)):
            with self.subTest(guard=field), patch.object(service, field, 1):
                with self.assertRaises(httpx.HTTPStatusError) as failure:
                    runner.finish()
                self.assertEqual(failure.exception.response.status_code, expected)
                self.assertTrue(path.is_file())
                self.assertEqual(runner.state['acknowledged_sequence'], -1)
                self.assertFalse(runner.state['closed'])
                with get_connection() as conn:
                    self.assertIsNone(conn.execute('SELECT valid_until FROM capture_context_registry WHERE context_id=?',
                                                   (runner.state['context_id'],)).fetchone()[0])
        runner.finish()
        self.assertTrue(runner.state['closed'])
