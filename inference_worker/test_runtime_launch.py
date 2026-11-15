"""Runtime launch contracts; no GPU, model loading, operating settings or HTTP."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import httpx

from inference_worker.run_multi import (commands, continuous_profiles, main, child_main, run_preflight,
                                       preflight, probe_continuous_protocol, ContinuousProtocolError, FATAL_EXIT)
from inference_worker.reference_collector import ReferenceCollector
from deploy.host_runtime.settings import arguments
from deploy.host_runtime.ownership import primary_ownership


def status_error(code):
    response = httpx.Response(code, request=httpx.Request('GET', 'https://fixture.invalid'))
    return httpx.HTTPStatusError('not logged', request=response.request, response=response)


class RuntimeLaunchTests(unittest.TestCase):
    def test_protocol_probe_uses_existing_readonly_discovery_and_validates_enabled_response(self):
        from types import SimpleNamespace
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={'items': [], 'next_after': None})
        with httpx.Client(base_url='https://fixture.invalid', transport=httpx.MockTransport(respond)) as client:
            probe_continuous_protocol(SimpleNamespace(client=client))
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, 'GET')
        self.assertEqual(requests[0].url.path, '/api/processing/continuous-events/active')
        self.assertEqual(dict(requests[0].url.params), {'limit': '1'})

    def test_protocol_probe_disabled_auth_unreachable_and_invalid_all_fail_closed(self):
        from types import SimpleNamespace
        for status, body, reason in ((503, {'detail': 'continuous_events_disabled'}, 'disabled'),
                                    (401, {'detail': 'secret'}, 'auth'),
                                    (403, {'detail': 'secret'}, 'auth'),
                                    (503, {}, 'http_503'), (404, {}, 'http_404'),
                                    (200, {}, 'invalid_response')):
            with self.subTest(status=status, reason=reason), httpx.Client(
                    base_url='https://fixture.invalid', transport=httpx.MockTransport(
                        lambda request: httpx.Response(status, json=body))) as client:
                with self.assertRaisesRegex(ContinuousProtocolError, '^'+reason+'$'):
                    probe_continuous_protocol(SimpleNamespace(client=client))
        def offline(request):
            raise httpx.ConnectError('sensitive URL/token text', request=request)
        with httpx.Client(base_url='https://fixture.invalid', transport=httpx.MockTransport(offline)) as client:
            with self.assertRaisesRegex(ContinuousProtocolError, '^unreachable$'):
                probe_continuous_protocol(SimpleNamespace(client=client))

    def test_continuous_preflight_probes_before_model_and_closes_on_disabled(self):
        from types import SimpleNamespace
        for enabled in (False, True):
            client = Mock()
            client.client.get.return_value = httpx.Response(200 if enabled else 503,
                json={'items': [], 'next_after': None} if enabled else {'detail': 'continuous_events_disabled'},
                request=httpx.Request('GET', 'https://fixture.invalid'))
            detector = Mock(return_value=Mock(supports_no_helmet=False))
            modules = {'inference_worker.video_client': SimpleNamespace(VideoClient=lambda: client),
                       'inference_worker.run_server': SimpleNamespace(create_detector=detector),
                       'inference_worker.person_tracker': SimpleNamespace(PersonTracker=Mock()),
                       'inference_worker.ppe_rules': SimpleNamespace(assess_ppe=Mock())}
            with self.subTest(enabled=enabled), patch.dict('sys.modules', modules):
                if enabled:
                    preflight(['A'], self.model, '--submit-review-candidates', continuous=True)
                    detector.assert_called_once()
                else:
                    with self.assertRaisesRegex(ContinuousProtocolError, 'disabled'):
                        preflight(['A'], self.model, '--submit-review-candidates', continuous=True)
                    detector.assert_not_called(); client.get_ppe_policy.assert_not_called()
            client.close.assert_called_once()

    def test_legacy_preflight_never_requires_continuous_protocol(self):
        from types import SimpleNamespace
        client = Mock()
        modules = {'inference_worker.video_client': SimpleNamespace(VideoClient=lambda: client),
                   'inference_worker.run_server': SimpleNamespace(create_detector=Mock()),
                   'inference_worker.person_tracker': SimpleNamespace(PersonTracker=Mock()),
                   'inference_worker.ppe_rules': SimpleNamespace(assess_ppe=Mock())}
        with patch.dict('sys.modules', modules):
            preflight(['A'], self.model, None)
        client.client.get.assert_not_called()
        client.close.assert_called_once()

    def test_disabled_protocol_is_sanitized_fatal_before_peers_and_raw_state(self):
        from io import StringIO
        with patch('inference_worker.run_multi.continuous_profiles'), \
             patch('inference_worker.run_multi.preflight', side_effect=ContinuousProtocolError('disabled')) as check, \
             patch('inference_worker.run_multi.supervise') as supervisor, \
             patch('sys.stderr', new_callable=StringIO) as error:
            with self.assertRaises(SystemExit) as result:
                main(['--model-path', str(self.model), '--camera-id', 'A', '--runner', 'continuous',
                      '--continuous-profile', f'A={self.a}', '--submit-review-candidates', '--preflight-only'])
        self.assertEqual(result.exception.code, FATAL_EXIT)
        self.assertTrue(check.call_args.kwargs['continuous'])
        self.assertIn('continuous_protocol_disabled', error.getvalue())
        supervisor.assert_not_called()
        self.assertEqual({p.name for p in self.root.iterdir()}, {'model.pt', 'a.json', 'b.json'})

    def test_backend_discovery_disabled_guard_precedes_database_access(self):
        from fastapi import HTTPException
        from backend.processing import capture
        with patch.dict('os.environ', {'PPE_CONTINUOUS_EVENTS_ENABLED': 'false'}), \
             patch.object(capture.lc, 'get_connection') as connection:
            with self.assertRaises(HTTPException) as result:
                capture.discovery('fixture', limit=1)
        self.assertEqual(result.exception.status_code, 503)
        self.assertEqual(result.exception.detail, 'continuous_events_disabled')
        connection.assert_not_called()

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
