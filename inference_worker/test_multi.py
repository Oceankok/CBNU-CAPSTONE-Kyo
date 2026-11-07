"""Supervisor lifecycle checks: no actual inference child is launched."""
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest
from unittest.mock import Mock, patch

from inference_worker.run_multi import commands, supervise


class MultiCameraTests(unittest.TestCase):
    def test_independent_camera_commands_and_single_reference_collector(self):
        result = commands(['CAM_A', 'CAM_B'], Path('model.pt'), Path('stop'),
                          mode='--submit-review-candidates')
        self.assertEqual(len(result), 3)
        self.assertEqual([args[args.index('--camera-id')+1] for args in result[:2]], ['CAM_A','CAM_B'])
        self.assertTrue(all('--submit-review-candidates' in args for args in result[:2]))
        self.assertIn('inference_worker.reference_collector', result[2])
        self.assertFalse(any('token' in arg.lower() for args in result for arg in args))

    def test_child_failure_stops_all_peers_using_shared_stop_signal(self):
        with TemporaryDirectory() as folder:
            stop = Path(folder)/'stop'
            children = [Mock(), Mock(), Mock()]
            children[0].poll.return_value = 1
            with patch('inference_worker.run_multi.subprocess.Popen', side_effect=children):
                with self.assertRaisesRegex(RuntimeError, 'all peers'):
                    supervise([['camera-a'], ['camera-b'], ['reference']], stop)
            self.assertTrue(stop.exists())
            for child in children:
                child.wait.assert_called()

    def test_partial_start_failure_still_signals_started_camera(self):
        with TemporaryDirectory() as folder:
            stop = Path(folder)/'stop'
            child = Mock()
            with patch('inference_worker.run_multi.subprocess.Popen', side_effect=[child, OSError('failed')]):
                with self.assertRaises(OSError):
                    supervise([['a'], ['b']], stop)
            self.assertTrue(stop.exists())
            child.wait.assert_called()

    def test_hung_child_is_terminated_then_killed_after_grace_period(self):
        with TemporaryDirectory() as folder:
            stop = Path(folder)/'stop'
            child = Mock()
            child.poll.return_value = 1
            child.wait.side_effect = [subprocess.TimeoutExpired('fake', 70),
                                      subprocess.TimeoutExpired('fake', 5), 0]
            with patch('inference_worker.run_multi.subprocess.Popen', return_value=child):
                with self.assertRaises(RuntimeError):
                    supervise([['a']], stop)
            child.terminate.assert_called_once()
            child.kill.assert_called_once()
