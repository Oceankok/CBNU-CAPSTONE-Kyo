"""Synthetic opt-in policies are test fixtures, never human validation records."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.tests import test_education_translations as translation_fixtures
from backend.tests import test_education_materials as material_fixtures
from backend.education import auto_translation as auto
from backend.education.models import MaterialContent
from backend.db.event_repository import get_connection
from backend.db.init_db import init_database
from backend.db.connection import schema_version
from backend.db.migrations import LATEST_VERSION


OUTPUT = MaterialContent(title='AI helmet', body='Inspect before work.', checklist=['Check fit'])


class AutoTranslationTests(unittest.TestCase):
    draft = material_fixtures.EducationMaterialTests.draft
    publish = material_fixtures.EducationMaterialTests.publish
    insert_recommendation = staticmethod(material_fixtures.EducationMaterialTests.insert_recommendation)
    translation = translation_fixtures.TranslationTests.translation
    publish_translation = translation_fixtures.TranslationTests.publish_translation

    def setUp(self):
        material_fixtures.EducationMaterialTests.setUp(self)
        self.original = self.publish(self.draft().json()['material_id']).json()
        self.base = '/api/education/materials/' + self.original['material_id'] + '/translations'
        self.directory = self.enterContext(TemporaryDirectory())
        self.path = Path(self.directory) / 'policy.json'
        self.enterContext(patch.dict(os.environ, {'PPE_AUTO_TRANSLATION_POLICY': str(self.path),
            'PPE_EDUCATION_TRANSLATION_PROVIDER': 'openai', 'PPE_EDUCATION_TRANSLATION_OPENAI_API_KEY': 'synthetic-not-a-key',
            'PPE_EDUCATION_TRANSLATION_MODEL': 'synthetic-model'}))
        self.configure()

    def row(self):
        with get_connection() as conn:
            return dict(auto.source_row(conn, 'material', self.original['material_id']))

    def configure(self, **changes):
        self.config = {'enabled': True, 'allow_external_processing': True, 'daily_requests': 5,
            'total_requests': 10, 'total_source_chars': 10000, 'max_source_chars': 2000, 'max_queued': 20,
            'verified_catalog': [{'content_sha256': auto.digest(self.row()), 'language': 'en', 'ppe_type': 'helmet',
                'classification': 'basic_ppe', 'safety_critical': False, 'representative_validation_passed': True,
                'validation_reference': 'SYNTHETIC TEST ONLY - NOT A REAL APPROVAL'}]}
        self.config.update(changes)
        self.path.write_text(json.dumps(self.config), encoding='utf8')

    def item(self, language='en'):
        response = self.client.get('/api/worker/education', params={'language': language}, headers=self.worker)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        return response.json()['items'][0]

    def enqueue(self, **changes):
        body = {'source_kind': 'material', 'source_id': self.original['material_id'],
                'source_version': self.original['version'], 'language': 'en'}
        body.update(changes)
        return self.client.post('/api/worker/education-translations/request', headers=self.worker, json=body)

    def run_job(self, side_effect=None):
        with patch('backend.education.translation_provider.generate', side_effect=side_effect,
                   return_value=(OUTPUT, 'synthetic-model')) as generate:
            auto.run_once()
            return generate

    def test_get_is_read_only_then_async_cache_reuses_and_preserves_original(self):
        with patch('backend.education.translation_provider.generate') as provider:
            before = self.item()
            self.assertEqual(before['translation']['status'], 'missing')
            self.assertEqual(before['translation']['available_languages'], ['en'])
            with get_connection() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM education_translation_job').fetchone()[0], 0)
            provider.assert_not_called()
        self.assertEqual(self.enqueue().status_code, 202)
        self.assertEqual(self.item()['translation']['status'], 'pending')
        self.run_job().assert_called_once_with(MaterialContent(**before['original']), 'en')
        after = self.item()
        self.assertEqual(after['translation']['origin'], 'ai')
        self.assertEqual(after['translation']['status'], 'ready')
        self.assertEqual(after['language'], 'en')
        self.assertIn('AI helmet', after['material'])
        self.assertEqual(after['original'], before['original'])
        self.assertEqual(self.enqueue().json()['status'], 'ready')
        self.run_job().assert_not_called()
        for forbidden in ('validation_reference', 'failure_code', 'claim_token', 'job_id', 'created_by'):
            self.assertNotIn(forbidden, json.dumps(after))

    def test_concurrent_requests_and_independent_process_claims_dispatch_once(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            codes = list(pool.map(lambda _: self.enqueue().status_code, range(10)))
        self.assertEqual(codes, [202]*10)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM education_translation_job').fetchone()[0], 1)
        # Fresh interpreter processes share the real database/claim lock, no
        # process-local semaphore. Provider is replaced before run_once.
        code = '''import os,runpy,sys
from backend.education import translation_provider as p
from backend.education.models import MaterialContent
def generate(*args):
    with open(os.environ['PPE_TEST_PROVIDER_LOG'],'a') as log:log.write('dispatch\\n')
    return MaterialContent(title='Process output',body='Synthetic',checklist=['Check']), 'mock'
p.generate=generate
sys.argv=['translation-worker','--once']
runpy.run_module('backend.education.auto_translation',run_name='__main__')
'''
        log = Path(self.directory) / 'dispatch.log'
        self.enterContext(patch.dict(os.environ, {'PPE_TEST_PROVIDER_LOG':str(log)}))
        processes = [subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr.decode(errors='replace'))
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT dispatched_count FROM education_translation_budget WHERE bucket='total'").fetchone()[0], 1)
        self.assertEqual(log.read_text().splitlines(),['dispatch'])
        self.assertEqual(self.item()['translation']['status'], 'ready')

    def test_manual_base_language_overrides_ai_region_even_when_policy_off(self):
        region = {**self.config['verified_catalog'][0], 'language': 'en-US'}
        self.configure(verified_catalog=[region])
        self.assertEqual(self.enqueue(language='en-US').status_code, 202)
        self.run_job()
        manual = self.translation().json()
        self.assertEqual(self.publish_translation(manual).status_code, 200)
        with get_connection() as conn:
            conn.execute("UPDATE education_translation SET provider='openai' WHERE translation_id=?",(manual['translation_id'],))
        for enabled in (True, False):
            self.config['enabled'] = enabled
            self.path.write_text(json.dumps(self.config), encoding='utf8')
            item = self.item('en-US')
            self.assertEqual(item['language'], 'en')
            self.assertEqual(item['translation']['origin'], 'reviewed')
            self.assertIn('Helmet check', item['material'])
            self.assertTrue(item['translation_fallback'])

    def test_region_missing_uses_verified_base_and_explicit_korean_never_enqueues(self):
        self.assertEqual(self.item('en-US')['translation']['status'], 'missing')
        self.assertEqual(self.enqueue(language='en-US').status_code, 202)
        self.run_job().assert_called_once()
        self.assertEqual(self.item('en-US')['language'], 'en')
        self.assertEqual(self.item('ko')['translation']['status'], 'original')
        self.assertEqual(self.enqueue(language='ko').status_code, 409)

    def test_browser_lower_preference_can_request_the_exact_verified_language(self):
        item=self.client.get('/api/worker/education',headers={**self.worker,'Accept-Language':'fr,en;q=0.8'}).json()['items'][0]
        self.assertEqual(item['requested_language'],'fr')
        self.assertEqual(item['translation']['status'],'missing')
        self.assertEqual(item['translation']['request_language'],'en')
        self.assertEqual(self.enqueue(language=item['translation']['request_language']).status_code,202)
        self.run_job().assert_called_once()
        item=self.client.get('/api/worker/education',headers={**self.worker,'Accept-Language':'fr,en;q=0.8'}).json()['items'][0]
        self.assertEqual(item['language'],'en')
        self.assertTrue(item['translation_fallback'])

    def test_policy_fail_closed_and_procedure_not_eligible_from_ppe_label(self):
        self.configure(enabled=False)
        self.assertEqual(self.item()['translation']['status'], 'disabled')
        self.assertEqual(self.enqueue().status_code, 409)
        self.configure(verified_catalog=[])
        self.assertEqual(self.item()['translation']['status'], 'ineligible')
        self.assertEqual(self.enqueue().status_code, 409)
        self.configure()
        with get_connection() as conn:
            conn.execute("UPDATE education_material SET body='Stop and isolate machinery in sequence' WHERE material_id=?", (self.original['material_id'],))
        self.assertEqual(self.item()['translation']['status'], 'ineligible')
        self.assertEqual(self.enqueue().status_code, 409)
        self.configure()
        self.config['verified_catalog'][0]['safety_critical'] = True
        self.path.write_text(json.dumps(self.config), encoding='utf8')
        self.assertIsNone(auto.policy())
        self.run_job().assert_not_called()

    def test_generation_off_keeps_current_cache_and_provider_unconfigured_cannot_enqueue(self):
        self.enqueue(); self.run_job()
        self.configure(enabled=False, allow_external_processing=False)
        self.assertEqual(self.item()['translation']['status'], 'ready')
        self.assertEqual(self.item()['language'], 'en')
        with get_connection() as conn:
            conn.execute('UPDATE education_material SET version=version+1 WHERE material_id=?', (self.original['material_id'],))
        self.original['version'] += 1
        self.configure()
        with patch.dict(os.environ, {'PPE_EDUCATION_TRANSLATION_PROVIDER':''}):
            self.assertEqual(self.item()['translation']['status'], 'disabled')
            self.assertEqual(self.enqueue().status_code,409)
            self.run_job().assert_not_called()

    def test_manual_after_enqueue_skips_cost_but_inflight_manual_still_wins(self):
        self.enqueue()
        self.publish_translation(self.translation().json())
        self.run_job().assert_not_called()
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM education_translation_budget').fetchone()[0],0)
        with get_connection() as conn:
            conn.execute("UPDATE education_translation SET status='superseded'")
            conn.execute('UPDATE education_material SET version=version+1 WHERE material_id=?',(self.original['material_id'],))
        self.original['version'] += 1
        self.enqueue()
        def publish_during_call(*args):
            self.publish_translation(self.translation().json())
            return OUTPUT, 'mock'
        self.run_job(side_effect=publish_during_call).assert_called_once()
        self.assertEqual(self.item()['translation']['origin'],'reviewed')

    def test_stalled_queue_is_terminal_without_dispatch_and_admin_copy_is_versioned(self):
        self.enqueue()
        with get_connection() as conn:
            conn.execute("UPDATE education_translation_job SET created_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.item()['translation']['status'],'failed')
        self.assertEqual(self.enqueue().status_code,409)
        self.run_job().assert_not_called()
        with get_connection() as conn:
            conn.execute('UPDATE education_material SET version=version+1 WHERE material_id=?',(self.original['material_id'],))
        self.original['version'] += 1
        self.enqueue(); self.run_job()
        self.configure(enabled=False)
        endpoint='/api/education/materials/'+self.original['material_id']+'/automatic-translations'
        self.assertEqual(self.client.get(endpoint,params={'source_version':2},headers=self.worker).status_code,403)
        self.assertEqual(self.client.get(endpoint,params={'source_version':1},headers=self.admin).status_code,409)
        response=self.client.get(endpoint,params={'source_version':2},headers=self.admin)
        self.assertEqual(response.status_code,200,response.text)
        cached=response.json()['items'][0]
        self.assertEqual(cached['content'],OUTPUT.model_dump())
        self.assertNotIn('job_id',cached)
        manual=self.client.post(self.base,headers=self.admin,json={**cached['content'],'language':'en','expected_source_version':2})
        self.assertEqual(manual.status_code,201,manual.text)
        self.assertEqual(self.publish_translation(manual.json()).status_code,200)
        self.assertEqual(self.item()['translation']['origin'],'reviewed')

    def test_provider_error_crash_and_late_results_never_redispatch(self):
        self.enqueue()
        self.run_job(side_effect=TimeoutError('synthetic ambiguous result')).assert_called_once()
        self.assertEqual(self.item()['translation']['status'], 'failed')
        self.assertIn(self.original['body'], self.item()['material'])
        self.assertEqual(self.enqueue().status_code, 409)
        self.run_job().assert_not_called()
        # A second source version simulates a new, independent job after edit.
        with get_connection() as conn:
            conn.execute('UPDATE education_material SET version=version+1 WHERE material_id=?', (self.original['material_id'],))
        self.original['version'] += 1
        self.enqueue()
        queued, token, _ = auto.claim()
        with get_connection() as conn:
            conn.execute("UPDATE education_translation_job SET deadline_at='2000-01-01T00:00:00+00:00' WHERE job_id=?", (queued['job_id'],))
        self.run_job().assert_not_called()
        auto.complete(queued, token, OUTPUT, 'late')
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT state FROM education_translation_job WHERE job_id=?', (queued['job_id'],)).fetchone()[0], 'unknown')

    def test_source_change_during_provider_discards_output_and_keeps_korean(self):
        self.enqueue()
        def changed(*args):
            with get_connection() as conn:
                conn.execute('UPDATE education_material SET version=version+1 WHERE material_id=?', (self.original['material_id'],))
            return OUTPUT, 'mock'
        self.run_job(side_effect=changed)
        self.assertNotIn('AI helmet', self.item()['material'])
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT state FROM education_translation_job').fetchone()[0], 'stale')
            self.assertIsNone(conn.execute('SELECT body FROM education_translation_job').fetchone()[0])

    def test_unpublish_or_catalog_revocation_hides_cached_ai(self):
        self.enqueue(); self.run_job()
        self.configure(verified_catalog=[])
        self.assertEqual(self.item()['language'], 'ko')
        self.configure()
        self.assertEqual(self.item()['language'], 'en')
        self.client.post('/api/education/materials/' + self.original['material_id'] + '/unpublish', headers=self.admin, json={'expected_version': 1})
        self.assertIsNone(self.item()['material'])
        self.assertIsNone(self.item()['original'])
        self.assertIsNone(self.item()['translation'])
        self.assertEqual(self.enqueue().status_code, 404)

    def test_auth_membership_current_quarter_and_payload_boundaries(self):
        body = {'source_kind':'material','source_id':self.original['material_id'],'source_version':1,'language':'en'}
        endpoint = '/api/worker/education-translations/request'
        self.assertEqual(self.client.post(endpoint,json=body).status_code,401)
        self.assertEqual(self.client.post(endpoint,json=body,headers=self.admin).status_code,403)
        self.assertEqual(self.client.post(endpoint,json={**body,'body':'injected'},headers=self.worker).status_code,422)
        self.assertEqual(self.enqueue(source_version=0).status_code,409)
        with get_connection() as conn:
            conn.execute("DELETE FROM user_zone WHERE user_id='worker01'")
        self.assertEqual(self.enqueue().status_code,404)
        self.assertEqual(self.client.get('/api/worker/education',headers=self.worker).json()['groups'],[])

    def test_persistent_quota_survives_restart_and_prevents_second_source_dispatch(self):
        self.configure(total_requests=1)
        self.enqueue(); self.run_job().assert_called_once()
        with get_connection() as conn:
            before=[dict(r) for r in conn.execute('SELECT * FROM education_translation_budget ORDER BY bucket')]
        init_database()
        with get_connection() as conn:
            self.assertEqual(schema_version(conn),LATEST_VERSION)
            self.assertEqual([dict(r) for r in conn.execute('SELECT * FROM education_translation_budget ORDER BY bucket')],before)
            conn.execute('UPDATE education_material SET version=version+1 WHERE material_id=?',(self.original['material_id'],))
        self.original['version'] += 1
        self.enqueue(); self.run_job().assert_not_called()
        self.assertEqual(self.item()['translation']['status'],'failed')

    def test_bundle_cache_copy_manual_precedence_and_source_unpublish(self):
        preview=self.client.post('/api/education/bundles/preview',headers=self.admin,
            json={'recommendation_ids':['REC_CURRENT','REC_OTHER']}).json()
        original=self.client.post('/api/education/bundles',headers=self.admin,json={
            **OUTPUT.model_dump(),'recommendation_ids':['REC_CURRENT','REC_OTHER'],'expected_snapshot':preview['snapshot']}).json()
        original=self.client.post('/api/education/bundles/'+original['bundle_id']+'/publish',headers=self.admin,
            json={'expected_version':0,'approved':True}).json()
        with get_connection() as conn:
            row=auto.source_row(conn,'bundle',original['bundle_id'])
            entry={**self.config['verified_catalog'][0],'content_sha256':auto.digest(row)}
        self.configure(verified_catalog=[entry])
        enqueued=self.enqueue(source_kind='bundle',source_id=original['bundle_id'],source_version=1)
        self.assertEqual(enqueued.status_code,202,enqueued.text)
        self.run_job().assert_called_once()
        items=self.client.get('/api/worker/education-bundles?language=en',headers=self.worker).json()['items']
        self.assertEqual(items[0]['translation']['origin'],'ai')
        self.assertEqual(items[0]['assigned_zones'],['프레스 구역'])
        self.assertNotIn('sources',items[0])
        copy=self.client.get('/api/education/bundles/'+original['bundle_id']+'/automatic-translations?source_version=1',headers=self.admin).json()['items'][0]
        manual=self.client.post('/api/education/bundles/'+original['bundle_id']+'/translations',headers=self.admin,
            json={**copy['content'],'language':'en','expected_source_version':1}).json()
        self.assertEqual(self.publish_translation(manual).status_code,200)
        self.assertEqual(self.client.get('/api/worker/education-bundles?language=en',headers=self.worker).json()['items'][0]['translation']['origin'],'reviewed')
        self.client.post('/api/education/bundles/'+original['bundle_id']+'/unpublish',headers=self.admin,json={'expected_version':1})
        self.assertEqual(self.client.get('/api/worker/education-bundles?language=en',headers=self.worker).json()['items'],[])

    def test_queue_source_character_and_daily_limits_fail_without_extra_dispatch(self):
        self.configure(max_source_chars=1)
        self.assertEqual(self.enqueue().status_code,409)
        self.configure(daily_requests=1,total_requests=5)
        self.enqueue(); self.run_job().assert_called_once()
        with get_connection() as conn:
            conn.execute('UPDATE education_material SET version=version+1 WHERE material_id=?',(self.original['material_id'],))
        self.original['version']+=1
        self.assertEqual(self.enqueue().status_code,202)
        self.run_job().assert_not_called()
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT dispatched_count FROM education_translation_budget WHERE bucket='total'").fetchone()[0],1)
            conn.execute('UPDATE education_material SET version=version+1 WHERE material_id=?',(self.original['material_id'],))
        self.original['version']+=1
        self.configure(max_queued=1)
        self.assertEqual(self.enqueue().status_code,202)
        # A distinct version cannot bypass a full durable queue.
        with get_connection() as conn:
            conn.execute('UPDATE education_material SET version=version+1 WHERE material_id=?',(self.original['material_id'],))
        self.original['version']+=1
        self.assertEqual(self.enqueue().status_code,429)

    def test_v35_upgrade_preserves_existing_source_rows_and_zone_revisions(self):
        with get_connection() as conn:
            before={table:[dict(r) for r in conn.execute('SELECT * FROM '+table)] for table in
                    ('education_material','education_translation','education_bundle','zone_rule','candidate_event')}
            conn.execute('DROP TABLE education_translation_job')
            conn.execute('DROP TABLE education_translation_budget')
            if getattr(conn,'dialect','sqlite')=='postgresql':
                conn.execute('UPDATE app_schema_version SET version=35')
            else:
                conn.execute('PRAGMA user_version=35')
        init_database(); init_database()
        with get_connection() as conn:
            self.assertEqual(schema_version(conn),LATEST_VERSION)
            for table, rows in before.items():
                self.assertEqual([dict(r) for r in conn.execute('SELECT * FROM '+table)], rows)
        self.assertEqual(self.item()['translation']['status'],'missing')
