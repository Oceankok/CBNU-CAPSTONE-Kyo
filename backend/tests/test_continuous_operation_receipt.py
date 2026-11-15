"""Read-only original-session receipt, real API/PG, synthetic video fixture."""
from unittest import TestCase
from unittest.mock import patch
import json
import os
import time
from uuid import uuid4

import httpx

from backend.tests import test_auto_rules_pipeline as fixture
from backend.db.event_repository import get_connection


class OperationReceiptTests(TestCase):
    def setUp(self):
        self.pipe=fixture.AutoRulesPipelineTests()
        self.addCleanup(self.pipe.doCleanups)
        self.pipe.setUp()

    def snapshot(self):
        with get_connection() as conn:
            return {table:[dict(row) for row in conn.execute('SELECT * FROM '+table)] for table in (
                'candidate_event','processing_event_lifecycle','processing_event_operation',
                'event_clip_request','processing_event_camera','processing_camera_capture',
                'event_review','event_review_history','event_retention',
                'auto_decision_shadow_run','auto_decision_shadow_item')}

    def receipt(self, body=None, headers=None, event_id=None):
        p=self.pipe
        result=p.client.post(p.base+'/'+(event_id or p.ident)+'/operation-receipt',
            headers=p.node if headers is None else headers,json=p.close_body if body is None else body)
        self.assertEqual(result.headers['cache-control'],'private, no-store')
        return result

    def test_complete_commit_lost_response_then_source_loss_receipt_is_exact_and_read_only(self):
        p=self.pipe;original=p.client.post
        def lose(url,*args,**kwargs):
            result=original(url,*args,**kwargs)
            if url.endswith('/updates') and kwargs['json']['kind']=='close':
                result.raise_for_status()
                raise httpx.ReadTimeout('synthetic response loss after complete close commit')
            return result
        with patch.object(p.client,'post',side_effect=lose):
            with self.assertRaises(httpx.ReadTimeout):p.create()
        original_body=json.loads(json.dumps(p.close_body))
        p.source.unlink()
        before=self.snapshot()
        for _ in range(2):
            response=self.receipt();response.raise_for_status();body=response.json()
            self.assertEqual(response.headers['cache-control'],'private, no-store')
            self.assertEqual(body['receipt_status'],'committed_exact')
            self.assertEqual(body['lifecycle']['phase'],'closed')
            self.assertEqual(body['sequence'],2);self.assertFalse(body['may_attempt_new_update'])
            self.assertTrue(body['accepted_operation']['body']['complete'])
            self.assertEqual(body['accepted_operation']['sha256'],body['submitted_sha256'])
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(p.close_body,original_body);p.no_reviews()
        self.assertFalse(p.preview()['rule_evaluation']['eligible'])

    def test_committed_different_body_never_acknowledges_or_overwrites(self):
        p=self.pipe;p.create();before=self.snapshot()
        changed={**p.close_body,'complete':False,'close_reason':'process_restart'}
        body=self.receipt(changed).raise_for_status().json()
        self.assertEqual(body['receipt_status'],'committed_different')
        self.assertTrue(body['accepted_operation']['body']['complete'])
        self.assertNotEqual(body['submitted_sha256'],body['accepted_operation']['sha256'])
        self.assertFalse(body['may_attempt_new_update'])
        self.assertEqual(p.client.post(p.base+'/'+p.ident+'/updates',headers=p.node,json=changed).status_code,409)
        self.assertEqual(self.snapshot(),before)

    def test_not_committed_and_future_sequence_are_advisory_without_writes(self):
        p=self.pipe;p.create(leave_open=True);before=self.snapshot()
        body=self.receipt().raise_for_status().json()
        self.assertEqual(body['receipt_status'],'not_committed')
        self.assertIsNone(body['accepted_operation']);self.assertTrue(body['may_attempt_new_update'])
        future=self.receipt({**p.close_body,'sequence':3}).raise_for_status().json()
        self.assertFalse(future['may_attempt_new_update'])
        self.assertEqual(self.snapshot(),before)
        # Explicit truthful disposition is a separate mutation at the next revision.
        p.client.post(p.base+'/'+p.ident+'/updates',headers=p.node,
                      json={**p.close_body,'complete':False,'close_reason':'process_restart'}).raise_for_status()
        conflicting=self.receipt().raise_for_status().json()
        self.assertEqual(conflicting['receipt_status'],'committed_different')
        self.assertEqual(conflicting['lifecycle']['phase'],'interrupted');p.no_reviews()

    def test_receipt_reuses_server_normalization_not_client_json_hash(self):
        p=self.pipe;p.create()
        alternative={k:v for k,v in p.close_body.items() if k!='confidence'}
        alternative['timestamp_end']=alternative['timestamp_end'].replace('+00:00','Z')
        result=self.receipt(alternative).raise_for_status().json()
        self.assertEqual(result['receipt_status'],'committed_exact')
        self.assertEqual(result['submitted_sha256'],result['accepted_operation']['sha256'])

    def test_cas_original_complete_wins_truthful_incomplete_cannot_overwrite(self):
        p=self.pipe;p.create(leave_open=True)
        original=dict(p.close_body)
        replacement={**original,'complete':False,'close_reason':'process_restart'}
        self.assertEqual(self.receipt(original).json()['receipt_status'],'not_committed')
        p.client.post(p.base+'/'+p.ident+'/updates',headers=p.node,json=original).raise_for_status()
        before=self.snapshot()
        self.assertEqual(p.client.post(p.base+'/'+p.ident+'/updates',headers=p.node,json=replacement).status_code,409)
        self.assertEqual(self.receipt(original).json()['receipt_status'],'committed_exact')
        self.assertEqual(self.receipt(replacement).json()['receipt_status'],'committed_different')
        self.assertEqual(self.snapshot(),before)
        p.reports(state='incomplete')
        self.assertFalse(p.preview()['rule_evaluation']['eligible']);p.no_reviews()

    def test_cas_incomplete_wins_late_original_complete_cannot_overwrite(self):
        p=self.pipe;p.create(leave_open=True)
        original=dict(p.close_body)
        replacement={**original,'complete':False,'close_reason':'process_restart'}
        self.assertTrue(self.receipt(original).json()['may_attempt_new_update'])
        p.client.post(p.base+'/'+p.ident+'/updates',headers=p.node,json=replacement).raise_for_status()
        before=self.snapshot()
        self.assertEqual(p.client.post(p.base+'/'+p.ident+'/updates',headers=p.node,json=original).status_code,409)
        self.assertEqual(self.receipt(replacement).json()['receipt_status'],'committed_exact')
        receipt=self.receipt(original).json()
        self.assertEqual(receipt['receipt_status'],'committed_different')
        self.assertFalse(receipt['accepted_operation']['body']['complete'])
        self.assertEqual(receipt['lifecycle']['phase'],'interrupted')
        self.assertEqual(self.snapshot(),before);p.no_reviews()

    def test_concurrent_original_and_incomplete_accept_only_one_immutable_body(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        p=self.pipe;p.create(leave_open=True)
        bodies=[dict(p.close_body),{**p.close_body,'complete':False,'close_reason':'process_restart'}]
        barrier=Barrier(2)
        def submit(body):
            barrier.wait(timeout=10)
            return p.client.post(p.base+'/'+p.ident+'/updates',headers=p.node,json=body).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(submit,body) for body in bodies]
            outcomes=[task.result(timeout=20) for task in futures]
        self.assertEqual(sorted(outcomes),[200,409])
        winner=outcomes.index(200);before=self.snapshot()
        self.assertEqual(self.receipt(bodies[winner]).json()['receipt_status'],'committed_exact')
        self.assertEqual(self.receipt(bodies[1-winner]).json()['receipt_status'],'committed_different')
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM processing_event_operation WHERE event_id=? AND sequence=2',(p.ident,)).fetchone()[0],1)
        self.assertEqual(self.snapshot(),before);p.no_reviews()

    def test_review_protection_is_orthogonal_to_historical_acceptance(self):
        p=self.pipe;p.create();p.reports()
        for camera in ('CAM_001','CAM_002'):p.upload(camera);p.process()
        p.client.post('/api/events/'+p.ident+'/review',headers=p.admin,
            json={'review_result':'hold','review_reason_code':'unclear'}).raise_for_status()
        before=self.snapshot();result=self.receipt().raise_for_status().json()
        self.assertEqual(result['receipt_status'],'committed_exact')
        self.assertEqual(result['recovery_blockers'],['already_reviewed'])
        self.assertFalse(result['may_attempt_new_update'])
        self.assertEqual(self.snapshot(),before)

    def test_lost_close_response_receipt_survives_server_source_expiry_and_human_review(self):
        p=self.pipe;original=p.client.post
        def lose(url,*args,**kwargs):
            result=original(url,*args,**kwargs)
            if url.endswith('/updates') and kwargs['json']['kind']=='close':
                result.raise_for_status();raise httpx.ReadTimeout('synthetic lost committed close')
            return result
        with patch.object(p.client,'post',side_effect=lose):
            with self.assertRaises(httpx.ReadTimeout):p.create()
        saved=dict(p.close_body)
        # Complete the real test media path after receipt recovery; no close rewrite.
        from inference_worker.continuous_transport import UploadPlan
        import hashlib
        for row in p.segments:
            p.plans[row['camera_id']]=UploadPlan(p.ident,row['request_id'],row['camera_id'],
                p.source.name,len(p.data),hashlib.sha256(p.data).hexdigest(),
                p.epoch-2,p.epoch+5.2,p.epoch-2,p.epoch+5.2,True)
        self.assertEqual(self.receipt().json()['receipt_status'],'committed_exact')
        p.reports()
        for camera in ('CAM_001','CAM_002'):p.upload(camera);p.process()
        # Simulate custody expiry independently of retained redacted evidence.
        with get_connection() as conn:
            conn.execute('UPDATE event_media SET source_expires_at=? WHERE event_id=?',(time.time()-10,p.ident))
        before=self.snapshot()
        self.assertEqual(self.receipt().json()['receipt_status'],'committed_exact')
        self.assertEqual(self.snapshot(),before)
        p.client.post('/api/events/'+p.ident+'/review',headers=p.admin,
            json={'review_result':'hold','review_reason_code':'unclear'}).raise_for_status()
        after=self.snapshot()
        receipt=self.receipt().raise_for_status().json()
        self.assertEqual(receipt['receipt_status'],'committed_exact')
        self.assertIn('already_reviewed',receipt['recovery_blockers'])
        self.assertEqual(self.snapshot(),after);self.assertEqual(p.close_body,saved)

    def test_unexpected_stored_fields_fail_closed_without_exposing_body(self):
        p=self.pipe;p.create()
        with get_connection() as conn:
            row=conn.execute('SELECT payload FROM processing_event_operation WHERE event_id=? AND sequence=2',(p.ident,)).fetchone()
            corrupt=json.loads(row[0]);corrupt['unexpected_private_field']='must-not-return'
            conn.execute('UPDATE processing_event_operation SET payload=? WHERE event_id=? AND sequence=2',(json.dumps(corrupt),p.ident))
        before=self.snapshot();result=self.receipt()
        self.assertEqual(result.status_code,409)
        self.assertEqual(result.json()['detail'],'continuous_operation_receipt_invalid')
        self.assertNotIn('must-not-return',result.text);self.assertEqual(self.snapshot(),before)

    def test_deleted_expired_merged_protections_preserve_historical_receipt(self):
        p=self.pipe;p.create()
        # Protection markers are isolated DB fixture setup, not recovery mutations.
        cases=[("UPDATE event_retention SET decision='delete' WHERE event_id=?",(p.ident,),'event_media_deleted'),
               ("UPDATE event_retention SET decision='retain',retain_until=? WHERE event_id=?",(time.time()-10,p.ident),'event_media_expired')]
        for statement,args,expected in cases:
            with get_connection() as conn:conn.execute(statement,args)
            before=self.snapshot();result=self.receipt().raise_for_status().json()
            self.assertEqual(result['receipt_status'],'committed_exact')
            self.assertIn(expected,result['recovery_blockers'])
            self.assertFalse(result['may_attempt_new_update']);self.assertEqual(self.snapshot(),before)
        with get_connection() as conn:
            conn.execute("UPDATE event_retention SET decision='pending',retain_until=NULL WHERE event_id=?",(p.ident,))
            conn.execute('''INSERT INTO event_merge(source_event_id,target_event_id,merged_by,merged_at,reason,media_ids_json,request_ids_json)
                VALUES(?,'EVT_0001','admin02','2026-11-15','isolated protection fixture','[]','[]')''',(p.ident,))
        before=self.snapshot();result=self.receipt().raise_for_status().json()
        self.assertEqual(result['receipt_status'],'committed_exact')
        self.assertEqual(result['recovery_blockers'],['event_merged']);self.assertEqual(self.snapshot(),before)

    def test_auth_scope_disabled_invalid_and_missing_event_fail_closed(self):
        p=self.pipe;p.create();before=self.snapshot()
        self.assertEqual(self.receipt(headers={}).status_code,401)
        self.assertEqual(self.receipt(headers=p.admin).status_code,401)
        token=p.client.post('/api/nodes',headers=p.admin,json={'node_id':'other-receipt','name':'other'}).raise_for_status().json()['token']
        other={'Authorization':'Bearer '+token}
        self.assertEqual(self.receipt(headers=other).status_code,403)
        p.client.put('/api/processing/workers/other-receipt',headers=p.admin,json={'enabled':True}).raise_for_status()
        self.assertEqual(self.receipt(headers=other).status_code,403)
        self.assertEqual(self.receipt({**p.close_body,'session_id':uuid4().hex}).status_code,403)
        self.assertEqual(self.receipt(event_id='EVT_'+uuid4().hex).status_code,404)
        self.assertEqual(self.receipt({**p.close_body,'sequence':0}).status_code,422)
        self.assertEqual(self.receipt({**p.close_body,'client_proof':True}).status_code,422)
        with patch.dict(os.environ,{'PPE_CONTINUOUS_EVENTS_ENABLED':'false'}):
            self.assertEqual(self.receipt().status_code,503)
        self.assertEqual(self.snapshot(),before)
