from datetime import datetime,timezone,timedelta
from uuid import uuid4
from unittest.mock import patch
import unittest
from backend.tests import test_video
from backend.db.event_repository import get_connection

class CaptureSessionTests(unittest.TestCase):
    def setUp(self):
        test_video.VideoIngestTests.setUp(self)
        p=patch.dict('os.environ',{'PPE_CAPTURE_CONTEXT_ENABLED':'true'});p.start();self.addCleanup(p.stop)

    def session(self):
        response=self.client.post('/api/video/capture-sessions',headers=self.nodes['source'],json={'camera_id':'CAM_001','session_id':str(uuid4())})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def stream(self,c):
        ident=str(uuid4());body={'camera_id':'CAM_001','capture_context_id':c['context_id'],'stream_id':ident}
        r=self.client.post('/api/video/streams',headers=self.nodes['source'],json=body)
        self.assertEqual(r.status_code,201,r.text)
        self.assertEqual(self.client.post('/api/video/streams',headers=self.nodes['source'],json=body).json(),r.json())
        return ident

    def stop(self,c,seq):
        return self.client.post('/api/video/capture-sessions/'+c['context_id']+'/stop',headers=self.nodes['source'],json={'capture_stopped':True,'final_sequences':seq})

    def test_gate_binding_and_explicit_ack(self):
        c=self.session();stream=self.stream(c)
        self.assertEqual(self.stop(c,{}).status_code,409)
        self.assertEqual(self.client.post('/api/video/capture-sessions/'+c['context_id']+'/stop',headers=self.nodes['other'],json={'capture_stopped':True,'final_sequences':{stream:-1}}).status_code,403)
        self.assertEqual(self.client.patch('/api/cameras/CAM_001',headers=self.admin,json={'zone_name':None}).status_code,409)
        result=self.stop(c,{stream:-1});self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(result.json()['retired'])
        self.assertEqual(self.stop(c,{stream:-1}).json()['valid_until'],result.json()['valid_until'])
        self.assertEqual(self.client.patch('/api/cameras/CAM_001',headers=self.admin,json={'zone_name':None}).status_code,200)

    def test_timeout_does_not_retire_or_unlock(self):
        c=self.session();stream=self.stream(c)
        with get_connection() as conn:conn.execute('UPDATE video_stream SET last_seen_at=0 WHERE stream_id=?',(stream,))
        from backend.video.service import cleanup
        cleanup()
        self.assertEqual(self.client.patch('/api/cameras/CAM_001',headers=self.admin,json={'zone_name':None}).status_code,409)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT status FROM video_stream WHERE stream_id=?',(stream,)).fetchone()[0],'active')
        self.assertEqual(self.stop(c,{stream:-1}).status_code,200)

    def test_flag_disable_does_not_bypass_binding(self):
        c=self.session();self.stream(c)
        with patch.dict('os.environ',{'PPE_CAPTURE_CONTEXT_ENABLED':'false'}):
            self.assertEqual(self.client.patch('/api/cameras/CAM_001',headers=self.admin,json={'zone_name':None}).status_code,409)
            self.assertEqual(self.client.post('/api/video/streams',headers=self.nodes['source'],json={'camera_id':'CAM_001'}).status_code,422)

    def test_event_before_registration_move_uses_context_zone(self):
        c=self.session();stream=self.stream(c)
        now=datetime.now(timezone.utc)
        body={'event_id':'EVT_'+uuid4().hex,'camera_id':'CAM_001','capture_context_id':c['context_id'],
              'tracking_id':'T1','ppe_type':'helmet','timestamp_start':now.isoformat(),'timestamp_end':now.isoformat(),
              'frame_sample_count':1,'model_version':'test','review_only':True}
        self.assertEqual(self.stop(c,{stream:-1}).status_code,200)
        self.assertEqual(self.client.patch('/api/cameras/CAM_001',headers=self.admin,json={'zone_name':None}).status_code,200)
        from backend.processing.events import EventSubmission,submit
        submit(EventSubmission(**body))
        with get_connection() as conn:
            row=conn.execute('SELECT zone_name,source FROM event_zone_snapshot WHERE event_id=?',(body['event_id'],)).fetchone()
            self.assertEqual((row['zone_name'],row['source']),(c['zone_name'],'capture_context'))

    def test_final_sequence_and_missing_context_rejected(self):
        c=self.session();stream=self.stream(c)
        self.assertEqual(self.stop(c,{stream:0}).status_code,409)
        self.assertEqual(self.client.post('/api/video/streams',headers=self.nodes['source'],json={'camera_id':'CAM_001'}).status_code,422)
        from backend.processing.events import EventSubmission,submit
        now=datetime.now(timezone.utc)
        body=EventSubmission(event_id='EVT_'+uuid4().hex,camera_id='CAM_001',ppe_type='helmet',timestamp_start=now,timestamp_end=now,frame_sample_count=1,model_version='test',review_only=True)
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):submit(body)

    def test_chunk_context_and_delayed_event_after_stop(self):
        c=self.session();stream=self.stream(c)
        start=datetime.now(timezone.utc)-timedelta(seconds=8)
        with get_connection() as conn:
            conn.execute('UPDATE capture_context_registry SET valid_from=? WHERE context_id=?',((start-timedelta(seconds=1)).isoformat(),c['context_id']))
        headers={**self.nodes['source'],'Content-Type':'video/mp4','X-Capture-Start':start.isoformat(),'X-Duration-Ms':'1000'}
        r=self.client.post(f'/api/video/streams/{stream}/chunks?sequence=0',headers=headers,content=self.data)
        self.assertEqual(r.status_code,202,r.text)
        listed=self.client.get(f'/api/processing/video/streams/{stream}/chunks',headers=self.nodes['processor']).json()['items']
        self.assertEqual(listed[0]['capture_context_id'],c['context_id'])
        self.assertEqual(self.stop(c,{stream:0}).status_code,200)
        self.assertEqual(self.client.post(f'/api/video/streams/{stream}/chunks?sequence=1',headers=headers,content=self.data).status_code,409)
        self.assertEqual(self.client.patch('/api/cameras/CAM_001',headers=self.admin,json={'zone_name':None}).status_code,200)
        from backend.processing import lifecycle
        with patch.dict('os.environ',{'PPE_CONTINUOUS_EVENTS_ENABLED':'true'}):
            body=lifecycle.Start(event_id='EVT_'+uuid4().hex,camera_id='CAM_001',capture_context_id=c['context_id'],
                tracking_id='T',ppe_type='helmet',timestamp_start=start,timestamp_end=start+timedelta(seconds=3),
                frame_sample_count=3,model_version='test',review_only=True,observed_seconds=3,gap_seconds=0,gap_count=0,
                session_id=uuid4().hex,policy_key='test')
            lifecycle.start(body,'processor')
        with get_connection() as conn:
            row=conn.execute('SELECT zone_name,source FROM event_zone_snapshot WHERE event_id=?',(body.event_id,)).fetchone()
            self.assertEqual((row['zone_name'],row['source']),(c['zone_name'],'capture_context'))

    def test_source_reassignment_is_blocked_while_active(self):
        c=self.session();stream=self.stream(c)
        r=self.client.put('/api/cameras/CAM_001/node',headers=self.admin,json={'source_node_id':'other','output_node_id':'source'})
        self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.stop(c,{stream:-1}).status_code,200)
        self.assertEqual(self.client.put('/api/cameras/CAM_001/node',headers=self.admin,json={'source_node_id':'other','output_node_id':'source'}).status_code,200)

    def test_v30_migration_preserves_old_snapshot_and_repeats(self):
        from backend.db.connection import is_postgres
        from backend.db.init_db import init_database
        if is_postgres():self.skipTest('SQLite synthetic ledger boundary')
        with get_connection() as conn:
            before=[dict(r) for r in conn.execute('SELECT * FROM event_zone_snapshot ORDER BY event_id')]
            conn.execute('PRAGMA user_version=30')
        init_database();init_database()
        with get_connection() as conn:
            self.assertEqual(before,[dict(r) for r in conn.execute('SELECT * FROM event_zone_snapshot ORDER BY event_id')])

    def test_legacy_start_canonical_does_not_add_null_context(self):
        from backend.processing.lifecycle import canonical
        class Body:
            def model_dump(self,**kwargs):return {'event_id':'EVT_TEST','capture_context_id':None}
        self.assertEqual(canonical(Body()),'{"event_id":"EVT_TEST"}')

    def buffered_context(self):
        c=self.session();stream=self.stream(c)
        start=datetime.now(timezone.utc)-timedelta(seconds=8)
        with get_connection() as conn:
            conn.execute('UPDATE capture_context_registry SET valid_from=? WHERE context_id=?',
                         ((start-timedelta(seconds=1)).isoformat(),c['context_id']))
        headers={**self.nodes['source'],'Content-Type':'video/mp4',
                 'X-Capture-Start':start.isoformat(),'X-Duration-Ms':'1000'}
        response=self.client.post(f'/api/video/streams/{stream}/chunks?sequence=0',
                                  headers=headers,content=self.data)
        self.assertEqual(response.status_code,202,response.text)
        return c,stream,response.json()['chunk_id'],headers

    def processor_streams(self):
        response=self.client.get('/api/processing/video/streams',headers=self.nodes['processor'])
        self.assertEqual(response.status_code,200,response.text)
        return {row['stream_id']:row for row in response.json()['items']}

    def test_stopped_buffered_stream_has_frozen_final_and_original_context(self):
        c,stream,chunk,_=self.buffered_context()
        self.assertEqual(self.stop(c,{stream:0}).status_code,200)
        row=self.processor_streams()[stream]
        self.assertTrue(row['capture_stopped'])
        self.assertEqual(row['final_sequence'],0)
        self.assertEqual(row['last_sequence'],0)
        self.assertEqual(row['capture_context_id'],c['context_id'])
        self.assertEqual(row['capture_session_id'],c['session_id'])
        self.assertEqual(row['assignment_version'],c['assignment_version'])
        self.assertEqual(self.client.get('/api/processing/video/chunks/'+chunk,
                                        headers=self.nodes['processor']).status_code,200)
        self.assertNotEqual(self.client.get('/api/processing/video/chunks/'+chunk,
                                           headers=self.nodes['source']).status_code,200)

    def test_stopped_and_new_stream_coexist_without_context_rebinding(self):
        old,old_stream,_,_=self.buffered_context()
        self.assertEqual(self.stop(old,{old_stream:0}).status_code,200)
        with get_connection() as conn:
            destination=conn.execute('SELECT zone_name FROM zone_rule WHERE zone_name!=? LIMIT 1',
                                     (old['zone_name'],)).fetchone()[0]
        self.assertEqual(self.client.patch('/api/cameras/CAM_001',headers=self.admin,
                                           json={'zone_name':destination}).status_code,200)
        new=self.session();new_stream=self.stream(new)
        rows=self.processor_streams()
        self.assertEqual(set(rows),{old_stream,new_stream})
        self.assertTrue(rows[old_stream]['capture_stopped'])
        self.assertFalse(rows[new_stream]['capture_stopped'])
        self.assertNotIn('final_sequence',rows[new_stream])
        self.assertEqual(rows[old_stream]['capture_context_id'],old['context_id'])
        self.assertEqual(rows[new_stream]['capture_context_id'],new['context_id'])
        self.assertGreater(rows[new_stream]['assignment_version'],rows[old_stream]['assignment_version'])
        chunks=self.client.get(f'/api/processing/video/streams/{old_stream}/chunks',
                               headers=self.nodes['processor']).json()['items']
        self.assertEqual(chunks[0]['capture_context_id'],old['context_id'])

    def test_expired_raw_is_unavailable_but_receipt_survives_cleanup(self):
        from backend.video import service
        c,stream,chunk,headers=self.buffered_context()
        with get_connection() as conn:
            before=dict(conn.execute('SELECT * FROM capture_chunk_receipt WHERE stream_id=?',
                                     (stream,)).fetchone())
            conn.execute('UPDATE video_chunk SET expires_at=0 WHERE stream_id=?',(stream,))
        self.assertEqual(self.client.get('/api/processing/video/chunks/'+chunk,
                                        headers=self.nodes['processor']).status_code,404)
        service.cleanup()
        retry=self.client.post(f'/api/video/streams/{stream}/chunks?sequence=0',
                               headers=headers,content=self.data)
        self.assertEqual(retry.status_code,202,retry.text)
        self.assertTrue(retry.json()['duplicate'])
        self.assertEqual(retry.json()['chunk_id'],chunk)
        self.assertEqual(self.stop(c,{stream:0}).status_code,200)
        self.assertNotIn(stream,self.processor_streams())
        self.assertEqual(self.client.get('/api/processing/video/chunks/'+chunk,
                                        headers=self.nodes['processor']).status_code,404)
        self.assertEqual(self.client.get(f'/api/processing/video/streams/{stream}/chunks',
                                        headers=self.nodes['processor']).json()['items'],[])
        with get_connection() as conn:
            self.assertEqual(before,dict(conn.execute('SELECT * FROM capture_chunk_receipt WHERE stream_id=?',
                                                      (stream,)).fetchone()))
            self.assertIsNotNone(conn.execute('SELECT * FROM capture_context_stream WHERE stream_id=?',
                                             (stream,)).fetchone())
