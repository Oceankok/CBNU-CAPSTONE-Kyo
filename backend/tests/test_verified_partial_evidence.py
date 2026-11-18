"""HTTP and storage contracts for interrupted verified capture."""
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.db.event_repository import get_connection
from backend.processing import lifecycle
from backend.tests import test_continuous_events as fixtures


class VerifiedPartialEvidenceTests(unittest.TestCase):
    setUp = fixtures.ContinuousEventTests.setUp
    ts = fixtures.ContinuousEventTests.ts
    start = fixtures.ContinuousEventTests.start
    progress = fixtures.ContinuousEventTests.progress
    capture_report = fixtures.ContinuousEventTests.capture_report

    def manifest(self, camera='CAM_001', sequence=0, start=0, end=3, headers=None):
        return self.client.post(self.base+'/'+self.ident+'/segments', headers=headers or self.nodes[0], json={
            'session_id':self.session, 'camera_id':camera, 'sequence':sequence,
            'capture_start_at':self.ts(start), 'capture_end_at':self.ts(end)})

    def partial(self, camera='CAM_001', **changes):
        body=dict(state='incomplete', reason='missing_preroll',
            captured_ranges=[{'start_at':self.ts(0),'end_at':self.ts(3)}],
            missing_ranges=[{'start_at':self.ts(-2),'end_at':self.ts(0),'reason':'missing_preroll'}])
        return self.capture_report(camera, **{**body, **changes})

    def test_scoped_segments_require_own_durable_ranges_and_safe_sequence(self):
        self.start().raise_for_status()
        self.assertEqual(self.manifest().status_code,422)
        self.partial().raise_for_status()
        self.assertEqual(self.manifest(headers=self.nodes[1]).status_code,403)
        self.assertEqual(self.manifest('CAM_002').status_code,422)
        self.assertEqual(self.manifest(start=-1).status_code,422)
        self.assertEqual(self.manifest(end=4).status_code,422)
        first=self.manifest(end=1); first.raise_for_status()
        self.assertEqual(self.manifest(end=1).status_code,200)
        self.assertEqual(self.manifest(sequence=1,start=0,end=2).status_code,409)
        self.manifest(sequence=1,start=1,end=3).raise_for_status()
        self.assertEqual(self.partial(sequence=2, state='complete').status_code,409)
        self.assertEqual(self.progress(kind='close',end=3,complete=True,close_reason='ppe_present',final_segment_count=2).status_code,409)

    def test_interrupted_review_needs_public_redacted_media_and_auto_stays_blocked(self):
        self.start().raise_for_status()
        self.partial('CAM_002').raise_for_status()
        segment=self.manifest('CAM_002').json()['segments'][0]
        self.progress(kind='close',end=3,complete=False,close_reason='source_gap',final_segment_count=0).raise_for_status()
        with get_connection() as c:
            lifecycle.require_finalizable(c,self.ident,manual_review=True)
            with self.assertRaisesRegex(ValueError,'not_closed'):
                lifecycle.require_finalizable(c,self.ident)
        review={'review_result':'confirmed','review_reason_code':'other','review_comment':'Reviewed reference'}
        self.assertNotEqual(self.client.post('/api/events/'+self.ident+'/review',headers=self.admin,json=review).status_code,200)
        folder=Path(self.enterContext(TemporaryDirectory())); file=folder/'video.mp4'; file.write_bytes(b'test')
        self.enterContext(patch('backend.media.paths.owned_path',return_value=file))
        self.enterContext(patch('backend.media.service.owned_path',return_value=file))
        with get_connection() as c:
            c.execute("INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,created_at,request_id,capture_start_at,capture_end_at) VALUES(?,?,?,'video','reference',?,'ready','unprocessed',CURRENT_TIMESTAMP,?,?,?)",
                (str(uuid4()),self.ident,'CAM_002','storage/test.mp4',segment['request_id'],self.ts(0),self.ts(3)))
            c.execute("UPDATE event_clip_request SET status='received' WHERE request_id=?",(segment['request_id'],))
        self.assertNotEqual(self.client.post('/api/events/'+self.ident+'/review',headers=self.admin,json=review).status_code,200)
        with get_connection() as c:
            c.execute("UPDATE event_media SET redaction_status='complete' WHERE event_id=?",(self.ident,))
        response=self.client.post('/api/events/'+self.ident+'/review',headers=self.admin,json=review)
        self.assertEqual(response.status_code,200,response.text)

    def test_healthy_camera_collecting_then_complete_with_partial_reference(self):
        self.start().raise_for_status()
        self.capture_report('CAM_001').raise_for_status()
        self.assertEqual(self.manifest('CAM_001',start=-2,end=5).status_code,422)
        self.partial('CAM_002').raise_for_status()
        self.assertEqual(self.manifest('CAM_001',start=-2,end=5).status_code,200)
        self.assertEqual(self.manifest('CAM_002',start=0,end=3).status_code,200)
        with patch.dict('os.environ',{'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS':'0.1'}):
            self.assertEqual(self.capture_report('CAM_001',sequence=2,state='complete').status_code,409)
            self.assertEqual(self.progress(kind='close',end=3,complete=True,close_reason='ppe_present',final_segment_count=1).status_code,409)
            self.progress(kind='close',end=3,complete=False,close_reason='source_gap',final_segment_count=1).raise_for_status()
            self.capture_report('CAM_001',sequence=2,state='complete').raise_for_status()
        with get_connection() as c:
            result=lifecycle.public(c,self.ident)
            self.assertEqual({v['camera_id']:v['capture_report']['state'] for v in result['cameras']},
                             {'CAM_001':'complete','CAM_002':'incomplete'})
            with self.assertRaisesRegex(ValueError,'not_closed'):
                lifecycle.require_finalizable(c,self.ident)

    def test_all_cameras_complete_on_interrupted_event_still_need_redacted_media(self):
        self.start().raise_for_status()
        body={'session_id':self.session,'sequence':0,'capture_start_at':self.ts(-2),'capture_end_at':self.ts(5)}
        parts=self.client.post(self.base+'/'+self.ident+'/segments',headers=self.nodes[0],json=body).json()['segments']
        with patch.dict('os.environ',{'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS':'0.1'}):
            self.assertEqual(self.capture_report('CAM_001',state='complete').status_code,409)
            self.progress(kind='close',end=3,complete=False,close_reason='source_gap',final_segment_count=1).raise_for_status()
            for camera in ('CAM_001','CAM_002'):
                self.capture_report(camera,state='complete').raise_for_status()
        review={'review_result':'confirmed','review_reason_code':'other','review_comment':'Reviewed surviving video'}
        self.assertNotEqual(self.client.post('/api/events/'+self.ident+'/review',headers=self.admin,json=review).status_code,200)
        folder=Path(self.enterContext(TemporaryDirectory())); file=folder/'video.mp4'; file.write_bytes(b'test')
        self.enterContext(patch('backend.media.paths.owned_path',return_value=file))
        self.enterContext(patch('backend.media.service.owned_path',return_value=file))
        part=next(p for p in parts if p['camera_id']=='CAM_001')
        with get_connection() as c:
            with self.assertRaisesRegex(ValueError,'not_closed'):
                lifecycle.require_finalizable(c,self.ident)
            c.execute("INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,created_at,request_id,capture_start_at,capture_end_at) VALUES(?,?,?,'video','clip',?,'ready','complete',CURRENT_TIMESTAMP,?,?,?)",
                (str(uuid4()),self.ident,'CAM_001','storage/test.mp4',part['request_id'],self.ts(-2),self.ts(5)))
            c.execute("UPDATE event_clip_request SET status='received' WHERE request_id=?",(part['request_id'],))
        response=self.client.post('/api/events/'+self.ident+'/review',headers=self.admin,json=review)
        self.assertEqual(response.status_code,200,response.text)

    def test_report_preserves_observed_range_after_source_becomes_unavailable(self):
        self.start().raise_for_status()
        self.capture_report('CAM_001').raise_for_status()
        response=self.capture_report('CAM_001',sequence=2,state='incomplete',reason='source_segment_unavailable',
            missing_ranges=[{'start_at':self.ts(0),'end_at':self.ts(3),'reason':'source_segment_unavailable'}])
        response.raise_for_status()
        report=next(c['capture_report'] for c in response.json()['cameras'] if c['camera_id']=='CAM_001')
        self.assertEqual(report['state'],'incomplete')
        self.assertEqual(len(report['captured_ranges']),1)
        self.assertEqual(report['missing_ranges'][0]['reason'],'source_segment_unavailable')

    def test_forged_later_complete_cannot_enable_auto(self):
        self.start().raise_for_status(); self.partial().raise_for_status(); self.manifest().raise_for_status()
        with get_connection() as c:
            stored=c.execute('SELECT payload FROM processing_camera_capture WHERE event_id=?',(self.ident,)).fetchone()[0]
            body=json.loads(stored); body['sequence']=2; body['state']='complete'
            c.execute('INSERT INTO processing_camera_capture(event_id,camera_id,sequence,payload,created_at) VALUES(?,?,?,?,0)',
                      (self.ident,'CAM_001',2,json.dumps(body)))
            c.execute('INSERT INTO processing_camera_capture(event_id,camera_id,sequence,payload,created_at) VALUES(?,?,?,?,0)',
                      (self.ident,'CAM_002',1,json.dumps({**body,'sequence':1})))
            c.execute("UPDATE processing_event_lifecycle SET phase='closed' WHERE event_id=?",(self.ident,))
            with self.assertRaisesRegex(ValueError,'incomplete'):
                lifecycle.require_finalizable(c,self.ident)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
    def test_worker_partial_capture_against_real_api(self):
        self.worker_capture_against_real_api(mixed=False)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
    def test_worker_mixed_capture_against_real_api(self):
        self.worker_capture_against_real_api(mixed=True)

    def worker_capture_against_real_api(self, *, mixed):
        import httpx
        import numpy as np
        from inference_worker.capture_outbox import CaptureOutbox
        from inference_worker.lifecycle_journal import LifecycleJournal
        from inference_worker.multicamera_capture import CameraClock, MultiCameraCapture
        from inference_worker.continuous_runtime import ContinuousFrameRuntime
        from inference_worker.continuous_dispatcher import ContinuousDispatcher
        from inference_worker.test_continuous_dispatcher import FakeTransport

        self.enterContext(patch.dict('os.environ',{'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS':'0.1','PPE_PROCESSING_MODE':'remote'}))
        root=Path(self.enterContext(TemporaryDirectory())); now=[self.t.timestamp()]
        self.enterContext(patch('backend.media.routes.WORK_ROOT',root/'server_work'))
        self.enterContext(patch('backend.media.service.SOURCE_ROOT',root/'server_sources'))
        self.enterContext(patch('backend.media.paths.SOURCE_ROOT',root/'server_sources'))
        box=CaptureOutbox(root/'journal',private_root=root/'raw',max_db_bytes=2000000,
            max_records=300,max_body_bytes=20000,source_ttl_seconds=100,clock=lambda:now[0])
        self.addCleanup(box.close)
        testcase=self
        class CameraTransport(FakeTransport):
            def upload(self, plan):
                response=testcase.client.post('/api/processing/events/'+plan.event_id+'/clip',
                    params={'request_id':plan.request_id,'camera_id':plan.camera_id}, headers=testcase.nodes[0],
                    content=(root/'raw'/plan.relative_path).read_bytes())
                response.raise_for_status()
                receipt=testcase.client.get(testcase.base+'/'+plan.event_id+'/receipts/'+plan.request_id,
                    params={'session_id':box.session},headers=testcase.nodes[0])
                receipt.raise_for_status()
                self.uploads.append(plan.request_id)
                self.receipts[plan.request_id]=receipt.json()
        transport=CameraTransport()
        requests=[]
        def forward(request):
            response=self.client.request(request.method,request.url.path,content=request.read(),headers={**self.nodes[0], 'content-type':'application/json'})
            requests.append((request.url.path,response.status_code,response.text))
            return httpx.Response(response.status_code,content=response.content,headers={'content-type':'application/json'})
        transport.client=httpx.Client(base_url='https://test',transport=httpx.MockTransport(forward))
        self.addCleanup(transport.client.close)
        dispatcher=ContinuousDispatcher(box,transport,enabled=True,max_delivery_records=30,max_delivery_db_bytes=2000000)
        self.addCleanup(dispatcher.close)
        capture=MultiCameraCapture(root/'raw',cameras={c:CameraClock(0,0,True) for c in ('CAM_001','CAM_002')},
            max_clock_error=0,pre_seconds=2,post_seconds=2,discovery_delay_limit=3.5,prebuffer_seconds=6,
            prebuffer_bytes=100000,max_buffer_frames=50,max_events=2,global_spool_bytes=4000000,
            spool_config=dict(max_bytes=1000000,max_segments=20,max_age_seconds=100,max_attempts=3,clock=lambda:now[0]),
            writer_config=dict(segment_seconds=2,output_fps=2,max_frame_pixels=256,max_input_gap_seconds=.6,encoder_timeout_seconds=5))
        journal=LifecycleJournal(box,camera_id='CAM_001',camera_snapshot=['CAM_001','CAM_002'],model_version='test',
            missing_grace_seconds=.6,threshold_seconds=3,max_sample_gap_seconds=.6,progress_emit_interval_seconds=1)
        runtime=ContinuousFrameRuntime(journal=journal,capture=capture,dispatcher=dispatcher,metadata_steps_per_pump=30)
        self.addCleanup(runtime.stop,'test_stop')
        frame=np.zeros((16,16,3),np.uint8); starts=[]
        if mixed:
            for offset in (-2,-1.5,-1,-.5):
                runtime.frame('CAM_002',self.t.timestamp()+offset,frame)
                runtime.frame('CAM_001',self.t.timestamp()+offset,frame,assessments=[])
        for step in range(18):
            now[0]=self.t.timestamp()+step/2
            if not (mixed and step==11):
                runtime.frame('CAM_002',now[0] if mixed else now[0]-.273824,frame)
            starts += [r for r in runtime.frame('CAM_001',now[0],frame,assessments=[{'tracking_id':'t',
                'ppe':{'helmet':{'status':'suspected_no_helmet' if step<12 else 'helmet'}}}]) if r['kind']=='start']
            runtime.pump(now=now[0])
        for _ in range(20):runtime.pump(now=now[0])
        self.assertEqual(len(starts),1)
        self.assertTrue(all(code<300 for _,code,_ in requests),requests)
        event=starts[0]['event_id']
        with get_connection() as c:
            result=lifecycle.public(c,event)
            self.assertEqual(result['lifecycle']['phase'],'interrupted')
            self.assertEqual({s['camera_id'] for s in result['segments']},{'CAM_001','CAM_002'})
            reports={camera['camera_id']:camera['capture_report'] for camera in result['cameras']}
            if mixed:
                self.assertEqual(reports['CAM_001']['state'],'complete')
                self.assertEqual(reports['CAM_001']['sequence'],2)
                self.assertEqual(reports['CAM_002']['state'],'incomplete')
                self.assertTrue(reports['CAM_002']['missing_ranges'])
                self.assertFalse(reports['CAM_001']['missing_ranges'])
            else:
                self.assertTrue(all(report['state']=='incomplete' for report in reports.values()))
        plans=[json.loads(r['plan']) for r in dispatcher.db.execute('SELECT plan FROM uploads WHERE event=?',(event,))]
        self.assertEqual({plan['camera_id'] for plan in plans},{'CAM_001','CAM_002'})
        self.assertTrue(all(row['state']=='accepted' for row in dispatcher.status()))
        with get_connection() as c:
            media=c.execute('SELECT camera_id,status,redaction_status,source_path,storage_path FROM event_media WHERE event_id=?',(event,)).fetchall()
            self.assertEqual({row['camera_id'] for row in media},{'CAM_001','CAM_002'})
            self.assertTrue(all(row['status']=='processing' and row['redaction_status']=='unprocessed'
                                and row['source_path'] and not row['storage_path']
                                and (root/'server_sources'/row['source_path']).is_file() for row in media))
