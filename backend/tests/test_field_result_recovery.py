import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import httpx
from field_agent import main as agent


class FieldResultRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(TemporaryDirectory())) / 'node.json'
        self.path.write_text(json.dumps({'old': {'status': 'completed', 'result': {'secret_fixture': 'do not archive'}}}))

    def run_agent(self, codes):
        reports, claims = [], []
        def handle(request):
            if request.method == 'PUT':
                reports.append(json.loads(request.content))
                status = codes[min(len(reports)-1, len(codes)-1)]
                return httpx.Response(status, json={'detail': 'fixture'})
            if request.url.path.endswith('/claim'):
                claims.append(1)
                return httpx.Response(200, json={'command': None})
            return httpx.Response(200, json={})
        client = httpx.Client(base_url='http://test', transport=httpx.MockTransport(handle))
        with patch.object(agent.httpx, 'Client', return_value=client), patch.object(agent.time, 'sleep'), patch.object(agent, 'execute') as execute:
            agent.run('http://test', 'fixture', self.path, once=True)
        execute.assert_not_called()
        return reports, claims

    def test_rejected_payload_becomes_small_failure_receipt_then_claim_continues(self):
        for code in (413, 422):
            self.path.write_text(json.dumps({"old": {"status": "completed", "result": {"secret_fixture": "do not archive"}}}))
            reports, claims = self.run_agent([code, 200])
            self.assertEqual(len(reports), 2)
            self.assertEqual(reports[1]['status'], 'failed')
            self.assertEqual(reports[1]['result']['reason'], 'result_report_rejected')
            self.assertEqual(reports[1]['result']['original_status'], 'completed')
            self.assertNotIn('secret_fixture', json.dumps(reports[1]))
            self.assertNotIn('_report_fallback', reports[1])
            self.assertEqual(claims, [1])
            self.assertEqual(json.loads(self.path.read_text()), {})

    def test_even_fallback_rejected_does_not_block_queue_and_retains_no_payload(self):
        reports, claims = self.run_agent([422, 422])
        self.assertEqual(len(reports), 2)
        self.assertEqual(claims, [1])
        audit = self.path.with_suffix('.rejected.json').read_text()
        self.assertNotIn('secret_fixture', audit)
        self.assertEqual(json.loads(audit)['old']['http_status'], 422)
        self.assertEqual(json.loads(self.path.read_text()), {})

    def test_temporary_error_preserves_original_for_retry(self):
        with self.assertRaises(httpx.HTTPStatusError):
            self.run_agent([503])
        self.assertIn('secret_fixture', self.path.read_text())
        self.assertFalse(self.path.with_suffix('.rejected.json').exists())

    def test_fallback_survives_restart_without_reexecuting_command(self):
        with self.assertRaises(httpx.HTTPStatusError):
            self.run_agent([413, 503])
        state = json.loads(self.path.read_text())
        self.assertTrue(state['old']['_report_fallback'])
        reports, claims = self.run_agent([200])
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['result']['reason'], 'result_report_rejected')
        self.assertEqual(claims, [1])

    def test_rejection_audit_is_bounded(self):
        for i in range(105):
            agent.remember_rejected_result(self.path, str(i), {'status': 'failed'}, 422)
        audit = json.loads(self.path.with_suffix('.rejected.json').read_text())
        self.assertEqual(len(audit), 100)
        self.assertNotIn('0', audit)
        self.assertIn('104', audit)


class FieldResultApiTests(unittest.TestCase):
    def test_receiver_accepts_minimal_failure_for_oversized_or_invalid_voice_result(self):
        from backend.tests.support import isolated_api, login
        for result, expected in [({'diagnostic': 'x'*66000}, 413),
                                 ({'voice_scan_ok': True, 'voices': ['invalid']}, 422)]:
            with self.subTest(status=expected), isolated_api() as api, TemporaryDirectory() as directory:
                admin = login(api)
                token = api.post('/api/nodes', headers=admin, json={'node_id': 'recovery', 'name': 'fixture'}).json()['token']
                headers = {'Authorization': 'Bearer '+token}
                api.post('/api/nodes/recovery/voices/refresh', headers=admin).raise_for_status()
                command = api.post('/api/field/commands/claim', headers=headers).json()['command']
                path = Path(directory)/'state.json'
                path.write_text(json.dumps({command['command_id']: {'status': 'completed', 'result': result}}))
                statuses = []
                def handle(request):
                    response = api.request(request.method, request.url.path, headers=headers,
                                           json=json.loads(request.content) if request.content else None)
                    if request.method == 'PUT':
                        statuses.append(response.status_code)
                    return response
                client = httpx.Client(base_url='http://test', transport=httpx.MockTransport(handle))
                with patch.object(agent.httpx, 'Client', return_value=client), patch.object(agent.time, 'sleep'), patch.object(agent, 'execute') as execute:
                    agent.run('http://test', token, path, once=True)
                execute.assert_not_called()
                self.assertEqual(statuses, [expected, 200])
                commands = api.get('/api/nodes/recovery/commands', headers=admin).json()['items']
                self.assertEqual(commands[0]['status'], 'failed')
                self.assertEqual(commands[0]['result']['reason'], 'result_report_rejected')
                self.assertEqual(json.loads(path.read_text()), {})

    def test_permanent_receipt_rejection_keeps_server_unresolved_and_never_requeues(self):
        import time
        from backend.tests.support import isolated_api, login
        with isolated_api() as api, TemporaryDirectory() as directory:
            admin = login(api)
            token = api.post('/api/nodes', headers=admin, json={'node_id': 'rejecting', 'name': 'fixture'}).json()['token']
            headers = {'Authorization': 'Bearer '+token}
            api.post('/api/nodes/rejecting/voices/refresh', headers=admin).raise_for_status()
            first = api.post('/api/field/commands/claim', headers=headers).json()['command']
            api.post('/api/nodes/rejecting/voices/refresh', headers=admin).raise_for_status()
            path = Path(directory)/'state.json'
            path.write_text(json.dumps({first['command_id']: {'status': 'completed', 'result': {}}}))
            claimed = []
            def handle(request):
                if request.method == 'PUT':
                    return httpx.Response(422, json={'detail': 'proxy rejects all receipts'})
                response = api.request(request.method, request.url.path, headers=headers)
                if request.url.path.endswith('/claim') and response.json()['command']:
                    claimed.append(response.json()['command']['command_id'])
                return response
            client = httpx.Client(base_url='http://test', transport=httpx.MockTransport(handle))
            with patch.object(agent.httpx, 'Client', return_value=client), patch.object(agent.time, 'sleep'), patch.object(agent, 'execute', return_value={'status': 'completed', 'result': {}}) as execute:
                agent.run('http://test', token, path, once=True)
            execute.assert_called_once()
            self.assertEqual(len(claimed), 1)
            self.assertNotIn(first['command_id'], claimed)
            rows = api.get('/api/nodes/rejecting/commands', headers=admin).json()['items']
            self.assertEqual({row['status'] for row in rows}, {'claimed'})
            self.assertTrue(all(row['result'] is None for row in rows))
            audit = json.loads(path.with_suffix('.rejected.json').read_text())
            self.assertEqual(set(audit), {first['command_id'], claimed[0]})
            # Server changes stale claimed work to unknown, never back to pending.
            future = time.time()+1801
            with patch('backend.field.service.time.time', return_value=future):
                self.assertIsNone(api.post('/api/field/commands/claim', headers=headers).json()['command'])
            rows = api.get('/api/nodes/rejecting/commands', headers=admin).json()['items']
            self.assertEqual({row['status'] for row in rows}, {'unknown'})
            self.assertEqual(json.loads(path.with_suffix('.rejected.json').read_text()), audit)
