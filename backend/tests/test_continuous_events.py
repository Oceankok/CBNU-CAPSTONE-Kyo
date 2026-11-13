"""No cameras/GPU: opt-in continuous metadata and segment contracts."""
from datetime import datetime,timedelta,timezone
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.tests.support import isolated_api,login
from backend.db.event_repository import get_connection
from backend.processing import lifecycle

class ContinuousEventTests(unittest.TestCase):
    def setUp(self):
        self.env=self.enterContext(patch.dict(os.environ,{'PPE_CONTINUOUS_EVENTS_ENABLED':'true'}))
        self.client=self.enterContext(isolated_api())
        self.admin=login(self.client)
        self.nodes=[]
        for ident in ('producer','other'):
            token=self.client.post('/api/nodes',headers=self.admin,json={'node_id':ident,'name':ident}).json()['token']
            headers={'Authorization':'Bearer '+token}
            self.client.put('/api/processing/workers/'+ident,headers=self.admin,json={'enabled':True}).raise_for_status()
            self.nodes.append(headers)
        self.client.post('/api/camera-groups',headers=self.admin,json={'name':'pairs','camera_ids':['CAM_001','CAM_002']}).raise_for_status()
        self.t=datetime(2026,11,12,0,0,tzinfo=timezone.utc)
        self.ident='EVT_'+uuid4().hex
        self.session=uuid4().hex
        self.body=dict(event_id=self.ident,camera_id='CAM_001',tracking_id='track1',ppe_type='helmet',timestamp_start=self.ts(0),timestamp_end=self.ts(3),confidence=None,frame_sample_count=46,model_version='test',review_only=True,session_id=self.session,policy_key='p1',observed_seconds=3,gap_seconds=0,gap_count=0)
        self.base='/api/processing/continuous-events'
    def ts(self,n):
        return (self.t+timedelta(seconds=n)).isoformat()
    def start(self,**changes):
        return self.client.post(self.base,headers=self.nodes[0],json={**self.body,**changes})
    def progress(self,sequence=1,end=10,**changes):
        body=dict(session_id=self.session,sequence=sequence,kind='progress',timestamp_end=self.ts(end),frame_sample_count=int(end*15)+1,observed_seconds=end,gap_seconds=0,gap_count=0)
        return self.client.post(self.base+'/'+self.ident+'/updates',headers=self.nodes[0],json={**body,**changes})
    def segment(self,sequence=0,start=-2,end=5,headers=None):
        return self.client.post(self.base+'/'+self.ident+'/segments',headers=headers or self.nodes[0],json=dict(session_id=self.session,sequence=sequence,capture_start_at=self.ts(start),capture_end_at=self.ts(end)))
    def test_opt_in_permissions_and_start_idempotency(self):
        with patch.dict(os.environ,{'PPE_CONTINUOUS_EVENTS_ENABLED':'false'}):
            self.assertEqual(self.start().status_code,503)
        self.assertEqual(self.client.post(self.base,json=self.body).status_code,401)
        a=self.start();self.assertEqual(a.status_code,201,a.text)
        self.assertEqual(self.start().status_code,200)
        self.assertEqual(self.start(policy_key='changed').status_code,409)
        self.assertEqual(self.client.post(self.base,headers=self.nodes[1],json=self.body).status_code,403)
        with get_connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM candidate_event WHERE event_id=?',(self.ident,)).fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM event_clip_request WHERE event_id=?',(self.ident,)).fetchone()[0],0)
    def test_progress_order_gaps_and_closed_immutability(self):
        self.start().raise_for_status()
        self.assertEqual(self.progress(sequence=2).status_code,409)
        self.assertEqual(self.progress().status_code,200)
        self.assertEqual(self.progress().status_code,200)
        self.assertEqual(self.progress(end=11).status_code,409)
        self.assertEqual(self.progress(sequence=2,end=9).status_code,422)
        seg=self.segment(end=12);self.assertEqual(seg.status_code,200,seg.text)
        self.assertEqual(len(seg.json()['segments']),2)
        close=self.progress(sequence=2,end=10,kind='close',close_reason='ppe_present',complete=True,final_segment_count=1)
        self.assertEqual(close.status_code,200,close.text)
        self.assertEqual(close.json()['lifecycle']['phase'],'closed')
        self.assertFalse(close.json()['auto_decision_enabled'])
        self.assertEqual(self.progress(sequence=2,end=10,kind='close',close_reason='ppe_present',complete=True,final_segment_count=1).status_code,200)
        self.assertEqual(self.progress(sequence=3,end=11).status_code,409)
        self.assertEqual(self.segment(sequence=1,start=12,end=13).status_code,409)
    def test_segment_boundaries_owner_reference_and_metadata(self):
        self.start().raise_for_status();self.progress(end=125).raise_for_status()
        a=self.segment(0,-2,58);self.assertEqual(a.status_code,200,a.text)
        self.assertEqual(self.segment(0,-2,58).json()['segments'],a.json()['segments'])
        self.assertEqual(self.segment(0,-2,57).status_code,409)
        self.assertEqual(self.segment(1,59,100).status_code,409)
        self.assertEqual(self.segment(1,58,119).status_code,422)
        self.assertEqual(self.segment(1,58,118,headers=self.nodes[1]).status_code,403)
        b=self.segment(1,58,118);b.raise_for_status();self.segment(2,118,127).raise_for_status()
        legacy=self.client.get('/api/processing/reference-clip-requests',headers=self.nodes[0]);legacy.raise_for_status()
        self.assertFalse(any(r['event_id']==self.ident for r in legacy.json()['items']))
        refs=self.client.get(self.base+'/reference-requests',headers=self.nodes[0]);refs.raise_for_status()
        own=[r for r in refs.json()['items'] if r['event_id']==self.ident]
        self.assertEqual(len(own),3)
        self.assertEqual(own[0]['clip_start_at'],lifecycle.stamp(self.t-timedelta(seconds=2)))
        from backend.media.service import register_media
        r=a.json()['segments'][0]
        mid=register_media(self.ident,r['camera_id'],'video',r['role'],request_id=r['request_id'])
        with get_connection() as c:
            media=c.execute('SELECT capture_start_at,capture_end_at FROM event_media WHERE media_id=?',(mid,)).fetchone()
            self.assertEqual(media['capture_end_at'],lifecycle.stamp(self.t+timedelta(seconds=58)))
        self.assertEqual(self.progress(sequence=2,end=125,kind='close',complete=True,close_reason='ppe_present',final_segment_count=3).status_code,200)
    def test_close_manifest_and_coverage_required(self):
        self.start().raise_for_status()
        self.assertEqual(self.progress(kind='close',end=3,close_reason='ppe_present',complete=True,final_segment_count=0).status_code,409)
        self.segment(start=1,end=5).raise_for_status()
        self.assertEqual(self.progress(kind='close',end=3,close_reason='ppe_present',complete=True,final_segment_count=1).status_code,409)
    def test_interrupted_close_with_no_media_stays_unreviewable(self):
        self.start().raise_for_status()
        r=self.progress(kind='close',end=3,close_reason='stream_lost',complete=False,final_segment_count=0)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['lifecycle']['phase'],'interrupted')
        event=self.client.get('/api/events/'+self.ident,headers=self.admin).json()
        self.assertFalse(event['event']['review_availability']['can_review'] if 'event' in event else event['review_availability']['can_review'])
    def test_review_merge_open_guard_and_delete_precedence(self):
        self.start().raise_for_status()
        from backend.media.service import validate_review
        from backend.event_merge.service import eligible
        with get_connection() as c:
            with self.assertRaisesRegex(ValueError,'not_closed'):
                validate_review(c,{'event_id':self.ident,'review_result':'hold'})
            with self.assertRaises(Exception):
                eligible(c,self.ident)
            c.execute("UPDATE event_retention SET decision='delete' WHERE event_id=?",(self.ident,))
        self.assertEqual(self.progress().status_code,409)
        self.assertEqual(self.segment().status_code,409)
    def test_finalizable_requires_all_registered_segments_redacted(self):
        self.start().raise_for_status();self.segment().raise_for_status()
        self.progress(kind='close',end=3,close_reason='ppe_present',complete=True,final_segment_count=1).raise_for_status()
        with get_connection() as c:
            with self.assertRaisesRegex(ValueError,'incomplete'):
                lifecycle.require_finalizable(c,self.ident)
    def test_closed_start_retry_never_replays_broadcast(self):
        self.start().raise_for_status();self.segment().raise_for_status()
        self.progress(kind='close',end=3,close_reason='ppe_present',complete=True,final_segment_count=1).raise_for_status()
        with patch('backend.processing.lifecycle.enqueue_event_broadcast') as broadcast:
            retry=self.start();self.assertEqual(retry.status_code,200,retry.text)
            broadcast.assert_not_called()
        from backend.field.service import enqueue_event_broadcast
        self.assertEqual(enqueue_event_broadcast(self.ident)['reason'],'continuous_event_closed')

    def test_other_processor_cannot_report_failure(self):
        self.start().raise_for_status();r=self.segment().json()['segments'][0]
        url='/api/processing/events/'+self.ident+'/clip-requests/'+r['request_id']
        body={'status':'unavailable','error_code':'missing'}
        self.assertEqual(self.client.put(url,headers=self.nodes[1],json=body).status_code,403)
        self.assertEqual(self.client.put(url,headers=self.nodes[0],json=body).status_code,200)

    def test_real_files_and_received_status_required_for_finalization(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        self.start().raise_for_status();segments=self.segment().json()['segments']
        self.progress(kind='close',end=3,close_reason='ppe_present',complete=True,final_segment_count=1).raise_for_status()
        self.complete_cameras()
        with TemporaryDirectory() as folder:
            files={}
            with get_connection() as c:
                for r in segments:
                    mid=str(uuid4());key='storage/'+mid+'.mp4';files[key]=Path(folder)/mid
                    files[key].write_bytes(b'synthetic-redacted-test')
                    c.execute("""INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,created_at,request_id,capture_start_at,capture_end_at)
                        VALUES(?,?,?,'video',?,?,'ready','complete',CURRENT_TIMESTAMP,?,?,?)""",
                        (mid,self.ident,r['camera_id'],r['role'],key,r['request_id'],r['capture_start_at'],r['capture_end_at']))
            with patch('backend.media.paths.owned_path',side_effect=lambda key:files[key]):
                with get_connection() as c:
                    with self.assertRaisesRegex(ValueError,'incomplete'):
                        lifecycle.require_finalizable(c,self.ident)
                    c.execute("UPDATE event_clip_request SET status='received' WHERE event_id=?",(self.ident,))
                    with self.assertRaisesRegex(ValueError,'incomplete'):
                        lifecycle.require_finalizable(c,self.ident)
                    for media in c.execute('SELECT media_id,processing_generation FROM event_media WHERE event_id=?',(self.ident,)).fetchall():
                        c.execute('INSERT INTO processing_segment_validation(media_id,generation,frame_count,fps,duration_seconds,result_sha256) VALUES(?,?,105,15,7,?)',(media['media_id'],media['processing_generation'],'synthetic-proof'))
                    lifecycle.require_finalizable(c,self.ident)
                    next(iter(files.values())).unlink()
                    with self.assertRaisesRegex(ValueError,'incomplete'):
                        lifecycle.require_finalizable(c,self.ident)

    def test_verified_progress_requires_cumulative_minimum(self):
        self.start(review_only=False,confidence=.9).raise_for_status()
        self.assertEqual(self.progress().status_code,422)
        self.assertEqual(self.progress(confidence=.95).status_code,422)
        self.assertEqual(self.progress(confidence=.8).status_code,200)
        self.assertEqual(self.progress(sequence=2,end=11,confidence=.85).status_code,422)
        self.assertEqual(self.progress(sequence=2,end=11,confidence=.7).status_code,200)

    def test_reinitialize_preserves_lifecycle_and_old_rows(self):
        from backend.db.init_db import init_database
        self.start().raise_for_status();self.segment().raise_for_status()
        self.capture_report().raise_for_status()
        with get_connection() as c:
            before=dict(c.execute('SELECT * FROM processing_event_lifecycle WHERE event_id=?',(self.ident,)).fetchone())
            legacy=c.execute("SELECT COUNT(*) FROM candidate_event WHERE event_id='EVT_0001'").fetchone()[0]
        init_database()
        with get_connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM processing_camera_capture WHERE event_id=?',(self.ident,)).fetchone()[0],1)
            self.assertEqual(dict(c.execute('SELECT * FROM processing_event_lifecycle WHERE event_id=?',(self.ident,)).fetchone()),before)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM candidate_event WHERE event_id='EVT_0001'").fetchone()[0],legacy)

    def test_media_order_is_capture_sequence_not_upload_order(self):
        self.start().raise_for_status();self.progress(end=65).raise_for_status()
        self.segment(0,-2,58).raise_for_status()
        parts=self.segment(1,58,67).json()['segments']
        with get_connection() as c:
            for r in reversed(parts):
                mid=str(uuid4())
                c.execute("""INSERT INTO event_media(media_id,event_id,camera_id,kind,role,status,redaction_status,created_at,request_id,capture_start_at,capture_end_at)
                    VALUES(?,?,?,'video',?,'processing','unprocessed',?,?,?,?)""",
                    (mid,self.ident,r['camera_id'],r['role'],self.ts(100-r['sequence']),r['request_id'],r['capture_start_at'],r['capture_end_at']))
        event=self.client.get('/api/events/'+self.ident,headers=self.admin).json()
        event=event.get('event',event)
        sequence={r['request_id']:r['sequence'] for r in parts}
        self.assertEqual([sequence[m['clip_request_id']] for m in event['media']],[0,0,1,1])
        self.assertEqual(len(event['collection']['segments']),4)

    def test_expired_upload_registration_and_legacy_owner_guards(self):
        import time
        from backend.media.service import register_media
        self.start().raise_for_status();r=self.segment().json()['segments'][0]
        legacy={k:v for k,v in self.body.items() if k not in {'session_id','policy_key','observed_seconds','gap_seconds','gap_count'}}
        self.assertEqual(self.client.post('/api/processing/events',headers=self.nodes[1],json=legacy).status_code,409)
        self.assertEqual(self.client.get('/api/processing/events/'+self.ident+'/clip-requests',headers=self.nodes[1]).status_code,403)
        with get_connection() as c:
            c.execute('UPDATE processing_event_segment SET deadline_at=? WHERE event_id=?',(time.time()-1,self.ident))
        with self.assertRaisesRegex(ValueError,'delivery_expired'):
            register_media(self.ident,r['camera_id'],'video',r['role'],request_id=r['request_id'])
        with patch.dict(os.environ,{'PPE_PROCESSING_MODE':'remote'}):
            response=self.client.post('/api/processing/events/'+self.ident+'/clip?request_id='+r['request_id'],headers=self.nodes[0],content=b'not read')
            self.assertEqual(response.status_code,409,response.text)
        with get_connection() as c:
            c.execute('UPDATE processing_event_segment SET deadline_at=? WHERE event_id=?',(time.time()+100,self.ident))
            c.execute("UPDATE event_retention SET decision='retain',retain_until=? WHERE event_id=?",(time.time()-1,self.ident))
        with self.assertRaisesRegex(ValueError,'deleted_or_expired'):
            register_media(self.ident,r['camera_id'],'video',r['role'],request_id=r['request_id'])
        with get_connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM event_media WHERE event_id=?',(self.ident,)).fetchone()[0],0)

    def test_retention_expiring_during_processing_prevents_publication(self):
        import time
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from fastapi import HTTPException
        from backend.media.service import register_media
        from backend.processing.routes import publish,ResultMetadata
        self.start().raise_for_status();r=self.segment().json()['segments'][0]
        mid=register_media(self.ident,r['camera_id'],'video',r['role'],request_id=r['request_id'])
        with get_connection() as c:
            c.execute("UPDATE event_retention SET decision='retain',retain_until=? WHERE event_id=?",(time.time()-1,self.ident))
        with TemporaryDirectory() as folder:
            path=Path(folder)/'result.mp4';path.write_bytes(b'synthetic-fixture')
            job={'status':'running','media_id':mid,'redaction_mode':'scrfd','generation':0}
            meta=ResultMetadata(faces_detected=0,processed_frames=105,redaction_mode='scrfd',inference_backend='test',processing_seconds=1)
            with patch('backend.processing.routes.service.authorized',return_value=job),patch('backend.processing.routes.publish_file') as publish_file:
                with self.assertRaisesRegex(HTTPException,'deleted_or_expired'):
                    publish('job','producer','token',path,meta,{'fps':15,'duration_seconds':7,'frame_count':105})
                publish_file.assert_not_called()

    def test_actual_decoded_video_duration_must_match_manifest(self):
        import cv2
        import numpy as np
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from fastapi import HTTPException
        from backend.processing.routes import validate_result, ResultMetadata
        with TemporaryDirectory() as folder:
            path=Path(folder)/'short.mp4'
            writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),15,(32,32))
            self.assertTrue(writer.isOpened())
            for _ in range(15):writer.write(np.zeros((32,32,3),dtype=np.uint8))
            writer.release()
            measurement=validate_result(path,'video',ResultMetadata(faces_detected=0,processed_frames=15,redaction_mode='scrfd',inference_backend='test',processing_seconds=1))
            manifest={'capture_start_at':self.ts(-2),'capture_end_at':self.ts(5)}
            with self.assertRaisesRegex(HTTPException,'duration_mismatch'):
                lifecycle.validate_segment_measurement(manifest,measurement)
            lifecycle.validate_segment_measurement({'capture_start_at':self.ts(0),'capture_end_at':self.ts(1)},measurement)

    def test_segment_duration_tolerance_is_bounded_at_low_fps(self):
        import cv2
        import numpy as np
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from fastapi import HTTPException
        from backend.processing.routes import validate_result, ResultMetadata
        with TemporaryDirectory() as folder:
            path=Path(folder)/'low-fps.mp4'
            writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),0.2,(32,32))
            self.assertTrue(writer.isOpened())
            writer.write(np.zeros((32,32,3),dtype=np.uint8)); writer.release()
            measurement=validate_result(path,'video',ResultMetadata(faces_detected=0,processed_frames=1,redaction_mode='scrfd',inference_backend='test',processing_seconds=1))
            self.assertAlmostEqual(measurement['duration_seconds'],5)
            with self.assertRaisesRegex(HTTPException,'duration_mismatch'):
                lifecycle.validate_segment_measurement({'capture_start_at':self.ts(0),'capture_end_at':self.ts(7)},measurement)
            # Low FPS is not itself prohibited when its actual duration matches.
            lifecycle.validate_segment_measurement({'capture_start_at':self.ts(0),'capture_end_at':self.ts(5)},measurement)
        manifest={'capture_start_at':self.ts(0),'capture_end_at':self.ts(7)}
        for fps in (0.2,15,30):
            tolerance=min(1/fps,1/15)
            lifecycle.validate_segment_measurement(manifest,{'fps':fps,'duration_seconds':7-tolerance})
            with self.assertRaisesRegex(HTTPException,'duration_mismatch'):
                lifecycle.validate_segment_measurement(manifest,{'fps':fps,'duration_seconds':7-tolerance-0.00001})
        with self.assertRaisesRegex(HTTPException,'duration_mismatch'):
            lifecycle.validate_segment_measurement({'capture_start_at':self.ts(0),'capture_end_at':self.ts(0.5)}, {'fps':0.2,'duration_seconds':5})

    def capture_report(self,camera='CAM_001',headers=None,**changes):
        body=dict(session_id=self.session,sequence=1,state='collecting',captured_ranges=[{'start_at':self.ts(-2),'end_at':self.ts(5)}],missing_ranges=[],time_basis='shared_monotonic',clock_offset_seconds=0,clock_uncertainty_seconds=0)
        return self.client.post(self.base+'/'+self.ident+'/cameras/'+camera+'/capture-reports',headers=headers or self.nodes[0],json={**body,**changes})
    def complete_cameras(self):
        with patch.dict(os.environ,{'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS':'0'}):
            for camera in ('CAM_001','CAM_002'):
                self.capture_report(camera,state='complete').raise_for_status()

    def test_capture_discovery_immediate_owner_closed_and_deleted(self):
        started=self.start().json()
        self.assertEqual(len(started['cameras']),2)
        self.assertEqual(lifecycle.dt(started['cameras'][0]['required_start_at']),lifecycle.dt(self.ts(-2)))
        url=self.base+'/active'
        self.assertEqual(len(self.client.get(url,headers=self.nodes[0]).json()['items']),1)
        self.assertEqual(self.client.get(url,headers=self.nodes[1]).json()['items'],[])
        self.segment().raise_for_status()
        self.progress(kind='close',end=3,close_reason='ppe_present',complete=True,final_segment_count=1).raise_for_status()
        self.assertEqual(len(self.client.get(url,headers=self.nodes[0]).json()['items']),1)
        with get_connection() as c:c.execute("UPDATE event_retention SET decision='delete' WHERE event_id=?",(self.ident,))
        self.assertEqual(self.client.get(url,headers=self.nodes[0]).json()['items'],[])
        self.assertEqual(self.capture_report().status_code,409)

    def test_capture_discovery_paginates_and_v23_migration_preserves_events(self):
        from backend.db.init_db import init_database
        from backend.db.connection import is_postgres, schema_version
        from backend.db.migrations import LATEST_VERSION
        self.start().raise_for_status()
        second='EVT_'+uuid4().hex
        self.start(event_id=second).raise_for_status()
        first=self.client.get(self.base+'/active?limit=1',headers=self.nodes[0]).json()
        self.assertEqual(len(first['items']),1)
        last=self.client.get(self.base+'/active',params={'limit':1,'after':first['next_after']},headers=self.nodes[0]).json()
        self.assertIsNone(last['next_after'])
        self.assertEqual({first['items'][0]['event_id'],last['items'][0]['event_id']},{self.ident,second})
        with get_connection() as c:
            # Build a real v23 fixture: remove later tables in reverse migration
            # order without CASCADE, preserving the event/camera/request parents.
            c.execute('DROP TABLE processing_clip_receipt')
            c.execute('DROP TABLE processing_camera_capture')
            if is_postgres():
                c.execute('UPDATE app_schema_version SET version=23 WHERE singleton=1')
            else:
                c.execute('PRAGMA user_version=23')
        init_database()
        self.assertEqual(self.capture_report().status_code,200)
        with get_connection() as c:
            self.assertEqual(schema_version(c),LATEST_VERSION)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM processing_event_lifecycle').fetchone()[0],2)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM processing_camera_capture').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM processing_clip_receipt').fetchone()[0],0)

    def test_capture_reports_retry_sequence_owner_and_missing_preservation(self):
        self.start().raise_for_status()
        self.assertEqual(self.capture_report(headers=self.nodes[1]).status_code,403)
        self.assertEqual(self.capture_report('CAM_003').status_code,404)
        self.assertEqual(self.capture_report(sequence=2).status_code,409)
        missing=[{'start_at':self.ts(-2),'end_at':self.ts(0),'reason':'late_discovery'}]
        ranges=[{'start_at':self.ts(0),'end_at':self.ts(5)}]
        kwargs=dict(state='incomplete',missing_ranges=missing,captured_ranges=ranges)
        self.assertEqual(self.capture_report(**kwargs).status_code,200)
        self.assertEqual(self.capture_report(**kwargs).status_code,200)
        self.assertEqual(self.capture_report().status_code,409)
        self.assertEqual(self.capture_report(sequence=2).status_code,409)
        self.assertEqual(self.capture_report(sequence=2,**kwargs).status_code,200)
        with get_connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM processing_camera_capture WHERE event_id=?',(self.ident,)).fetchone()[0],2)
        data=self.client.get('/api/events/'+self.ident,headers=self.admin).json()
        self.assertIn('cameras',data.get('collection',data.get('event',{}).get('collection',{})))

    def test_capture_complete_requires_clock_policy_closed_and_full_range(self):
        self.start().raise_for_status()
        with patch.dict(os.environ,{'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS':''}):
            self.assertEqual(self.capture_report(state='complete').status_code,503)
        with patch.dict(os.environ,{'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS':'0'}):
            self.assertEqual(self.capture_report(state='complete').status_code,409)
            self.segment().raise_for_status()
            self.progress(kind='close',end=3,close_reason='ppe_present',complete=True,final_segment_count=1).raise_for_status()
            self.assertEqual(self.capture_report(state='complete',clock_uncertainty_seconds=.01).status_code,422)
            self.assertEqual(self.capture_report(state='complete',earliest_available_at=self.ts(0)).status_code,409)
            self.assertEqual(self.capture_report(state='complete',captured_ranges=[{'start_at':self.ts(0),'end_at':self.ts(5)}]).status_code,409)
            self.assertEqual(self.capture_report(state='complete').status_code,200)
            self.assertEqual(self.capture_report('CAM_002',state='complete').status_code,200)
            with get_connection() as c:
                with self.assertRaisesRegex(ValueError,'incomplete'): lifecycle.require_finalizable(c,self.ident)

    def test_private_receipt_recovers_response_loss_without_extending_ttl(self):
        import hashlib
        import time
        from backend.db.init_db import init_database
        self.start().raise_for_status();part=self.segment().json()['segments'][0]
        path=self.base+'/'+self.ident+'/receipts/'+part['request_id']
        params={'session_id':self.session}
        self.assertEqual(self.client.get(path,params=params,headers=self.nodes[0]).status_code,404)
        raw=b'\x00\x00\x00\x18ftypisom'+b'synthetic-private-ingest-test'
        url='/api/processing/events/'+self.ident+'/clip?request_id='+part['request_id']
        with patch.dict(os.environ,{'PPE_PROCESSING_MODE':'remote'}):
            uploaded=self.client.post(url,headers=self.nodes[0],content=raw)
            self.assertEqual(uploaded.status_code,201,uploaded.text)
            self.assertIsNone(uploaded.json()['url'])
            self.assertEqual(self.client.post(url,headers=self.nodes[0],content=raw).status_code,409)
        receipt=self.client.get(path,params=params,headers=self.nodes[0])
        self.assertEqual(receipt.headers['cache-control'],'private, no-store')
        body=receipt.json()
        self.assertTrue(body['accepted'])
        self.assertEqual(body['source_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(body['source_bytes'],len(raw))
        self.assertEqual(body['media_id'],uploaded.json()['media_id'])
        self.assertEqual(body['camera_id'],part['camera_id'])
        self.assertEqual(body['source_state'],'available')
        self.assertNotIn('source_path',body);self.assertNotIn('url',body)
        self.assertEqual(self.client.get(path,params=params,headers=self.nodes[1]).status_code,403)
        self.assertEqual(self.client.get(path,params={'session_id':uuid4().hex},headers=self.nodes[0]).status_code,403)
        with get_connection() as c:
            expiry=time.time()-1
            c.execute('UPDATE event_media SET source_expires_at=? WHERE media_id=?',(expiry,body['media_id']))
        init_database()
        expired=self.client.get(path,params=params,headers=self.nodes[0]).json()
        self.assertTrue(expired['accepted']);self.assertEqual(expired['source_state'],'source_expired')
        self.assertEqual(expired['source_sha256'],body['source_sha256'])
        with get_connection() as c:
            self.assertEqual(c.execute('SELECT source_expires_at FROM event_media WHERE media_id=?',(body['media_id'],)).fetchone()[0],expiry)
            c.execute("UPDATE event_retention SET decision='delete' WHERE event_id=?",(self.ident,))
        deleted=self.client.get(path,params=params,headers=self.nodes[0]).json()
        self.assertTrue(deleted['accepted']);self.assertEqual(deleted['source_state'],'deleted')

    def test_failed_ingest_never_issues_delivery_receipt(self):
        self.start().raise_for_status();part=self.segment().json()['segments'][0]
        with patch.dict(os.environ,{'PPE_PROCESSING_MODE':'remote'}), patch('backend.processing.service.enqueue',side_effect=ValueError('queue_failure')):
            response=self.client.post('/api/processing/events/'+self.ident+'/clip?request_id='+part['request_id'],headers=self.nodes[0],content=b'\x00\x00\x00\x18ftypisom'+b'private')
            self.assertNotEqual(response.status_code,201)
        response=self.client.get(self.base+'/'+self.ident+'/receipts/'+part['request_id'],params={'session_id':self.session},headers=self.nodes[0])
        self.assertEqual(response.status_code,404)
        with get_connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM processing_clip_receipt').fetchone()[0],0)

    def test_command_ack_binds_exact_retry_not_current_revision(self):
        self.assertEqual(self.start().json()['accepted_command'],{'kind':'start','session_id':self.session,'sequence':0,'camera_id':None})
        first=self.progress().json()
        self.assertEqual(first['accepted_command'],{'kind':'progress','session_id':self.session,'sequence':1,'camera_id':None})
        self.progress(sequence=2,end=11).raise_for_status()
        retry=self.progress().json()
        self.assertEqual(retry['lifecycle']['revision'],2)
        self.assertEqual(retry['accepted_command'],first['accepted_command'])
        manifest=self.segment(end=13).json()
        self.assertEqual(manifest['accepted_command'],{'kind':'manifest','session_id':self.session,'sequence':0,'camera_id':None})
        for camera in ('CAM_001','CAM_002'):
            capture=self.capture_report(camera).json()
            self.assertEqual(capture['accepted_command'],{'kind':'capture_report','session_id':self.session,'sequence':1,'camera_id':camera})
        close=self.progress(sequence=3,end=11,kind='close',close_reason='ppe_present',complete=True,final_segment_count=1).json()
        self.assertEqual(close['accepted_command']['kind'],'close')
        self.assertEqual(close['accepted_command']['sequence'],3)
        conflict=self.progress(sequence=2,end=12)
        self.assertEqual(conflict.status_code,409)
        self.assertNotIn('accepted_command',conflict.json())

    def test_legacy_event_cannot_be_claimed(self):
        legacy={k:v for k,v in self.body.items() if k not in {'session_id','policy_key','observed_seconds','gap_seconds','gap_count'}}
        self.client.post('/api/processing/events',headers=self.nodes[0],json=legacy).raise_for_status()
        self.assertEqual(self.start().status_code,409)
    def test_concurrent_progress_exactly_one_wins(self):
        self.start().raise_for_status()
        from concurrent.futures import ThreadPoolExecutor
        from fastapi import HTTPException
        def call(end):
            body=lifecycle.Update(session_id=self.session,sequence=1,kind='progress',timestamp_end=self.ts(end),frame_sample_count=151,observed_seconds=end,gap_seconds=0,gap_count=0)
            try:
                lifecycle.update(self.ident,body,'producer');return 200
            except HTTPException as e:return e.status_code
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(call,[9,10])),[200,409])

if __name__=='__main__':unittest.main()
