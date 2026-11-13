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

    def run_recovery(self, children, *, limit=2, cycles=5, reference_limit=0):
        with TemporaryDirectory() as folder:
            stop = Path(folder)/'stop'
            clock = [0.0]
            def sleep(_):
                clock[0] += 1
                if clock[0] >= cycles:
                    stop.touch()
            with patch('inference_worker.run_multi.subprocess.Popen', side_effect=children) as spawn, \
                 patch('inference_worker.run_multi.time.monotonic', side_effect=lambda:clock[0]), \
                 patch('inference_worker.run_multi.time.sleep', side_effect=sleep):
                supervise([['camera-a'], ['camera-b'], ['reference']], stop,
                          camera_count=2, restart_limit=limit, restart_delay=2,
                          reference_restart_limit=reference_limit, reference_restart_delay=2)
                return spawn.call_args_list

    @staticmethod
    def child(exit_code=None):
        child = Mock()
        child.poll.return_value = exit_code
        child.returncode = exit_code
        return child

    def test_opt_in_restarts_only_failed_camera(self):
        failed, healthy, reference, recovered = self.child(1), self.child(), self.child(), self.child()
        calls = self.run_recovery([failed, healthy, reference, recovered])
        self.assertEqual([c.args[0] for c in calls], [['camera-a'], ['camera-b'], ['reference'], ['camera-a']])
        healthy.terminate.assert_not_called()
        reference.terminate.assert_not_called()
        recovered.wait.assert_called()

    def test_reference_failure_remains_fail_fast(self):
        with self.assertRaisesRegex(RuntimeError, 'all peers'):
            self.run_recovery([self.child(), self.child(), self.child(1)])

    def test_repeated_camera_failure_exhausts_budget(self):
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
            self.run_recovery([self.child(1), self.child(), self.child(), self.child(1)], limit=1)

    def test_relaunch_oserror_consumes_budget(self):
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
            self.run_recovery([self.child(1), self.child(), self.child(), OSError('not launchable')], limit=1)

    def test_stop_during_backoff_does_not_restart(self):
        calls = self.run_recovery([self.child(1), self.child(), self.child()], cycles=1)
        self.assertEqual(len(calls), 3)

    def test_successful_exit_is_restarted_in_explicit_continuous_supervision(self):
        calls = self.run_recovery([self.child(0), self.child(), self.child(), self.child()])
        self.assertEqual(len(calls), 4)

    def test_invalid_restart_policy_rejected_before_launch(self):
        with TemporaryDirectory() as folder, patch('inference_worker.run_multi.subprocess.Popen') as spawn:
            for kwargs in ({'restart_limit':-1}, {'restart_limit':1},
                           {'restart_delay':float('nan')}, {'restart_delay':float('inf')},
                           {'camera_count':4}):
                with self.assertRaises(ValueError):
                    supervise([['a']], Path(folder)/'stop', **kwargs)
            spawn.assert_not_called()

    def test_reference_opt_in_does_not_restart_healthy_cameras(self):
        a, b, failed, recovered = self.child(), self.child(), self.child(1), self.child()
        calls = self.run_recovery([a, b, failed, recovered], reference_limit=1, limit=0)
        self.assertEqual([c.args[0] for c in calls], [['camera-a'], ['camera-b'], ['reference'], ['reference']])
        a.terminate.assert_not_called()
        b.terminate.assert_not_called()
        recovered.wait.assert_called()

    def test_reference_exhaustion_and_launch_failure_are_bounded(self):
        for result in (self.child(1), OSError('launch failed')):
            with self.subTest(result=result), self.assertRaisesRegex(RuntimeError, 'Reference restart budget'):
                self.run_recovery([self.child(), self.child(), self.child(1), result], reference_limit=1)

    def test_reference_backoff_respects_stop(self):
        calls = self.run_recovery([self.child(), self.child(), self.child(1)], reference_limit=1, cycles=1)
        self.assertEqual(len(calls), 3)

    def test_camera_and_reference_have_independent_budgets(self):
        calls = self.run_recovery([self.child(1), self.child(), self.child(1), self.child(), self.child()],
                                  limit=1, reference_limit=1)
        self.assertEqual([c.args[0] for c in calls][-2:], [['camera-a'], ['reference']])

    def test_reference_policy_validation_before_launch(self):
        with TemporaryDirectory() as folder, patch('inference_worker.run_multi.subprocess.Popen') as spawn:
            for kwargs in ({'reference_restart_limit':-1},
                           {'reference_restart_delay':float('nan')},
                           {'reference_restart_delay':float('inf')},
                           {'reference_restart_limit':1},
                           {'reference_restart_limit':1, 'reference_restart_delay':1, 'camera_count':1}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    supervise([['reference']], Path(folder)/'stop', **kwargs)
            spawn.assert_not_called()
