import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from fastapi import HTTPException, Response
from backend.tests import test_auto_decision_preview as preview_tests
from backend.tests.support import login
from backend.db.event_repository import get_connection
from backend.db.connection import is_postgres, schema_version
from backend.db.migrations import LATEST_VERSION, apply_migrations
from backend.auth.service import create_user
from backend.auto_decision.shadow import store_shadow, ShadowRequest


class AutoDecisionShadowTests(unittest.TestCase):
    setUp = preview_tests.AutoDecisionPreviewTests.setUp
    state = preview_tests.AutoDecisionPreviewTests.state

    def save(self, key='trial_1', **updates):
        return self.client.post('/api/auto-decisions/shadow', headers=self.admin,
            json={**self.policy, 'request_key': key, **updates})

    def counts(self):
        with get_connection() as conn:
            return tuple(conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                         for table in ('auto_decision_shadow_run', 'auto_decision_shadow_item'))

    def test_persisted_history_never_reviews_changes_statistics_or_enables_automatic_confirmation(self):
        before=self.state()
        response=self.save(); self.assertEqual(response.status_code,201,response.text)
        body=response.json()
        self.assertEqual(self.counts(),(1,1))
        self.assertFalse(body['automatic_confirmation_enabled'])
        self.assertTrue(body['persisted'])
        self.assertFalse(body['items'][0]['automatic_confirmation_allowed'])
        self.assertTrue(body['items'][0]['threshold_candidate'])
        self.assertEqual(body['actor_id'],'admin02')
        self.assertEqual(body['policy']['threshold'],.9)
        self.assertEqual(before,self.state())
        for fragment in ('storage/fake',str(self.video),'request_digest'):
            self.assertNotIn(fragment,response.text)
        fetched=self.client.get('/api/auto-decisions/shadow/'+body['run_id'],headers=self.admin)
        self.assertEqual(fetched.status_code,200)
        self.assertEqual(fetched.json()['items'],body['items'])
        listing=self.client.get('/api/auto-decisions/shadow',headers=self.admin,params={'event_id':self.ident}).json()
        self.assertEqual(listing['items'][0]['result'],body['items'][0])
        self.assertTrue(listing['persisted'])
        self.assertIn('historical_diagnostic_not_current_state',listing['limitations'])

    def test_retry_is_original_history_not_a_new_evaluation(self):
        first=self.save().json()
        with get_connection() as conn:
            conn.execute('UPDATE candidate_event SET ai_confidence=.2 WHERE event_id=?',(self.ident,))
        retry=self.save();self.assertEqual(retry.status_code,200,retry.text)
        self.assertFalse(retry.json()['created'])
        self.assertEqual(retry.json()['run_id'],first['run_id'])
        self.assertEqual(retry.json()['items'],first['items'])
        self.assertEqual(self.counts(),(1,1))
        new=self.save('trial_2').json()
        self.assertFalse(new['items'][0]['threshold_candidate'])
        self.assertNotEqual(new['items'][0]['evidence_fingerprint'],first['items'][0]['evidence_fingerprint'])

    def test_same_key_different_request_conflicts_and_does_not_write(self):
        self.save()
        response=self.save(threshold=.8)
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()['detail'],'shadow_request_key_conflict')
        self.assertEqual(self.counts(),(1,1))

    def test_partial_batch_and_unexpected_error_roll_back_everything(self):
        response=self.save(event_ids=[self.ident,'missing'])
        self.assertEqual(response.status_code,404,response.text)
        self.assertEqual(self.counts(),(0,0))
        with patch('backend.auto_decision.shadow.inspect',side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):self.save()
        self.assertEqual(self.counts(),(0,0))
        self.assertEqual(self.save().status_code,201)

    def test_deleting_event_removes_result_and_retry_never_recreates_it(self):
        first=self.save().json()
        with get_connection() as conn:
            conn.execute('DELETE FROM candidate_event WHERE event_id=?',(self.ident,))
        self.assertEqual(self.counts(),(1,0))
        retry=self.save();self.assertEqual(retry.status_code,200,retry.text)
        self.assertEqual(retry.json()['run_id'],first['run_id'])
        self.assertEqual(retry.json()['items'],[])
        self.assertEqual(retry.json()['removed_item_count'],1)
        fetched=self.client.get('/api/auto-decisions/shadow/'+first['run_id'],headers=self.admin)
        self.assertNotIn(self.ident,fetched.text)
        self.assertEqual(self.save('new').status_code,404)
        self.assertEqual(self.counts(),(1,0))

    def test_actor_namespaces_keys_and_existing_human_review_is_blocked(self):
        first=self.save().json()
        create_user('admin03','test-password','Second admin','admin')
        self.admin=login(self.client,'admin03')
        with get_connection() as conn:
            conn.execute("INSERT INTO event_review(review_id,event_id,reviewer_id,review_result) VALUES('human',?,'admin02','hold')",(self.ident,))
        second=self.save().json()
        self.assertNotEqual(first['run_id'],second['run_id'])
        self.assertEqual(second['actor_id'],'admin03')
        self.assertFalse(second['items'][0]['threshold_candidate'])
        self.assertIn('already_reviewed_or_not_pending',second['items'][0]['blocking_reasons'])
        self.assertEqual(self.counts(),(2,2))

    def test_every_endpoint_admin_only_and_input_is_strict(self):
        payload={**self.policy,'request_key':'trial'}
        self.assertEqual(self.client.post('/api/auto-decisions/shadow',json=payload).status_code,401)
        worker=login(self.client,'worker01')
        self.assertEqual(self.client.post('/api/auto-decisions/shadow',json=payload,headers=worker).status_code,403)
        run=self.save().json()['run_id']
        for path in ('/api/auto-decisions/shadow?event_id='+self.ident,'/api/auto-decisions/shadow/'+run):
            self.assertEqual(self.client.get(path).status_code,401)
            self.assertEqual(self.client.get(path,headers=worker).status_code,403)
        for updates in ({'activate':True},{'items':[]},{'evidence_fingerprint':'fake'},{'threshold':None},{'request_key':' bad key'},{'request_key':''}):
            self.assertEqual(self.save(**updates).status_code,422)
        self.assertEqual(self.client.get('/api/auto-decisions/shadow',headers=self.admin,params={'event_id':self.ident,'limit':101}).status_code,422)
        self.assertEqual(self.client.get('/api/auto-decisions/shadow/missing',headers=self.admin).status_code,404)
        self.assertEqual(self.counts(),(1,1))

    def test_same_key_concurrent_retry_has_one_run(self):
        barrier=Barrier(2)
        body=ShadowRequest(**self.policy,request_key='parallel')
        def save(_):
            response=Response();barrier.wait(timeout=10)
            result=store_shadow(body,response,{'user_id':'admin02'})
            return response.status_code,result['run_id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(save,range(2)))
        self.assertEqual(sorted(code for code,_ in results),[200,201])
        self.assertEqual(len({ident for _,ident in results}),1)
        self.assertEqual(self.counts(),(1,1))

    def test_same_key_conflicting_concurrent_policy_has_one_winner(self):
        barrier=Barrier(2)
        def save(threshold):
            body=ShadowRequest(**{**self.policy,'threshold':threshold},request_key='parallel_conflict')
            barrier.wait(timeout=10)
            try:
                response=Response();store_shadow(body,response,{'user_id':'admin02'})
                return response.status_code
            except HTTPException as exc:return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(save,[.8,.9]))
        self.assertEqual(sorted(results),[201,409])
        self.assertEqual(self.counts(),(1,1))

    def test_v26_upgrade_repeatable_preserves_existing_rows_and_preview(self):
        before=self.state()
        with get_connection() as conn:
            conn.execute('DROP TABLE auto_decision_shadow_item')
            conn.execute('DROP TABLE auto_decision_shadow_run')
            if is_postgres():conn.execute('UPDATE app_schema_version SET version=26 WHERE singleton=1')
            else:conn.execute('PRAGMA user_version=26')
        if is_postgres():
            from backend.db.postgres import initialize
            initialize();initialize()
        else:
            with get_connection() as conn:apply_migrations(conn);apply_migrations(conn)
        with get_connection() as conn:
            self.assertEqual(schema_version(conn),LATEST_VERSION)
            self.assertIsNotNone(conn.execute("SELECT user_id FROM app_user WHERE user_id='admin02'").fetchone())
        self.assertEqual(before,self.state())
        self.assertEqual(self.client.post('/api/auto-decisions/preview',headers=self.admin,json=self.policy).status_code,200)
        self.assertEqual(self.save().status_code,201)
