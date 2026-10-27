import json
import os
import unittest
from unittest.mock import patch

import httpx

from backend.db.event_repository import get_connection
from backend.education import service
from backend.tests.support import isolated_api, login
from backend.zones.membership import replace_user_zones


class EducationGenerationTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        self.worker = login(self.client, 'worker01')
        self.enterContext(patch.dict(os.environ, {
            'PPE_EDUCATION_PROVIDER': 'openai', 'PPE_EDUCATION_MODEL': 'test-model',
            'PPE_EDUCATION_OPENAI_API_KEY': 'isolated-key-never-send',
        }))
        with get_connection() as conn:
            conn.execute('DELETE FROM education_recommendation')
            conn.execute("INSERT INTO quarterly_summary(quarter,created_at) VALUES('2026-Q3',CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING")
            replace_user_zones(conn, 'worker01', ['프레스 구역'])
            conn.execute('''INSERT INTO education_recommendation(recommendation_id,quarter,recommendation_rank,ppe_type,
                zone_name,education_topic,priority_score,confirmed_count,generated_at)
                VALUES('REC_GEN','2026-Q3',1,'helmet','프레스 구역','프레스 구역 안전모 착용 점검 교육',99,42,CURRENT_TIMESTAMP)''')
        self.url = '/api/recommendations/REC_GEN/materials/generate'
        self.content = {'title': '안전모 점검 초안', 'body': '현장 안전지침과 제조사 안내를 우선하여 착용 상태를 점검합니다.',
                        'checklist': ['외관 확인', '착용 상태 확인', '현장 지침 확인']}
        self.response_data = self.completed()
        self.raw = None
        self.http_status = 200
        self.callback = None
        self.failure = None
        self.requests = []
        self.client_options = []
        real_client = httpx.Client

        def handle(request):
            self.requests.append(request)
            if self.callback:
                self.callback()
            if self.failure:
                raise self.failure
            if self.raw is not None:
                return httpx.Response(self.http_status, content=self.raw)
            return httpx.Response(self.http_status, json=self.response_data)

        def factory(**kwargs):
            self.client_options.append(kwargs)
            return real_client(transport=httpx.MockTransport(handle), **kwargs)

        # Every provider attempt in this fixture is intercepted, including failure cases.
        self.enterContext(patch('backend.education.provider.httpx.Client', side_effect=factory))

    def completed(self, content=None):
        return {'status': 'completed', 'model': 'test-model-snapshot', 'output': [
            {'type': 'message', 'role': 'assistant', 'status': 'completed',
             'content': [{'type': 'output_text', 'text': json.dumps(content or self.content)}]}]}

    def generate(self):
        return self.client.post(self.url, headers=self.admin)

    def material_count(self):
        with get_connection() as conn:
            return conn.execute('SELECT COUNT(*) FROM education_material').fetchone()[0]

    def test_disabled_or_incomplete_configuration_never_calls_provider(self):
        configurations = [
            {'PPE_EDUCATION_PROVIDER': ''}, {'PPE_EDUCATION_PROVIDER': 'unknown'},
            {'PPE_EDUCATION_MODEL': ''}, {'PPE_EDUCATION_MODEL': 'bad model'},
            {'PPE_EDUCATION_OPENAI_API_KEY': '', 'OPENAI_API_KEY': 'unrelated-key-must-not-be-used'},
        ]
        for config in configurations:
            with self.subTest(config=config), patch.dict(os.environ, config):
                self.assertEqual(self.generate().status_code, 503)
                state = self.client.get('/api/education/generation', headers=self.admin).json()
                self.assertFalse(state['configured'])
                self.assertNotIn('isolated-key', json.dumps(state))
        self.assertEqual(self.requests, [])
        self.assertEqual(self.material_count(), 0)

    def test_generated_draft_requires_edit_review_and_publish_before_worker_exposure(self):
        state = self.client.get('/api/education/generation', headers=self.admin).json()
        self.assertTrue(state['configured'])
        result = self.generate()
        self.assertEqual(result.status_code, 201, result.text)
        draft = result.json()
        self.assertEqual((draft['status'], draft['provider'], draft['model'], draft['created_by']),
                         ('draft', 'openai', 'test-model-snapshot', 'admin02'))
        self.assertIsNone(self.client.get('/api/worker/education', headers=self.worker).json()['items'][0]['material'])
        material_url = '/api/education/materials/' + draft['material_id']
        edited = {**self.content, 'body': '관리자가 수정·검토한 본문', 'expected_version': 0}
        self.assertEqual(self.client.patch(material_url, headers=self.admin, json=edited).status_code, 200)
        self.assertEqual(self.client.post(material_url+'/publish', headers=self.admin,
                         json={'expected_version': 1, 'approved': True}).status_code, 200)
        visible = self.client.get('/api/worker/education', headers=self.worker).json()['items'][0]
        self.assertIn(edited['body'], visible['material'])
        self.assertNotIn('provider', visible)
        self.assertNotIn('confirmed_count', visible)

    def test_outbound_request_contains_only_topic_and_ppe_with_explicit_structured_output(self):
        self.assertEqual(self.generate().status_code, 201)
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(str(request.url), 'https://api.openai.com/v1/responses')
        payload = json.loads(request.content)
        self.assertEqual(set(json.loads(payload['input'])), {'ppe_type', 'education_topic'})
        self.assertNotIn('confirmed_count', payload['input'])
        self.assertNotIn('priority_score', payload['input'])
        self.assertNotIn('admin02', payload['input'])
        self.assertNotIn('isolated-key', request.content.decode())
        self.assertFalse(payload['store'])
        self.assertEqual(payload['max_output_tokens'], 2400)
        self.assertTrue(payload['text']['format']['strict'])
        self.assertFalse(payload['text']['format']['schema']['additionalProperties'])
        self.assertFalse(self.client_options[0]['trust_env'])
        self.assertFalse(self.client_options[0]['follow_redirects'])

    def test_auth_roles_block_generation_and_configuration_before_provider_access(self):
        for path in (self.url, '/api/education/generation'):
            method = self.client.post if path == self.url else self.client.get
            self.assertEqual(method(path).status_code, 401)
            self.assertEqual(method(path, headers=self.worker).status_code, 403)
        self.assertEqual(self.requests, [])

    def test_http_failures_are_redacted_and_do_not_replace_published_material(self):
        existing = self.client.post('/api/recommendations/REC_GEN/materials', headers=self.admin, json=self.content).json()
        self.client.post('/api/education/materials/'+existing['material_id']+'/publish', headers=self.admin,
                         json={'expected_version': 0, 'approved': True}).raise_for_status()
        for status, expected in ((401,503),(403,503),(429,429),(400,502),(500,502),(302,502)):
            with self.subTest(status=status):
                self.http_status = status
                self.response_data = {'error': {'message': 'private-upstream-detail isolated-key-never-send'}}
                response = self.generate()
                self.assertEqual(response.status_code, expected)
                self.assertNotIn('private-upstream-detail', response.text)
                self.assertNotIn('isolated-key', response.text)
                self.assertEqual(self.material_count(), 1)
                self.assertIn(self.content['body'], self.client.get('/api/worker/education', headers=self.worker).json()['items'][0]['material'])
        self.assertEqual(len(self.requests), 6)  # No automatic retries.

    def test_timeout_and_network_failure_release_generation_slot(self):
        for failure, expected in ((httpx.ReadTimeout('private timeout'),504),(httpx.ConnectError('private connection'),502)):
            self.failure = failure
            response = self.generate()
            self.assertEqual(response.status_code, expected)
            self.assertNotIn('private', response.text)
        self.failure = None
        self.assertEqual(self.generate().status_code, 201)

    def test_incomplete_refused_malformed_and_invalid_content_never_create_drafts(self):
        incomplete = self.completed(); incomplete['status'] = 'incomplete'
        refused = self.completed(); refused['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'private refusal'}]
        cases = [(incomplete,502), (refused,422), ([],502), ({},502),
                 (self.completed({**self.content, 'title':' '}),502),
                 (self.completed({**self.content, 'checklist':[]}),502),
                 (self.completed({**self.content, 'status':'published'}),502)]
        for payload, expected in cases:
            self.response_data = payload
            response = self.generate()
            self.assertEqual(response.status_code, expected, response.text)
            self.assertNotIn('private refusal', response.text)
            self.assertEqual(self.material_count(), 0)
        self.raw = b'not-json'
        self.assertEqual(self.generate().status_code, 502)
        self.raw = b'x' * (256*1024+1)
        self.assertEqual(self.generate().status_code, 502)
        self.assertEqual(self.material_count(), 0)

    def test_recommendation_change_during_network_wait_rejects_wrong_scope_draft(self):
        def change():
            with get_connection() as conn:
                conn.execute("UPDATE education_recommendation SET zone_name='자재 이동 구역',education_topic='다른 주제' WHERE recommendation_id='REC_GEN'")
        self.callback = change
        response = self.generate()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail'], 'education_recommendation_changed')
        self.assertEqual(self.material_count(), 0)

    def test_deleted_recommendation_after_network_wait_and_unknown_id(self):
        self.assertEqual(self.client.post('/api/recommendations/MISSING/materials/generate', headers=self.admin).status_code, 404)
        self.assertEqual(self.requests, [])
        def remove():
            with get_connection() as conn:
                conn.execute("DELETE FROM education_recommendation WHERE recommendation_id='REC_GEN'")
        self.callback = remove
        self.assertEqual(self.generate().status_code, 409)
        self.assertEqual(self.material_count(), 0)

    def test_process_capacity_does_not_make_a_second_paid_request(self):
        self.assertTrue(service.generation_slot.acquire(blocking=False))
        try:
            self.assertEqual(self.generate().status_code, 429)
            self.assertEqual(self.requests, [])
        finally:
            service.generation_slot.release()
        self.assertEqual(self.generate().status_code, 201)

    def test_reasoning_output_is_not_treated_as_material_and_missing_model_is_rejected(self):
        self.response_data['output'].insert(0, {'type':'reasoning','summary':[]})
        self.assertEqual(self.generate().status_code, 201)
        self.response_data = self.completed()
        self.response_data.pop('model')
        self.assertEqual(self.generate().status_code, 502)
        self.assertEqual(self.material_count(), 1)
