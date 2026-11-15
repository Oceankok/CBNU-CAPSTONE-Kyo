"""Resolve real release Compose, then exercise API gates with its environment.

No Docker daemon/container startup, operating credentials, model or network API.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import patch
from uuid import uuid4

from backend.tests.support import isolated_api, login


ROOT = Path(__file__).resolve().parents[2]


@skipUnless(shutil.which('docker'), 'Docker Compose CLI required; no daemon needed')
class ReleaseContinuousEnvironmentTests(TestCase):
    def environment(self, **options):
        # Never let real provider credentials or the user's .env reach config.
        env = {k:v for k,v in os.environ.items() if not k.startswith(('PPE_', 'COMPOSE_')) and 'OPENAI' not in k}
        env.update(PPE_API_IMAGE='example.test/api:configuration-only',
                   PPE_WEB_IMAGE='example.test/web:configuration-only',
                   PPE_DATABASE_URL='postgresql://test:test@postgres/test',
                   PPE_POSTGRES_PASSWORD='configuration-only-password',
                   PPE_JWT_SECRET='configuration-only-secret-at-least-32-bytes', **options)
        with TemporaryDirectory() as temp:
            blank = Path(temp)/'empty.env'; blank.write_text('')
            result = subprocess.run(['docker', 'compose', '--profile', 'tools', '--env-file', str(blank),
                '-f', str(ROOT/'compose.release.yaml'), 'config', '--format', 'json'],
                env=env, check=True, capture_output=True, text=True, timeout=30)
        services = json.loads(result.stdout)['services']
        keys = ['PPE_CONTINUOUS_EVENTS_ENABLED', 'PPE_CAPTURE_CONTEXT_ENABLED',
                'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS']
        forwarded = {key:services['api']['environment'][key] for key in keys}
        for service in ['maintenance', 'db-init']:
            self.assertEqual({key:services[service]['environment'][key] for key in keys}, forwarded)
        return forwarded

    def exercise(self, env):
        with patch.dict(os.environ, env), isolated_api() as client:
            admin = login(client)
            token = client.post('/api/nodes', headers=admin,
                                json={'node_id':'release-check','name':'release-check'}).json()['token']
            headers = {'Authorization':'Bearer '+token}
            client.put('/api/processing/workers/release-check', headers=admin, json={'enabled':True}).raise_for_status()
            event = 'EVT_'+uuid4().hex; session = uuid4().hex
            start = client.post('/api/processing/continuous-events', headers=headers, json={
                'event_id':event,'camera_id':'CAM_001','tracking_id':'release-check', 'ppe_type':'helmet',
                'timestamp_start':'2026-11-15T00:00:00Z','timestamp_end':'2026-11-15T00:00:03Z',
                'confidence':None,'frame_sample_count':46,'model_version':'test', 'review_only':True,
                'session_id':session,'policy_key':'release-check','observed_seconds':3,'gap_seconds':0,'gap_count':0})
            stop = client.post('/api/video/streams/'+str(uuid4())+'/stop', headers=headers,
                               json={'capture_stopped':True,'final_sequence':-1})
            capture = None
            if start.status_code == 201:
                capture = client.post('/api/processing/continuous-events/'+event+'/cameras/CAM_001/capture-reports',
                    headers=headers,json={'session_id':session,'sequence':1,'state':'complete',
                    'captured_ranges':[{'start_at':'2026-11-14T23:59:58Z','end_at':'2026-11-15T00:00:05Z'}],
                    'missing_ranges':[],'time_basis':'shared_monotonic','clock_offset_seconds':0,'clock_uncertainty_seconds':0})
            policy = client.get('/api/auto-decisions/settings', headers=admin).raise_for_status().json()
            self.assertEqual(policy['mode'], 'off')
            self.assertFalse(policy['automatic_confirmation_enabled'])
            return start, stop, capture

    def test_release_defaults_keep_both_protocols_off(self):
        env = self.environment()
        self.assertEqual(env['PPE_CONTINUOUS_EVENTS_ENABLED'], 'false')
        self.assertEqual(env['PPE_CAPTURE_CONTEXT_ENABLED'], 'false')
        self.assertEqual(env['PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS'], '')
        start, stop, _ = self.exercise(env)
        self.assertEqual(start.status_code,503,start.text)
        self.assertEqual(stop.status_code,503,stop.text)

    def test_explicit_protocol_opt_in_reaches_api_but_never_enables_automatic_review(self):
        env = self.environment(PPE_CONTINUOUS_EVENTS_ENABLED='true', PPE_CAPTURE_CONTEXT_ENABLED='true',
                               PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS='0.025')
        self.assertEqual(env['PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS'], '0.025')
        start, stop, capture = self.exercise(env)
        # Context opt-in also enforces real provenance, rather than accepting
        # the test's context-free submission simply because the route is on.
        self.assertEqual(start.status_code,422,start.text)
        self.assertEqual(start.json()['detail'],'capture_context_required')
        self.assertEqual(stop.status_code,404,stop.text)

    def test_explicit_clock_budget_reaches_evidence_check_without_approving(self):
        env = self.environment(PPE_CONTINUOUS_EVENTS_ENABLED='true',
                               PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS='0.025')
        start, _, capture = self.exercise(env)
        self.assertEqual(start.status_code,201,start.text)
        self.assertEqual(capture.status_code,409,capture.text)
        self.assertEqual(capture.json()['detail'],'continuous_capture_incomplete')

    def test_opt_in_without_clock_policy_still_fails_closed(self):
        env = self.environment(PPE_CONTINUOUS_EVENTS_ENABLED='true')
        start, _, capture = self.exercise(env)
        self.assertEqual(start.status_code,201,start.text)
        self.assertEqual(capture.status_code,503,capture.text)
        self.assertEqual(capture.json()['detail'],'continuous_clock_policy_required')
