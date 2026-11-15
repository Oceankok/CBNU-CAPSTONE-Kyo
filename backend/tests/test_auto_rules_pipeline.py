"""Producer payloads -> actual ASGI/PG/media completion -> OFF/shadow.

No detector, camera, calibration or face model is claimed. Encoded synthetic
video is uploaded and decoded by real APIs; redaction output is a test provider.
The httpx adapter bridges transport I/O to TestClient (no TCP server).
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4
import hashlib
import json
import os

import cv2
import httpx
import numpy as np

from backend.db.event_repository import get_connection
from backend.tests import test_processing as processing_fixture
from inference_worker.event_lifecycle import EventLifecycle
from inference_worker.continuous_payloads import start_payload, update_payload, segment_payload
from inference_worker.continuous_transport import PrivateClipTransport, UploadPlan


class AutoRulesPipelineTests(TestCase):
    def setUp(self):
        processing_fixture.ProcessingTests.setUp(self)
        self.enterContext(patch.dict(os.environ, {
            'PPE_CONTINUOUS_EVENTS_ENABLED':'true',
            'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS':'0'}))
        self.group=self.client.post('/api/camera-groups',headers=self.admin,
                         json={'name':'pipeline-pair','camera_ids':['CAM_001','CAM_002']}).raise_for_status().json()
        self.session=uuid4().hex
        self.epoch=(datetime.now(timezone.utc)-timedelta(seconds=60)).timestamp()
        engine=EventLifecycle('CAM_001',missing_grace_seconds=1,threshold_seconds=3,
                              max_sample_gap_seconds=.5)
        absent=[{'tracking_id':'fixture-track','policy_scope_key':'fixture-policy',
                 'ppe_type':'helmet','status':'suspected_no_helmet','verified_ppe_confidence':None}]
        transitions=[]
        for i in range(17):transitions.extend(engine.update(absent,self.epoch+i/5))
        transitions.extend(engine.update([{**absent[0],'status':'helmet'}],self.epoch+3.4))
        self.start=next(row for row in transitions if row['kind']=='start')
        self.progress=next(row for row in transitions if row['kind']=='progress')
        self.close=next(row for row in transitions if row['kind']=='close')
        self.ident=self.start['event_id'];self.base='/api/processing/continuous-events'
        self.source=Path(self.temp.name)/'source.mp4'
        output=cv2.VideoWriter(str(self.source),cv2.VideoWriter_fourcc(*'mp4v'),5,(32,32))
        self.assertTrue(output.isOpened())
        for _ in range(36):output.write(np.zeros((32,32,3),dtype=np.uint8))
        output.release()
        self.data=self.source.read_bytes()
        self.plans={};self.media={}
        self.transport=PrivateClipTransport(server_url='http://localhost',
            token=self.node['Authorization'][7:],private_root=self.source.parent,
            max_upload_bytes=1_000_000,timeout_seconds=10,max_response_bytes=100_000,total_seconds=30,
            transport=httpx.MockTransport(self.handle))
        self.addCleanup(self.transport.close)

    def handle(self, request):
        response=self.client.request(request.method,request.url.raw_path.decode(),
                                      headers=dict(request.headers),content=request.read())
        return httpx.Response(response.status_code,headers=dict(response.headers),content=response.content)

    def settings(self, mode='shadow', version=0):
        return self.client.put('/api/auto-decisions/settings',headers=self.admin,json={
            'expected_version':version,'reason':'synthetic continuous pipeline',
            'mode':mode,'decision_method':'continuous_rules_v1',
            'rule_criteria':{'minimum_observed_seconds':3.0,'minimum_frame_samples':16},
            'model_versions':['fixture-model'],'ppe_types':['helmet'],'camera_ids':['CAM_001']}).raise_for_status()

    def create(self, mode='shadow', close_reason=None, complete=True, leave_open=False):
        self.settings(mode)
        self.start_body=start_payload(self.start,session_id=self.session,model_version='fixture-model')
        self.client.post(self.base,headers=self.node,json=self.start_body).raise_for_status()
        progress=update_payload(self.progress,session_id=self.session,sequence=1)
        self.client.post(self.base+'/'+self.ident+'/updates',headers=self.node,json=progress).raise_for_status()
        segment=SimpleNamespace(capture_start=self.epoch-2,capture_end=self.epoch+5.2)
        body=segment_payload(segment,session_id=self.session,event_sequence=0)
        self.segments=self.client.post(self.base+'/'+self.ident+'/segments',headers=self.node,json=body).raise_for_status().json()['segments']
        close={**self.close,'close_reason':close_reason or self.close['close_reason']}
        self.close_body=update_payload(close,session_id=self.session,sequence=2,complete=complete,final_segment_count=1)
        if not leave_open:
            self.client.post(self.base+'/'+self.ident+'/updates',headers=self.node,json=self.close_body).raise_for_status()
        for row in self.segments:
            self.plans[row['camera_id']]=UploadPlan(self.ident,row['request_id'],row['camera_id'],
                self.source.name,len(self.data),hashlib.sha256(self.data).hexdigest(),
                self.epoch-2,self.epoch+5.2,self.epoch-2,self.epoch+5.2,True)

    def reports(self, *, state='complete'):
        for camera in ('CAM_001','CAM_002'):
            body={'session_id':self.session,'sequence':1,'state':state,
                  'captured_ranges':[{'start_at':self.ts(-2),'end_at':self.ts(5.2)}],
                  'missing_ranges':[],'time_basis':'shared_monotonic',
                  'clock_offset_seconds':0,'clock_uncertainty_seconds':0}
            if state=='incomplete':body['reason']='synthetic_collector_crash'
            self.report_body=body
            self.client.post(self.base+'/'+self.ident+'/cameras/'+camera+'/capture-reports',
                             headers=self.node,json=body).raise_for_status()

    def ts(self,seconds):
        return datetime.fromtimestamp(self.epoch+seconds,tz=timezone.utc).isoformat(timespec='microseconds')

    def upload(self,camera):
        receipt=self.transport.upload(self.plans[camera])
        self.media[camera]=receipt['media_id']
        return receipt

    def process(self):
        response=self.client.post('/api/processing/claim',headers=self.node).raise_for_status()
        job=response.json()['job'];self.assertIsNotNone(job)
        headers={**self.node,'X-Processing-Lease':job['lease_token']}
        source=self.client.get(job['source_url'],headers=headers).raise_for_status().content
        self.assertEqual(source,self.data)
        meta={'faces_detected':0,'processed_frames':36,'redaction_mode':'scrfd',
              'inference_backend':'synthetic-output-provider','processing_seconds':0.01}
        result_headers={**headers,'Content-Type':'video/mp4','X-Processing-Metadata':json.dumps(meta)}
        url='/api/processing/jobs/'+job['job_id']+'/result'
        self.client.post(url,headers=result_headers,content=self.data).raise_for_status()
        # Exact publication retry must not append another unchanged snapshot.
        history=self.history()
        self.client.post(url,headers=result_headers,content=self.data).raise_for_status()
        self.assertEqual(self.history(),history)
        self.client.post('/api/processing/jobs/'+job['job_id']+'/cleanup',headers=self.node).raise_for_status()

    def history(self):
        return self.client.get('/api/auto-decisions/shadow',headers=self.admin,
            params={'event_id':self.ident}).raise_for_status().json()['items']

    def preview(self):
        return self.client.post('/api/auto-decisions/settings/preview',headers=self.admin,json={
            'mode':'shadow','decision_method':'continuous_rules_v1',
            'rule_criteria':{'minimum_observed_seconds':3.0,'minimum_frame_samples':16},
            'model_versions':['fixture-model'],'ppe_types':['helmet'],'camera_ids':['CAM_001'],
            'event_ids':[self.ident]}).raise_for_status().json()['items'][0]

    def no_reviews(self):
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM event_review WHERE event_id=?',(self.ident,)).fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT review_only FROM processing_review_only_event WHERE event_id=?',(self.ident,)).fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT event_status FROM candidate_event WHERE event_id=?',(self.ident,)).fetchone()[0],'pending')

    def test_report_first_real_upload_and_decoded_completion_shadow(self):
        self.create();self.reports()
        self.assertFalse(self.preview()['rule_evaluation']['eligible'])
        for camera in ('CAM_001','CAM_002'):
            self.upload(camera);self.process()
        self.assertTrue(self.history()[0]['result']['rule_evaluation']['eligible'])
        self.assertIsNone(self.preview()['observed_confidence']);self.no_reviews()

    def test_media_first_last_report_shadow_and_exact_retries(self):
        self.create()
        for camera in ('CAM_001','CAM_002'):self.upload(camera);self.process()
        self.assertFalse(self.history()[0]['result']['rule_evaluation']['eligible'])
        self.reports();history=self.history()
        self.assertTrue(history[0]['result']['rule_evaluation']['eligible'])
        self.client.post(self.base,headers=self.node,json=self.start_body).raise_for_status()
        self.client.post(self.base+'/'+self.ident+'/updates',headers=self.node,json=self.close_body).raise_for_status()
        self.client.post(self.base+'/'+self.ident+'/cameras/CAM_002/capture-reports',headers=self.node,json=self.report_body).raise_for_status()
        ref=next(row for row in self.segments if row['camera_id']=='CAM_002')
        receipt=self.transport.recover_receipt(self.plans['CAM_002'],session_id=self.session,
                                              role=ref['role'],expected_media_id=self.media['CAM_002'])
        self.assertEqual(receipt['media_id'],self.media['CAM_002'])
        self.assertEqual(self.history(),history);self.no_reviews()

    def test_off_never_writes_review_or_shadow_even_complete(self):
        self.create('off');self.reports()
        for camera in ('CAM_001','CAM_002'):self.upload(camera);self.process()
        self.assertEqual(self.history(),[]);self.no_reviews()

    def test_pending_unavailable_and_incomplete_crash_never_eligible(self):
        self.create();self.upload('CAM_001');self.process()
        self.assertIn('evidence_incomplete',self.preview()['blocking_reasons'])
        ref=next(row for row in self.segments if row['camera_id']=='CAM_002')
        self.client.put('/api/processing/events/'+self.ident+'/clip-requests/'+ref['request_id'],
            headers=self.node,json={'status':'unavailable','error_code':'synthetic_collector_crash'}).raise_for_status()
        self.reports(state='incomplete')
        self.assertIn('evidence_incomplete',self.preview()['blocking_reasons'])
        self.assertFalse(self.preview()['rule_evaluation']['eligible']);self.no_reviews()

    def test_incomplete_terminal_report_refreshes_rule_shadow(self):
        self.create()
        for camera in ('CAM_001','CAM_002'):self.upload(camera);self.process()
        old=self.history()
        self.reports(state='incomplete')
        fresh=self.history()
        self.assertGreater(len(fresh),len(old))
        self.assertGreater(fresh[0]['result']['collection']['evidence_revision'],
                           old[0]['result']['collection']['evidence_revision'])
        self.assertIn('continuous_evidence_incomplete',fresh[0]['result']['blocking_reasons'])
        self.assertFalse(fresh[0]['result']['rule_evaluation']['eligible']);self.no_reviews()

    def test_primary_only_server_snapshot_and_shadow(self):
        self.client.delete('/api/camera-groups/'+self.group['group_id'],headers=self.admin,
                           params={'expected_version':self.group['version']}).raise_for_status()
        self.create();self.assertEqual(len(self.segments),1)
        self.upload('CAM_001');self.process()
        self.reports_primary_only()
        self.assertTrue(self.history()[0]['result']['rule_evaluation']['eligible']);self.no_reviews()

    def reports_primary_only(self):
        body={'session_id':self.session,'sequence':1,'state':'complete',
              'captured_ranges':[{'start_at':self.ts(-2),'end_at':self.ts(5.2)}],
              'missing_ranges':[],'time_basis':'shared_monotonic',
              'clock_offset_seconds':0,'clock_uncertainty_seconds':0}
        self.client.post(self.base+'/'+self.ident+'/cameras/CAM_001/capture-reports',
                         headers=self.node,json=body).raise_for_status()

    def test_original_session_remote_read_then_incomplete_close(self):
        self.create(leave_open=True)
        self.upload('CAM_001');self.process()
        remote=self.client.get(self.base+'/'+self.ident,params={'session_id':self.session},headers=self.node).raise_for_status().json()
        self.assertEqual(remote['lifecycle']['phase'],'open')
        body={**self.close_body,'sequence':remote['lifecycle']['revision']+1,
              'complete':False,'close_reason':'process_restart'}
        self.client.post(self.base+'/'+self.ident+'/updates',headers=self.node,json=body).raise_for_status()
        self.client.post(self.base+'/'+self.ident+'/updates',headers=self.node,json=body).raise_for_status()
        self.reports(state='incomplete')
        diagnostic=self.preview()
        self.assertEqual(diagnostic['collection']['phase'],'interrupted')
        self.assertIn('continuous_event_not_closed',diagnostic['blocking_reasons']);self.no_reviews()

    def test_two_primaries_same_node_independent_sessions_keep_snapshot(self):
        self.create(leave_open=True)
        other=uuid4().hex;event='EVT_'+uuid4().hex
        body={**self.start_body,'event_id':event,'camera_id':'CAM_002','session_id':other,'tracking_id':'other-primary-track'}
        response=self.client.post(self.base,headers=self.node,json=body).raise_for_status().json()
        self.assertEqual({row['camera_id'] for row in response['cameras']},{'CAM_001','CAM_002'})
        self.assertEqual(response['lifecycle']['phase'],'open')
        self.assertEqual(self.client.get(self.base+'/'+event,params={'session_id':self.session},headers=self.node).status_code,403)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM candidate_event WHERE event_id IN (?,?)',(self.ident,event)).fetchone()[0],2)
        self.assertFalse(self.preview()['rule_evaluation']['eligible']);self.no_reviews()

    def test_incomplete_report_commit_survives_shadow_failure_and_retry(self):
        self.create()
        before=self.history()
        from backend.auto_decision import shadow
        original=shadow.record_policy_shadow
        def fail(*args):
            original(*args)
            raise RuntimeError('synthetic post-commit diagnostic failure')
        with patch.object(shadow,'record_policy_shadow',side_effect=fail),self.assertLogs('backend.processing.capture',level='WARNING'):
            self.reports(state='incomplete')
        self.assertEqual(self.history(),before)
        remote=self.client.get(self.base+'/'+self.ident,params={'session_id':self.session},headers=self.node).raise_for_status().json()
        self.assertTrue(all(row['capture_report']['state']=='incomplete' for row in remote['cameras']))
        self.reports(state='incomplete')
        after=self.history();self.assertGreater(len(after),len(before))
        self.reports(state='incomplete');self.assertEqual(self.history(),after);self.no_reviews()

    def test_manual_correction_and_history_survive_report_retry(self):
        self.create();self.reports()
        for camera in ('CAM_001','CAM_002'):self.upload(camera);self.process()
        self.client.post('/api/events/'+self.ident+'/review',headers=self.admin,
            json={'review_result':'confirmed','review_reason_code':'other',
                  'review_comment':'manual fixture confirmation','second_review_needed':True}).raise_for_status()
        self.client.put('/api/events/'+self.ident+'/review',headers=self.admin,
            json={'review_result':'false_positive','review_reason_code':'other','review_comment':'manual correction'}).raise_for_status()
        self.client.post(self.base+'/'+self.ident+'/cameras/CAM_002/capture-reports',headers=self.node,json=self.report_body).raise_for_status()
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT review_result FROM event_review WHERE event_id=?',(self.ident,)).fetchone()[0],'false_positive')
            self.assertEqual(conn.execute('SELECT review_result FROM event_review_history WHERE event_id=?',(self.ident,)).fetchone()[0],'confirmed')
        self.assertIn('already_reviewed_or_not_pending',self.preview()['blocking_reasons'])

    def test_loopback_http_off_and_shadow_workflow(self):
        """Real TCP/HTTP, same API + PG; synthetic source/redaction provider."""
        import socket
        from threading import Thread
        import time
        import uvicorn
        from backend.api.main import app
        listener=socket.socket();listener.bind(('127.0.0.1',0))
        port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,
                                           lifespan='off',log_level='error'))
        thread=Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True)
        thread.start()
        try:
            deadline=time.monotonic()+10
            while not server.started:
                if not thread.is_alive() or time.monotonic()>deadline:self.fail('Local API did not start')
                time.sleep(.01)
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=10) as client:
                self.client=client
                self.transport.close()
                self.transport=PrivateClipTransport(server_url=f'http://127.0.0.1:{port}',
                    token=self.node['Authorization'][7:],private_root=self.source.parent,
                    max_upload_bytes=1_000_000,timeout_seconds=10,max_response_bytes=100_000,total_seconds=30)
                self.addCleanup(self.transport.close)
                self.create('off');self.assertEqual(self.history(),[])
                self.settings('shadow',version=1)
                self.reports()
                for camera in ('CAM_001','CAM_002'):self.upload(camera);self.process()
                self.assertTrue(self.history()[0]['result']['rule_evaluation']['eligible'])
                self.assertFalse(self.history()[0]['result']['automatic_confirmation_allowed']);self.no_reviews()
        finally:
            server.should_exit=True;thread.join(timeout=10);listener.close()
            self.assertFalse(thread.is_alive(),'Local API thread leaked')

    def test_trailing_unknown_survives_wire_and_complete_media_abstains(self):
        self.create(close_reason='ppe_present_after_unknown');self.reports()
        for camera in ('CAM_001','CAM_002'):self.upload(camera);self.process()
        self.assertIn('abnormal_event_close_requires_review',self.history()[0]['result']['blocking_reasons'])
        self.no_reviews()

    def test_human_hold_survives_late_completion(self):
        self.create()
        # Hold requires finalizable continuous evidence in the current contract.
        for camera in ('CAM_001','CAM_002'):self.upload(camera);self.process()
        self.reports()
        self.client.post('/api/events/'+self.ident+'/review',headers=self.admin,
            json={'review_result':'hold','review_reason_code':'unclear'}).raise_for_status()
        self.client.post(self.base+'/'+self.ident+'/cameras/CAM_002/capture-reports',headers=self.node,json=self.report_body).raise_for_status()
        self.assertIn('already_reviewed_or_not_pending',self.preview()['blocking_reasons'])
        with get_connection() as conn:
            row=conn.execute('SELECT reviewer_id,review_result FROM event_review WHERE event_id=?',(self.ident,)).fetchone()
            self.assertEqual(tuple(row.values()) if isinstance(row,dict) else tuple(row),('admin02','hold'))
