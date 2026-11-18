import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from video_sender.capture_control import PostRoll, observation_stop_path, publish_result
from video_sender.context_sender import SessionRunner
from video_sender.context_starter import Starter


class Client:
    origin = 'https://source.test'

    def __init__(self):
        self.calls = []
        self.fail_ack = True

    def post(self, path, body):
        route = path.rsplit('/', 1)[-1]
        self.calls.append((route, body))
        if route == 'observation-stop':
            return {'observation_stop': dict(body, frame_index=3) if self.fail_ack else body}
        return {'retired': True}

    def upload(self, stream, sequence, path, captured, *, clock_proof_id):
        self.calls.append(('upload', sequence, clock_proof_id))


class GracefulStopSenderTests(TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.client = Client()
        with patch.dict('os.environ', {'VIDEO_NORMAL_STOP_ENABLED': 'true', 'VIDEO_CLOCK_PROOF_ENABLED': 'true'}):
            self.runner = SessionRunner.create(self.root / 'session', self.client, 'CAM_001')
        self.runner.state.update(context_id=str(uuid4()), capture_started=True, clock={})
        self.runner.save()
        self.run = self.runner.state['run_id']
        self.stream = self.runner.state['stream_id']
        self.proof_id = str(uuid4())
        self.request = self.runner.root / f'.normal-stop-{self.run}'
        self.marker = observation_stop_path(self.runner.root, self.run)
        self.roll = PostRoll(self.runner.root, self.run, self.stream, 0, 15)

    def test_boundary_and_tail_use_submitted_frame_grid_and_observed_samples(self):
        self.roll.before_frame(16, 1, self.proof_id)
        self.assertFalse(self.marker.exists())
        self.request.touch()
        self.roll.before_frame(17, 2, self.proof_id)
        self.request.touch()
        self.roll.before_frame(18, 2.1, str(uuid4()))
        self.assertEqual(json.loads(self.marker.read_text()), {
            'stream_id': self.stream, 'run_id': self.run, 'fps': 15,
            'sequence': 1, 'frame_index': 2, 'proof_id': self.proof_id,
        })
        self.assertFalse(self.roll.complete(18, 20))
        self.assertFalse(self.roll.complete(49, 3.99))
        self.assertFalse(self.roll.complete(48, 4))
        self.assertTrue(self.roll.complete(49, 4))

    def test_ready_chunk_race_lost_ack_and_exact_recovery_before_end(self):
        root = self.runner.root
        p = root / 'chunk_00000.mp4'
        p.write_bytes(b'clip')
        p.with_suffix('.txt').write_text('2026-11-18T00:00:00Z')
        p.with_suffix('.clock.json').write_text(json.dumps({'proof_id': self.proof_id}))

        def completed_chunks(directory):
            # The capture process publishes the cutoff between the sender's
            # first marker check and its view of the now-ready chunk.
            self.request.touch()
            self.roll.before_frame(0, 2, self.proof_id)
            p.with_suffix('.ready').touch()
            return [p]

        def clip(n):
            path = root / f'chunk_{n:05d}.mp4'
            path.write_bytes(b'clip')
            path.with_suffix('.txt').write_text('2026-11-18T00:00:00Z')
            path.with_suffix('.clock.json').write_text(json.dumps({'proof_id': self.proof_id}))
            path.with_suffix('.ready').touch()

        with patch('video_sender.context_sender.completed_chunks', side_effect=completed_chunks):
            with self.assertRaisesRegex(ValueError, 'observation_stop_acknowledgement_mismatch'):
                self.runner.drain()
        self.assertEqual([entry[0] for entry in self.client.calls], ['observation-stop'])
        self.assertTrue(p.exists())
        self.assertEqual(self.runner.state['acknowledged_sequence'], -1)
        clip(1)
        clip(2)
        publish_result(root, self.run, stopped_by_request=True, ffmpeg_exit=0, start_sequence=0,
                       frame_count=32, fps=15, normal_stop_completed=True)
        self.client.fail_ack = False
        resumed = SessionRunner(root, self.client)
        with patch('video_sender.clock_source.HistoricalClockDrain'), \
             patch('video_sender.context_sender.get_video_duration_ms', return_value=1000):
            resumed.finish()
        self.assertEqual([entry[0] for entry in self.client.calls],
                         ['observation-stop', 'observation-stop', 'upload', 'upload', 'upload', 'stop'])
        self.assertEqual(self.client.calls[0], self.client.calls[1])
        self.assertTrue(resumed.state['closed'])
        self.assertTrue(self.marker.exists())

    def test_interrupted_post_roll_never_sends_stop_ack(self):
        self.request.touch()
        self.roll.before_frame(4, 2, self.proof_id)
        self.runner.request_stop()
        self.assertTrue((self.runner.root / f'.stop-{self.run}').is_file())
        publish_result(self.runner.root, self.run, stopped_by_request=False, ffmpeg_exit=0,
                       start_sequence=0, frame_count=8, fps=15)
        with self.assertRaisesRegex(ValueError, 'capture_completion_unverified'):
            self.runner.finish()
        self.assertEqual(self.client.calls, [])
        self.assertFalse(self.runner.state['closed'])

    def test_active_and_restarted_gui_use_durable_normal_request(self):
        starter = Starter(self.root)
        starter.directory = self.runner.root
        starter.process = SimpleNamespace(poll=lambda: None)
        starter.request_stop()
        self.assertTrue(self.request.exists())
        self.assertFalse((self.runner.root / f'.stop-{self.run}').exists())
        restarted = Starter(self.root)
        restarted.directory = self.runner.root
        self.assertIn('종료 처리 중', starter.status())
        self.assertIn('종료 처리 중', restarted.status())
        self.assertFalse(self.runner.state['closed'])
        starter.request_stop(immediate=True)
        self.assertTrue((self.runner.root / f'.stop-{self.run}').exists())


    def peer_session(self):
        client = Client()
        with patch.dict('os.environ', {'VIDEO_NORMAL_STOP_ENABLED': 'true', 'VIDEO_CLOCK_PROOF_ENABLED': 'true'}):
            peer = SessionRunner.create(self.root / 'peer', client, 'CAM_002')
        peer.state.update(context_id=str(uuid4()), capture_started=True, clock={})
        peer.save()
        return peer

    def controller(self, peer):
        from video_sender.automatic_dual import AutomaticDual
        controller = AutomaticDual.__new__(AutomaticDual)
        controller.slots = []
        for runner in (self.runner, peer):
            starter = Starter(self.root)
            starter.directory = runner.root
            controller.slots.append({'camera': runner.state['camera_id'], 'starter': starter})
        return controller

    def test_paired_stop_waits_for_later_camera_actual_and_frame_grid_timestamps(self):
        peer = self.peer_session()
        controller = self.controller(peer)
        self.assertEqual(controller.stop_all(), [])
        peer_run = peer.state['run_id']
        peer_roll = PostRoll(peer.root, peer_run, peer.state['stream_id'], 0, 15)
        self.roll.before_frame(10, 2, self.proof_id, 1002)
        self.assertFalse(self.roll.complete(45, 4.3, 1004.3))
        peer_roll.before_frame(20, 2.8, str(uuid4()), 1003)
        self.assertFalse(self.roll.complete(58, 4.9, 1005.2))
        self.assertFalse(self.roll.complete(58, 5.2, 1005.1))
        self.assertTrue(self.roll.complete(58, 5.2, 1005.2))
        self.assertTrue(peer_roll.complete(55, 5.2, 1005.2))

    def test_paired_stop_missing_peer_is_bounded_and_retains_marker(self):
        peer = self.peer_session()
        self.assertEqual(self.controller(peer).stop_all(), [])
        now = 3.0
        roll = PostRoll(self.runner.root, self.run, self.stream, 0, 15, monotonic=lambda: now)
        roll.before_frame(0, 3, self.proof_id, 1003)
        self.assertFalse(roll.complete(55, 6, 1006))
        now = 14
        with self.assertRaisesRegex(RuntimeError, 'normal_stop_peer_timeout'):
            roll.check_deadline()
        self.assertTrue(self.marker.exists())

    def test_restarted_gui_preserves_pair_and_dispatches_second_when_first_fails(self):
        peer = self.peer_session()
        controller = self.controller(peer)
        first = controller.slots[0]['starter']
        peer_request = peer.root / f".normal-stop-{peer.state['run_id']}"
        with patch.object(first, '_publish_stop', side_effect=OSError):
            self.assertEqual(controller.stop_all(), ['CAM_001'])
        self.assertTrue(peer_request.exists())
        saved = peer_request.read_bytes()
        restarted = self.controller(peer)
        self.assertEqual(restarted.stop_all(), [])
        self.assertEqual(restarted.stop_all(), [])
        self.assertTrue(self.request.exists())
        self.assertEqual(peer_request.read_bytes(), saved)
        self.assertEqual(json.loads(self.request.read_text())['run_id'], peer.state['run_id'])

