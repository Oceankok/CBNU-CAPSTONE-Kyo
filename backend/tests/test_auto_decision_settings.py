"""Synthetic isolated policy/hook regression; not model accuracy validation."""
from unittest import TestCase
from unittest.mock import patch
from backend.tests import test_auto_decision_preview as baseline
from backend.db.event_repository import get_connection
from backend.auto_decision.settings import process_ready
from backend.db.connection import begin_write


class SettingsTests(TestCase):
    setUp=baseline.AutoDecisionPreviewTests.setUp
    state=baseline.AutoDecisionPreviewTests.state

    def body(self, mode='shadow', version=0):
        return {'mode':mode,'threshold':0.9,'model_versions':['test-model'],
                'ppe_types':['helmet'],'camera_ids':['CAM_001'],'zone_names':[],
                'validation_reference':None,'expected_version':version,'reason':'synthetic policy test'}

    def save(self, body=None):
        return self.client.put('/api/auto-decisions/settings',headers=self.admin,json=body or self.body())

    def hook(self):
        with get_connection() as conn:
            begin_write(conn)
            return process_ready(conn,self.ident)

    def test_default_off_read_only_and_admin_only(self):
        before=self.state()
        result=self.client.get('/api/auto-decisions/settings',headers=self.admin).json()
        self.assertEqual(result['mode'],'off');self.assertEqual(result['version'],0)
        self.assertFalse(result['automatic_confirmation_enabled'])
        self.assertIn('calibrated_violation_probability_unavailable',result['activation_blockers'])
        self.assertIn('model_artifact_attestation_unavailable',result['activation_blockers'])
        self.assertIn('whole_event_classifier_evidence_unavailable',result['activation_blockers'])
        self.assertFalse(self.hook()['applied'])
        self.assertEqual(self.client.get('/api/auto-decisions/settings').status_code,401)
        self.assertEqual(before,self.state())

    def test_policy_cas_and_audit(self):
        response=self.save();self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['version'],1)
        self.assertEqual(self.save().status_code,409)
        history=self.client.get('/api/auto-decisions/settings/history',headers=self.admin).json()['items']
        self.assertEqual(len(history),1);self.assertEqual(history[0]['changed_by'],'admin02')
        self.assertEqual(history[0]['policy']['mode'],'shadow')
        self.assertEqual(self.save(self.body('off',1)).status_code,200)
        self.assertEqual(self.hook()['reason'],'automatic_confirmation_disabled')

    def test_request_reference_or_env_cannot_enable(self):
        before=self.state();body=self.body('enforce');body['validation_reference']='invented-approved-report'
        with patch.dict('os.environ',{'PPE_AUTO_DECISION_ENABLED':'true'}):
            response=self.save(body)
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['detail']['code'],'automatic_confirmation_not_ready')
        self.assertEqual(self.client.get('/api/auto-decisions/settings',headers=self.admin).json()['version'],0)
        self.assertEqual(before,self.state())

    def test_reject_invalid_scope_and_values(self):
        for change in ({'camera_ids':[]},{'camera_ids':['missing']},{'zone_names':['missing']},
                       {'threshold':True},{'threshold':1.1},{'reason':' '},{'model_versions':[' x']},
                       {'ppe_types':['invalid']},{'enabled':True},{'expected_version':False}):
            with self.subTest(change=change):self.assertEqual(self.save({**self.body(),**change}).status_code,422)

    def test_shadow_idempotent_and_never_review(self):
        self.assertEqual(self.save().status_code,200)
        before=self.state()
        first=self.hook();second=self.hook()
        self.assertEqual(first['reason'],'shadow_only');self.assertFalse(first['applied'])
        self.assertEqual(first,second);self.assertEqual(before,self.state())
        history=self.client.get('/api/auto-decisions/shadow',headers=self.admin,params={'event_id':self.ident}).json()
        self.assertEqual(len(history['items']),1)
        self.assertEqual(history['items'][0]['result']['source'],'completion_hook_shadow')
        self.assertEqual(history['items'][0]['result']['settings_version'],1)
        diagnosis=history['items'][0]['result']['trusted_score_adapter']
        self.assertFalse(diagnosis['automatic_confirmation_allowed'])
        self.assertIsNone(diagnosis['calibrated_violation_probability'])

    def test_review_only_even_high_person_score_abstains(self):
        self.assertEqual(self.save({**self.body(),'threshold':0}).status_code,200)
        with get_connection() as conn:
            conn.execute('UPDATE processing_review_only_event SET review_only=1 WHERE event_id=?',(self.ident,))
            conn.execute('UPDATE candidate_event SET ai_confidence=1 WHERE event_id=?',(self.ident,))
        before=self.state();result=self.hook()
        self.assertFalse(result['threshold_candidate']);self.assertEqual(before,self.state())
        history=self.client.get('/api/auto-decisions/shadow',headers=self.admin,params={'event_id':self.ident}).json()
        self.assertIn('review_only',history['items'][0]['result']['blocking_reasons'])
        self.assertIsNone(history['items'][0]['result']['observed_confidence'])

    def test_scope_uses_event_snapshot_and_missing_scope_does_not_write_shadow(self):
        with get_connection() as conn:
            zone=conn.execute('SELECT zone_name FROM event_zone_snapshot WHERE event_id=?',(self.ident,)).fetchone()['zone_name']
        self.assertEqual(self.save({**self.body(),'zone_names':[zone]}).status_code,200)
        self.assertEqual(self.hook()['reason'],'shadow_only')
        self.assertEqual(self.save({**self.body(version=1),'model_versions':['other-model']}).status_code,200)
        self.assertEqual(self.hook()['reason'],'event_out_of_scope')

    def test_reinitialization_preserves_policy_and_history(self):
        self.assertEqual(self.save().status_code,200)
        from backend.db.init_db import init_database
        init_database()
        self.assertEqual(self.client.get('/api/auto-decisions/settings',headers=self.admin).json()['version'],1)
        self.assertEqual(len(self.client.get('/api/auto-decisions/settings/history',headers=self.admin).json()['items']),1)

    def test_hook_transaction_rollback_drops_shadow(self):
        self.assertEqual(self.save().status_code,200)
        with self.assertRaisesRegex(RuntimeError,'rollback fixture'):
            with get_connection() as conn:
                begin_write(conn)
                process_ready(conn,self.ident)
                raise RuntimeError('rollback fixture')
        result=self.client.get('/api/auto-decisions/shadow',headers=self.admin,params={'event_id':self.ident}).json()
        self.assertEqual(result['items'],[])

    def test_stored_enforce_loses_adapter_and_stays_pending(self):
        # Synthetic corrupted/obsolete policy is never authorization at hook time.
        self.assertEqual(self.save().status_code,200)
        import json
        with get_connection() as conn:
            row=conn.execute('SELECT policy_json FROM auto_decision_settings WHERE singleton=1').fetchone()
            value=json.loads(row['policy_json']);value['mode']='enforce';value['validation_reference']='fixture'
            conn.execute('UPDATE auto_decision_settings SET policy_json=? WHERE singleton=1',(json.dumps(value),))
        before=self.state()
        self.assertEqual(self.hook()['reason'],'automatic_confirmation_not_ready')
        self.assertEqual(before,self.state())

    def test_schema32_upgrade_and_divergent33_rejected_before_seed(self):
        from backend.db.init_db import init_database
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import sqlite3, os
        # A true schema32 predecessor keeps its existing rows and starts OFF.
        with get_connection() as conn:
            conn.execute('DROP TABLE auto_decision_settings_history')
            conn.execute('DROP TABLE auto_decision_settings')
            if getattr(conn,'dialect','sqlite')=='postgresql':
                conn.execute('UPDATE app_schema_version SET version=32 WHERE singleton=1')
            else:
                conn.execute('PRAGMA user_version=32')
        before=self.state();init_database()
        self.assertEqual(before,self.state())
        self.assertEqual(self.client.get('/api/auto-decisions/settings',headers=self.admin).json()['mode'],'off')
        with TemporaryDirectory() as directory:
            path=Path(directory)/'divergent.db'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE marker(value TEXT)');conn.execute("INSERT INTO marker VALUES('preserved')")
                conn.execute('PRAGMA user_version=33')
            with patch.dict(os.environ,{'PPE_DATABASE_URL':'','PPE_DB_PATH':str(path)}):
                with self.assertRaisesRegex(RuntimeError,'Incompatible schema33'):init_database()
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute('SELECT value FROM marker').fetchone()[0],'preserved')
                self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[('marker',)])

    def test_scoped_preview_applies_camera_and_historical_zone_without_writes(self):
        body={k:v for k,v in self.body('off').items() if k not in ('expected_version','reason')}
        body['event_ids']=[self.ident]
        before=self.state()
        for change,reason in (({'camera_ids':[]},'camera_out_of_scope'),
                              ({'camera_ids':['OTHER_CAMERA']},'camera_out_of_scope'),
                              ({'zone_names':['OTHER_ZONE']},'zone_out_of_scope')):
            response=self.client.post('/api/auto-decisions/settings/preview',headers=self.admin,json={**body,**change})
            self.assertEqual(response.status_code,200,response.text)
            item=response.json()['items'][0]
            self.assertFalse(item['threshold_candidate']);self.assertTrue(item['scope_applied'])
            self.assertEqual(item['trusted_score_adapter']['mode'],'diagnostic_only')
            self.assertFalse(item['trusted_score_adapter']['automatic_confirmation_allowed'])
            self.assertIn(reason,item['blocking_reasons'])
        self.assertEqual(before,self.state())
        self.assertEqual(self.client.get('/api/auto-decisions/settings',headers=self.admin).json()['version'],0)

    def test_concurrent_policy_updates_only_one_version_wins(self):
        from concurrent.futures import ThreadPoolExecutor
        from backend.auto_decision.settings import SettingsUpdate, update_settings
        from fastapi import HTTPException
        def update():
            try:
                return update_settings(SettingsUpdate(**self.body()), {'user_id':'admin02'})['version']
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(lambda _:update(),range(2)))
        self.assertEqual(sorted(outcomes),[1,409])
        self.assertEqual(len(self.client.get('/api/auto-decisions/settings/history',headers=self.admin).json()['items']),1)
