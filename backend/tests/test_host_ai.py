"""Isolated queue and native worker tests; no real CLI, model, or external HTTP."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import cv2
import httpx
import numpy as np
from fastapi import HTTPException

from backend.db.event_repository import get_connection
from backend.education import provider as education
from backend.equipment import provider as equipment
from backend.processing import ai_jobs
from backend.processing.worker import Worker
from backend.tests.support import isolated_api, login


class HostAITests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch('backend.processing.ai_jobs.ROOT', root / 'sources'))
        self.enterContext(patch('backend.processing.worker.WORK_ROOT', root / 'worker'))
        self.enterContext(patch('backend.equipment.recognition.EVIDENCE_ROOT', root / 'evidence'))
        self.enterContext(patch.dict(os.environ, {'PPE_PROCESSING_MODE': 'remote', 'PPE_HOST_AI_ENABLED': 'true',
            'PPE_EQUIPMENT_PROVIDER': 'codex_chatgpt', 'PPE_EDUCATION_PROVIDER': 'codex_chatgpt',
            'PPE_EDUCATION_MODEL': '', 'PPE_TEST_POSTGRES_URL': os.environ.get('PPE_TEST_POSTGRES_URL', '')}))
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        self.worker_user = login(self.client, 'worker01')
        response = self.client.post('/api/nodes', headers=self.admin, json={'node_id': 'host-ai', 'name': '호스트 처리'})
        self.token = response.json()['token']
        self.node = {'Authorization': 'Bearer ' + self.token}
        self.client.put('/api/processing/workers/host-ai', headers=self.admin, json={'enabled': True}).raise_for_status()
        ai_jobs.advertise('host-ai', {'equipment': 'codex_chatgpt', 'education': 'codex_chatgpt'})
        self.image = cv2.imencode('.jpg', np.zeros((32, 32, 3), dtype=np.uint8))[1].tobytes()
        self.content = {'title': '안전모 교육', 'body': '현장 지침에 따라 착용 상태를 확인합니다.',
                        'checklist': ['외관 확인', '착용 확인', '현장 지침 확인']}

    def queued(self, kind='equipment'):
        return ai_jobs.enqueue(kind, 'codex_chatgpt', {}, self.image if kind == 'equipment' else None)

    def claim(self):
        result = self.client.post('/api/processing/ai/claim', headers=self.node)
        self.assertEqual(result.status_code, 200, result.text)
        job = result.json()['job']
        return job, {**self.node, 'X-Processing-Lease': job['lease_token']}

    def native_worker(self):
        worker = Worker('https://isolated.invalid', self.token)
        worker.client.close()
        def handle(request):
            response = self.client.request(request.method, request.url.raw_path.decode(),
                                           headers=dict(request.headers), content=request.content)
            return httpx.Response(response.status_code, headers=dict(response.headers), content=response.content)
        worker.client = httpx.Client(base_url='https://isolated.invalid', headers=self.node,
                                    transport=httpx.MockTransport(handle))
        self.addCleanup(worker.client.close)
        return worker

    def test_source_and_completion_require_enabled_worker_and_matching_lease(self):
        ident = self.queued()
        self.assertEqual(self.client.post('/api/processing/ai/claim').status_code, 401)
        self.assertEqual(self.client.post('/api/processing/ai/claim', headers=self.worker_user).status_code, 401)
        job, headers = self.claim()
        self.assertEqual(job['job_id'], ident)
        url = '/api/processing/ai/jobs/'+ident
        self.assertEqual(self.client.get(url+'/source', headers=self.admin).status_code, 401)
        self.assertEqual(self.client.get(url+'/source', headers=self.node).status_code, 403)
        self.assertEqual(self.client.get(url+'/source', headers=headers).content, self.image)
        result = {'analysis': {'candidates': []}, 'redacted_jpeg': base64.b64encode(self.image).decode()}
        self.assertEqual(self.client.post(url+'/result', headers=self.node, json=result).status_code, 403)
        self.assertEqual(self.client.post(url+'/result', headers=headers, json=result).status_code, 200)
        self.assertFalse(ai_jobs.source_path(ident).exists())
        self.assertEqual(self.client.get(url+'/source', headers=headers).status_code, 409)
        self.assertEqual(self.client.post(url+'/result', headers=headers, json=result).status_code, 409)
        self.assertIsNone(self.client.post('/api/processing/claim', headers=self.node).json()['job'])
        self.assertEqual(self.client.post(url+'/cleanup', headers=self.node).status_code, 200)

    def test_invalid_and_expired_results_never_publish(self):
        ident = self.queued()
        _, headers = self.claim()
        url = '/api/processing/ai/jobs/'+ident
        self.assertEqual(self.client.post(url+'/result', headers=headers, json={'analysis': {}, 'redacted_jpeg': 'bad'}).status_code, 422)
        self.assertEqual(self.client.post(url+'/failure', headers=headers, json={'error_code': 'secret raw exception'}).status_code, 422)
        with get_connection() as conn:
            conn.execute('UPDATE host_ai_job SET deadline=0 WHERE job_id=?', (ident,))
        self.assertEqual(self.client.post(url+'/failure', headers=headers, json={'error_code': 'provider_timeout'}).status_code, 409)
        ai_jobs.maintain()
        self.assertFalse(ai_jobs.source_path(ident).exists())
        with get_connection() as conn:
            row = ai_jobs.job(conn, ident)
        self.assertEqual((row['status'], row['input_json'], row['result_json']), ('cancelled', '{}', None))
        self.assertEqual(self.client.post(url+'/cleanup', headers=self.node).status_code, 200)

    def test_missing_stale_disabled_worker_and_queue_limit(self):
        with get_connection() as conn:
            conn.execute('UPDATE host_ai_worker SET expires_at=0')
        with self.assertRaises(HTTPException) as exc:
            self.queued()
        self.assertEqual(exc.exception.status_code, 503)
        ai_jobs.advertise('host-ai', {'equipment': 'codex_chatgpt'})
        with get_connection() as conn:
            conn.execute("UPDATE processing_worker SET enabled=0 WHERE node_id='host-ai'")
        with self.assertRaises(HTTPException):
            self.queued()
        with get_connection() as conn:
            conn.execute("UPDATE processing_worker SET enabled=1 WHERE node_id='host-ai'")
        for _ in range(4):
            self.queued()
        with self.assertRaises(HTTPException) as exc:
            self.queued()
        self.assertEqual(exc.exception.status_code, 429)

    def test_timeout_purges_source_and_request_data(self):
        with patch.object(ai_jobs, 'DEADLINE_SECONDS', -1):
            with self.assertRaises(HTTPException) as exc:
                ai_jobs.request_job('equipment', 'codex_chatgpt', {}, self.image)
        self.assertEqual(exc.exception.status_code, 504)
        with get_connection() as conn:
            row = dict(conn.execute('SELECT * FROM host_ai_job').fetchone())
        self.assertEqual((row['status'], row['input_json'], row['result_json']), ('cancelled', '{}', None))
        self.assertFalse(ai_jobs.source_path(row['job_id']).exists())

    def test_inference_busy_defers_host_ai_claim(self):
        self.queued()
        self.client.put('/api/processing/inference-load', headers=self.node, json={'busy': True}).raise_for_status()
        self.assertIsNone(self.client.post('/api/processing/ai/claim', headers=self.node).json()['job'])
        self.client.put('/api/processing/inference-load', headers=self.node, json={'busy': False}).raise_for_status()
        self.assertIsNotNone(self.client.post('/api/processing/ai/claim', headers=self.node).json()['job'])

    def run_api_with_host(self, call, worker):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(call)
            until = time.monotonic() + 3
            while time.monotonic() < until:
                with get_connection() as conn:
                    queued = conn.execute("SELECT 1 FROM host_ai_job WHERE status='queued'").fetchone()
                if queued:
                    break
                if result.done():
                    return result.result()
                time.sleep(0.02)
            try:
                self.assertTrue(worker.run_once())
                return result.result(timeout=3)
            finally:
                with get_connection() as conn:
                    conn.execute("UPDATE host_ai_job SET deadline=0 WHERE status IN ('queued','running')")

    def test_remote_photo_redacts_before_provider_and_confirms_normal_equipment(self):
        worker = self.native_worker()
        redacted = cv2.imencode('.jpg', np.full((32,32,3), 100, dtype=np.uint8))[1].tobytes()
        def redact(source, output, mode):
            self.assertEqual(source.read_bytes(), self.image)
            self.assertEqual(mode, 'scrfd')
            output.write_bytes(redacted)
        def analyze(image, **kwargs):
            self.assertEqual(image, redacted)
            self.assertNotEqual(image, self.image)
            return equipment.ProviderResult(candidates=[equipment.Candidate(label='프레스', equipment_type='press')])
        with patch('backend.processing.ai_worker.redact_image', side_effect=redact), \
             patch('backend.equipment.subscription_provider.SubscriptionProvider.analyze', side_effect=analyze):
            response = self.run_api_with_host(lambda: self.client.post('/api/equipment/recognitions?camera_id=CAM_001',
                headers={**self.admin, 'Content-Type': 'image/jpeg'}, content=self.image), worker)
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()
        self.assertEqual(row['status'], 'analyzed')
        confirmed = self.client.post('/api/equipment/recognitions/'+row['recognition_id']+'/confirm', headers=self.admin,
            json={'candidate_id': row['candidates'][0]['candidate_id'], 'name': '프레스', 'zone_name': '프레스 구역', 'required_ppe': ['vest']})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertIn(confirmed.json()['equipment_id'], [item['equipment_id'] for item in self.client.get('/api/equipment', headers=self.admin).json()['items']])
        self.assertEqual(list(worker.root.iterdir()), [])
        self.assertEqual(list(ai_jobs.ROOT.iterdir()), [])

    def test_remote_codex_education_stays_draft_and_purges_job_text(self):
        worker = self.native_worker()
        with get_connection() as conn:
            recommendation = conn.execute('SELECT recommendation_id FROM education_recommendation LIMIT 1').fetchone()[0]
        with patch('backend.equipment.subscription_provider.codex_structured', return_value=json.dumps(self.content)) as codex:
            response = self.run_api_with_host(lambda: self.client.post('/api/recommendations/'+recommendation+'/materials/generate', headers=self.admin), worker)
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()
        self.assertEqual((row['status'], row['provider'], row['model']), ('draft', 'codex_chatgpt', 'codex-default'))
        self.assertNotIn('image', codex.call_args.kwargs)
        self.assertNotIn('admin02', codex.call_args.args[0])
        with get_connection() as conn:
            job = conn.execute('SELECT * FROM host_ai_job').fetchone()
            self.assertEqual((job['input_json'], job['result_json'], job['cleanup_pending']), ('{}', None, 0))
        self.assertEqual(list(worker.root.iterdir()), [])

    def test_host_redaction_failure_never_calls_equipment_provider(self):
        self.queued()
        worker = self.native_worker()
        with patch('backend.processing.ai_worker.redact_image', side_effect=RuntimeError('private failure')), \
             patch('backend.equipment.subscription_provider.SubscriptionProvider.analyze') as analyze:
            self.assertTrue(worker.run_once())
        analyze.assert_not_called()
        with get_connection() as conn:
            row = conn.execute('SELECT status,error_code,cleanup_pending FROM host_ai_job').fetchone()
        self.assertEqual(tuple(row.values()) if isinstance(row, dict) else tuple(row), ('failed', 'redaction_failed', 0))
        self.assertEqual(list(worker.root.iterdir()), [])

    def test_codex_education_failure_never_falls_back_to_paid_api(self):
        with patch('backend.equipment.subscription_provider.codex_structured', side_effect=equipment.ProviderError('provider_auth_required')), \
             patch('backend.education.provider.httpx.Client') as paid:
            with self.assertRaises(education.GenerationError) as exc:
                education.generate({'ppe_type': 'helmet', 'education_topic': '안전모 교육'})
        self.assertEqual(exc.exception.code, 'education_provider_auth_required')
        paid.assert_not_called()


class EquipmentPaidAdapterTests(unittest.TestCase):
    def test_explicit_paid_provider_sends_only_supplied_redacted_image(self):
        from backend.equipment.openai_provider import OpenAIProvider
        requests = []
        real_client = httpx.Client
        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={'status': 'completed', 'output': [
                {'type': 'message', 'role': 'assistant', 'status': 'completed',
                 'content': [{'type': 'output_text', 'text': '{"candidates":[]}'}]}]})
        with patch.dict(os.environ, {'PPE_EQUIPMENT_PROVIDER': 'openai', 'PPE_EQUIPMENT_MODEL': 'test-vision',
                                     'PPE_EQUIPMENT_OPENAI_API_KEY': 'fake-isolated-key'}), \
             patch('backend.equipment.openai_provider.httpx.Client', side_effect=lambda **kwargs:
                   real_client(transport=httpx.MockTransport(handle), **kwargs)):
            self.assertIsInstance(equipment.get_provider(), OpenAIProvider)
            self.assertEqual(equipment.get_provider().analyze(b'redacted-only', timeout_seconds=10).candidates, [])
        payload = json.loads(requests[0].content)
        self.assertFalse(payload['store'])
        self.assertEqual(payload['input'][0]['content'][1]['image_url'], 'data:image/jpeg;base64,'+base64.b64encode(b'redacted-only').decode())
