"""Supervisor lifecycle checks: no actual inference child is launched."""
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest
from unittest.mock import Mock, patch

from inference_worker.run_multi import commands, supervise, main, child_main, run_preflight, preflight, FATAL_EXIT


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

    def run_recovery(self, children, *, limit=2, cycles=5, reference_limit=0, policy='fail-fast'):
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
                          reference_restart_limit=reference_limit, reference_restart_delay=2,
                          failure_policy=policy)
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

    def test_isolate_exhaustion_keeps_healthy_peers(self):
        for failed_slot in (0, 2):
            for relaunch in (self.child(1), OSError('failed')):
                with self.subTest(slot=failed_slot, relaunch=relaunch):
                    children = [self.child(), self.child(), self.child()]
                    children[failed_slot] = self.child(1)
                    calls = self.run_recovery([*children, relaunch], limit=1,
                                              reference_limit=1, cycles=8, policy='isolate')
                    self.assertEqual(len(calls), 4)

    def test_isolate_normal_exit_is_not_restarted(self):
        calls = self.run_recovery([self.child(0), self.child(), self.child()], policy='isolate')
        self.assertEqual(len(calls), 3)

    def test_isolate_all_cameras_unavailable_stops_reference(self):
        reference = self.child()
        with self.assertRaisesRegex(RuntimeError, 'No active camera'):
            self.run_recovery([self.child(1), self.child(1), reference], limit=0, policy='isolate')
        reference.wait.assert_called()

    def test_fatal_errors_stop_every_policy_without_retry(self):
        for code in (2, FATAL_EXIT):
            for policy in ('isolate', 'fail-fast'):
                with self.subTest(code=code, policy=policy), self.assertRaisesRegex(RuntimeError, 'configuration/auth'):
                    self.run_recovery([self.child(code), self.child(), self.child()], policy=policy)

    def test_isolate_stop_during_backoff_does_not_restart(self):
        calls = self.run_recovery([self.child(1), self.child(), self.child()], cycles=1, policy='isolate')
        self.assertEqual(len(calls), 3)

    def test_existing_stop_does_not_launch_anything(self):
        with TemporaryDirectory() as folder, patch('inference_worker.run_multi.subprocess.Popen') as spawn:
            stop = Path(folder)/'stop'
            stop.touch()
            supervise([['a']], stop, camera_count=1, failure_policy='isolate')
            spawn.assert_not_called()

    def test_default_launcher_arguments_select_isolation_and_preflight(self):
        # Exact argument shape emitted by the host inference_entry.arguments.
        with TemporaryDirectory() as folder:
            model = Path(folder)/'model.pt'
            model.touch()
            args = ['--model-path', str(model), '--buffer-mib', '256',
                    '--camera-id', 'CAM_A', '--camera-id', 'CAM_B', '--submit-review-candidates']
            with patch('inference_worker.run_multi.run_preflight') as pre, \
                 patch('inference_worker.run_multi.supervise') as supervisor:
                main(args)
            self.assertIn('--preflight-only', pre.call_args.args[0])
            self.assertEqual(supervisor.call_args.kwargs['failure_policy'], 'isolate')
            self.assertEqual(supervisor.call_args.kwargs['restart_limit'], 3)
            self.assertEqual(supervisor.call_args.kwargs['restart_delay'], 5)
            self.assertEqual(supervisor.call_args.kwargs['reference_restart_limit'], 3)
            with patch('inference_worker.run_multi.run_preflight'), \
                 patch('inference_worker.run_multi.supervise') as supervisor:
                main([*args, '--failure-policy', 'fail-fast', '--camera-restart-limit', '0',
                      '--reference-restart-limit', '0'])
            self.assertEqual(supervisor.call_args.kwargs['failure_policy'], 'fail-fast')
            self.assertEqual(supervisor.call_args.kwargs['restart_limit'], 0)

    def test_preflight_failure_prevents_peers(self):
        with TemporaryDirectory() as folder:
            model = Path(folder)/'model.pt'
            model.touch()
            with patch('inference_worker.run_multi.run_preflight', side_effect=RuntimeError('preflight')), \
                 patch('inference_worker.run_multi.supervise') as supervisor:
                with self.assertRaises(RuntimeError):
                    main(['--model-path', str(model), '--camera-id', 'CAM_A'])
            supervisor.assert_not_called()

    def test_preflight_interrupt_reaps_child(self):
        child = self.child()
        child.wait.side_effect = [KeyboardInterrupt(), 0]
        with patch('inference_worker.run_multi.subprocess.Popen', return_value=child):
            with self.assertRaises(KeyboardInterrupt):
                run_preflight(['preflight'])
        child.terminate.assert_called_once()
        self.assertEqual(child.wait.call_count, 2)

    def test_child_error_classification_does_not_expose_exception_text(self):
        from io import StringIO
        for status, code in ((401, FATAL_EXIT), (403, FATAL_EXIT), (422, FATAL_EXIT), (503, 1)):
            error = RuntimeError('sensitive token')
            error.response = Mock(status_code=status)
            with patch('inference_worker.run_multi.runpy.run_module', side_effect=error), \
                 patch('sys.stdout', new_callable=StringIO) as output:
                self.assertEqual(child_main('inference_worker.run_server'), code)
            self.assertNotIn('sensitive token', output.getvalue())

    def test_preflight_checks_auth_model_mode_and_tracker_before_children(self):
        from types import SimpleNamespace
        # Keep these tests dependency-free: model, HTTP and tracker are explicit doubles.
        client, model, tracker = Mock(), Mock(supports_no_helmet=False), Mock()
        factory = Mock(return_value=model)
        modules = {'inference_worker.video_client': SimpleNamespace(VideoClient=lambda: client),
                   'inference_worker.run_server': SimpleNamespace(create_detector=factory),
                   'inference_worker.person_tracker': SimpleNamespace(PersonTracker=tracker)}
        with patch.dict('sys.modules', modules):
            preflight(['A', 'B'], Path('model.pt'), '--submit-review-candidates')
            self.assertEqual(client.get_ppe_policy.call_count, 2)
            client.close.assert_called_once()
            tracker.assert_called_once()
            with self.assertRaises(ValueError):
                preflight(['A'], Path('model.pt'), '--submit-events')
            factory.side_effect = RuntimeError('corrupt model')
            with self.assertRaisesRegex(RuntimeError, 'corrupt model'):
                preflight(['A'], Path('model.pt'), None)
            factory.reset_mock(side_effect=True)
            client.get_ppe_policy.side_effect = RuntimeError('auth denied')
            with self.assertRaisesRegex(RuntimeError, 'auth denied'):
                preflight(['A'], Path('model.pt'), None)
            factory.assert_not_called()
            self.assertEqual(client.close.call_count, 4)

    def test_preflight_tracker_dependency_failure_is_not_retried(self):
        from types import SimpleNamespace
        tracker = Mock(side_effect=RuntimeError('tracker dependency missing'))
        modules = {'inference_worker.video_client': SimpleNamespace(VideoClient=Mock),
                   'inference_worker.run_server': SimpleNamespace(create_detector=Mock),
                   'inference_worker.person_tracker': SimpleNamespace(PersonTracker=tracker)}
        with patch.dict('sys.modules', modules):
            with self.assertRaisesRegex(RuntimeError, 'tracker dependency missing'):
                preflight(['A'], Path('model.pt'), None)
        tracker.assert_called_once()

    def test_preflight_fatal_exit_is_sanitized(self):
        from io import StringIO
        with TemporaryDirectory() as folder:
            model = Path(folder)/'model.pt'
            model.touch()
            with patch('inference_worker.run_multi.preflight', side_effect=RuntimeError('secret')), \
                 patch('sys.stderr', new_callable=StringIO) as output:
                with self.assertRaises(SystemExit) as raised:
                    main(['--model-path', str(model), '--camera-id', 'A', '--preflight-only'])
            self.assertEqual(raised.exception.code, FATAL_EXIT)
            self.assertNotIn('secret', output.getvalue())
