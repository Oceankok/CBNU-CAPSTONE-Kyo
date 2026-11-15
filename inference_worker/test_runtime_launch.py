"""Runtime launch contracts; no GPU, model loading, operating settings or HTTP."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import httpx

from inference_worker.run_multi import commands, continuous_profiles, main, child_main, run_preflight, FATAL_EXIT
from inference_worker.reference_collector import ReferenceCollector
from deploy.host_runtime.settings import arguments
from deploy.host_runtime.ownership import primary_ownership


def status_error(code):
    response = httpx.Response(code, request=httpx.Request('GET', 'https://fixture.invalid'))
    return httpx.HTTPStatusError('not logged', request=response.request, response=response)


class RuntimeLaunchTests(unittest.TestCase):
    def test_preflight_terminate_error_still_closes_owned_trees(self):
        trees = Mock()
        child = trees.spawn.return_value
        child.poll.return_value = None
        child.wait.side_effect = RuntimeError('injected wait failure')
        child.terminate.side_effect = OSError('injected termination failure')
        with patch('deploy.host_runtime.processes.ProcessTrees', return_value=trees):
            with self.assertRaisesRegex(OSError, 'termination failure'):
                run_preflight(['unused'], process_trees=True)
        trees.close.assert_called_once()

    def setUp(self):
        self.temp = self.enterContext(TemporaryDirectory())
        self.root = Path(self.temp)
        self.model = self.root/'model.pt'
        self.model.touch()
        self.a, self.b = self.root/'a.json', self.root/'b.json'
        self.a.write_text('{}'); self.b.write_text('{}')
        self.settings = {'camera_ids': ['A', 'B'], 'model_path': str(self.model), 'mode': 'review'}

    def test_legacy_missing_keys_keep_defaults_and_explicit_values_forward(self):
        args = arguments(self.settings)
        with patch('inference_worker.run_multi.run_preflight', return_value=True), \
             patch('inference_worker.run_multi.supervise') as supervisor:
            main(args)
        self.assertEqual(supervisor.call_args.kwargs['restart_limit'], 3)
        self.assertEqual(supervisor.call_args.kwargs['failure_policy'], 'isolate')
        settings = {**self.settings, 'failure_policy': 'fail-fast', 'camera_restart_limit': 0,
                    'reference_restart_limit': 0, 'preflight_timeout': 12, 'buffer_mib': 64}
        args = arguments(settings)
        self.assertIn('--preflight-timeout', args)
        with patch('inference_worker.run_multi.run_preflight', return_value=True) as pre, \
             patch('inference_worker.run_multi.supervise') as supervisor:
            main(args)
        self.assertEqual(pre.call_args.kwargs['timeout'], 12)
        self.assertEqual(supervisor.call_args.kwargs['restart_limit'], 0)

    def test_invalid_settings_fail_before_launch(self):
        for fields in ({'camera_ids': ['A', 'A']}, {'camera_ids': ['']}, {'mode': 'guess'},
                       {'failure_policy': 'loop'}, {'preflight_timeout': float('nan')},
                       {'camera_restart_limit': True}, {'camera_restart_delay': 0},
                       {'buffer_mib': 7}, {'PPE_PROCESSING_NODE_TOKEN': 'secret'},
                       {'continuous_config': str(self.a)}):
            with self.subTest(fields=fields), self.assertRaises((ValueError, TypeError)):
                arguments({**self.settings, **fields})

    def test_multi_primary_launches_once_each_and_never_collector(self):
        args = arguments({**self.settings, 'runner': 'continuous',
                          'continuous_profiles': {'A': str(self.a), 'B': str(self.b)}})
        with patch('inference_worker.run_multi.run_preflight', return_value=True), \
             patch('inference_worker.run_multi.supervise') as supervisor:
            main(args)
        launch = supervisor.call_args.args[0]
        self.assertEqual(len(launch), 2)
        self.assertEqual([c[c.index('--camera-id')+1] for c in launch], ['A', 'B'])
        self.assertTrue(all('--continuous-config' in c for c in launch))
        self.assertFalse(any('inference_worker.reference_collector' in c for c in launch))
        self.assertEqual(supervisor.call_args.kwargs['restart_limit'], 0)
        self.assertEqual(supervisor.call_args.kwargs['reference_restart_limit'], 0)

    def test_single_primary_does_not_claim_reference_detections(self):
        single = {**self.settings, 'camera_ids': ['A'], 'runner': 'continuous',
                  'primary_camera_id': 'A', 'continuous_config': str(self.a)}
        args = arguments(single)
        self.assertIn('--primary-camera-id', args)
        with self.assertRaisesRegex(ValueError, 'only its primary'):
            arguments({**single, 'camera_ids': ['A', 'B']})
        with self.assertRaises(ValueError):
            arguments({**single, 'camera_restart_limit': 1})
        with patch('inference_worker.run_multi.run_preflight') as pre:
            with self.assertRaises(SystemExit):
                main([*args, '--camera-restart-limit', '1'])
        pre.assert_not_called()

    def test_profiles_reject_duplicate_primaries_and_overlapping_state(self):
        for entries in ([f'A={self.a}', f'A={self.b}'], [f'A={self.a}']):
            with self.assertRaises(ValueError):
                continuous_profiles(['A', 'B'], entries=entries)
        with patch('inference_worker.run_multi.validate_continuous_selection',
                   return_value={'state_directory': str(self.root/'state')}):
            with self.assertRaisesRegex(ValueError, 'non-overlapping'):
                continuous_profiles(['A', 'B'], entries=[f'A={self.a}', f'B={self.b}'])
        with patch('inference_worker.run_multi.validate_continuous_selection',
                   side_effect=[{'state_directory': str(self.root/'state-a')},
                                {'state_directory': str(self.root/'state-b')}]):
            result = continuous_profiles(['A', 'B'], entries=[f'A={self.a}', f'B={self.b}'])
        self.assertEqual(set(result), {'A', 'B'})

    def test_preflight_rejects_uncalibrated_profile_before_peers(self):
        with patch('inference_worker.run_multi.continuous_profiles', side_effect=ValueError('calibration missing')), \
             patch('inference_worker.run_multi.preflight') as check, \
             patch('inference_worker.run_multi.supervise') as supervisor:
            with self.assertRaises(SystemExit) as result:
                main(['--model-path', str(self.model), '--camera-id', 'A', '--runner', 'continuous',
                      '--continuous-profile', f'A={self.a}', '--submit-review-candidates', '--preflight-only'])
        self.assertEqual(result.exception.code, FATAL_EXIT)
        check.assert_not_called(); supervisor.assert_not_called()

    def test_actual_profile_validator_rejects_expired_calibration_without_state_creation(self):
        from inference_worker.run_multi import validate_continuous_selection
        config = dict(state_directory=str(self.root/'never-created'),
            clocks={c: dict(offset=0, error_bound=0, verified=True, epoch='fixture',
                          valid_until='2020-01-01T00:00:00Z') for c in ('A', 'B')},
            capture={}, mux={}, outbox={}, delivery={}, transport={}, lifecycle={}, source={}, decoder={},
            metadata_steps_per_pump=1, max_lag_seconds=1)
        self.a.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'calibration'):
            validate_continuous_selection(self.a, 'A')
        self.assertFalse((self.root/'never-created').exists())

    def test_local_primary_lock_conflicts_across_profiles_and_releases(self):
        locks = self.root/'owners'
        with primary_ownership('https://server.test/', ['A'], locks):
            with self.assertRaisesRegex(RuntimeError, 'already owned'):
                with primary_ownership('https://server.test', ['A'], locks):
                    self.fail('double owner')
            with primary_ownership('https://server.test', ['B'], locks):
                pass
        with primary_ownership('https://server.test', ['A'], locks):
            pass

    def test_reference_request_auth_is_fatal_transients_and_request_conflicts_defer(self):
        client = Mock()
        client.list_reference_requests.return_value = [{'request_id': 'fixture'}]
        client.list_streams.return_value = []
        collector = ReferenceCollector(client)
        for code in (401, 403, 404, 409, 429, 500, 503):
            with self.subTest(code=code), patch.object(collector, 'collect', side_effect=status_error(code)):
                if code in (401, 403):
                    with self.assertRaises(httpx.HTTPStatusError):
                        collector.run_once()
                else:
                    collector.run_once()

    def test_reference_main_auth_reaches_fatal_wrapper_and_client_closes(self):
        from inference_worker import reference_collector
        for code in (401, 403, 429, 503):
            client = Mock()
            client.list_reference_requests.side_effect = status_error(code)
            with patch.object(reference_collector, 'VideoClient', return_value=client), \
                 patch('sys.argv', ['reference_collector', '--once']):
                if code in (401, 403):
                    with patch('inference_worker.run_multi.runpy.run_module',
                               side_effect=lambda *a, **k: reference_collector.main()):
                        self.assertEqual(child_main('inference_worker.reference_collector'), FATAL_EXIT)
                else:
                    reference_collector.main()
            client.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
