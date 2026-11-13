"""Real API + synthetic media + real EOS reader/mux; no physical camera."""
from datetime import datetime,timezone,timedelta
from uuid import uuid4
from unittest.mock import patch
import unittest
import numpy as np
from backend.tests import test_video
from backend.db.event_repository import get_connection
from inference_worker.continuous_source_mux import SourceMux,SourceMuxError
from inference_worker.continuous_source_reader import ContinuousSourceReader

class ProcessorEosApiTests(unittest.TestCase):
    def setUp(self):
        test_video.VideoIngestTests.setUp(self)
        p=patch.dict('os.environ',{'PPE_CAPTURE_CONTEXT_ENABLED':'true'});p.start();self.addCleanup(p.stop)
        with get_connection() as conn:
            zone=conn.execute("SELECT zone_name FROM camera_info WHERE camera_id='CAM_001'").fetchone()[0]
        self.client.post('/api/cameras',headers=self.admin,json={'camera_id':'CAM_TAIL','zone_name':zone}).raise_for_status()
        self.client.put('/api/cameras/CAM_TAIL/node',headers=self.admin,json={'source_node_id':'source','output_node_id':'source'}).raise_for_status()
        self.contexts={};self.streams={};self.start=datetime.now(timezone.utc)-timedelta(seconds=15)
        for camera in ('CAM_001','CAM_TAIL'):
            r=self.client.post('/api/video/capture-sessions',headers=self.nodes['source'],json={'camera_id':camera,'session_id':str(uuid4())});r.raise_for_status()
            context=r.json()['context_id'];stream=str(uuid4())
            with get_connection() as conn:
                conn.execute('UPDATE capture_context_registry SET valid_from=? WHERE context_id=?',((self.start-timedelta(seconds=1)).isoformat(),context))
            self.client.post('/api/video/streams',headers=self.nodes['source'],json={'camera_id':camera,'capture_context_id':context,'stream_id':stream}).raise_for_status()
            self.contexts[camera]=context;self.streams[camera]=stream
        outer=self
        class Adapter:
            def list_streams(self):
                r=outer.client.get('/api/processing/video/streams',headers=outer.nodes['processor']);r.raise_for_status();return r.json()['items']
            def list_chunks(self,stream,after):
                r=outer.client.get(f'/api/processing/video/streams/{stream}/chunks',params={'after_sequence':after},headers=outer.nodes['processor']);r.raise_for_status();return r.json()['items']
            def stream_availability(self,stream,after):
                r=outer.client.get(f'/api/processing/video/streams/{stream}/availability',params={'after_sequence':after},headers=outer.nodes['processor'])
                if r.status_code==404:return None
                r.raise_for_status();return r.json()
        def download(ident):
            r=self.client.get('/api/processing/video/chunks/'+ident,headers=self.nodes['processor']);r.raise_for_status();return r.content
        def decode(raw,**kwargs):
            self.assertEqual(raw,self.data)
            yield 0,2,np.zeros((2,2,3),dtype=np.uint8)
            yield 1,2,np.zeros((2,2,3),dtype=np.uint8)
        mux=SourceMux(camera_ids=list(self.streams),primary_camera='CAM_001',max_bytes=10000,max_frames=100,max_frame_pixels=100,max_clock_error=0,clock_error_by_camera={c:0 for c in self.streams})
        self.reader=ContinuousSourceReader(Adapter(),mux,offsets={c:0 for c in self.streams},decoder=decode,downloader=download,max_chunk_bytes=10_000_000,max_chunk_gap_seconds=0,require_capture_context=True)
        self.assertEqual(self.reader.poll(),[])

    def upload(self,camera,seq):
        headers={**self.nodes['source'],'Content-Type':'video/mp4','X-Capture-Start':(self.start+timedelta(seconds=seq)).isoformat(),'X-Duration-Ms':'1000'}
        r=self.client.post(f'/api/video/streams/{self.streams[camera]}/chunks?sequence={seq}',headers=headers,content=self.data);r.raise_for_status()

    def stop(self,camera,seq):
        r=self.client.post('/api/video/capture-sessions/'+self.contexts[camera]+'/stop',headers=self.nodes['source'],json={'capture_stopped':True,'final_sequences':{self.streams[camera]:seq}});r.raise_for_status()

    def test_uneven_stopped_streams_all_uploaded_frames_drained(self):
        for camera in self.streams:self.upload(camera,0)
        output=self.reader.poll();self.assertEqual(len(output),4)
        self.upload('CAM_001',1);self.upload('CAM_001',2)
        self.stop('CAM_TAIL',0);self.stop('CAM_001',2)
        output+=self.reader.poll();output+=self.reader.poll()
        self.assertEqual(sum(c=='CAM_001' for c,_,_ in output),6)
        self.assertEqual(sum(c=='CAM_TAIL' for c,_,_ in output),2)
        self.assertEqual([t for _,t,_ in output],sorted(t for _,t,_ in output))
        with self.assertRaises(SourceMuxError):self.reader.poll()
        self.assertEqual(self.reader.primary_capture_context(),self.contexts['CAM_001'])

    def test_primary_end_freezes_live_reference_without_stopping_sender(self):
        for camera in self.streams:self.upload(camera,0)
        self.assertEqual(len(self.reader.poll()),4)
        self.upload('CAM_TAIL',1);self.stop('CAM_001',0)
        tail=self.reader.poll()
        self.assertEqual(len(tail),2)
        self.assertTrue(all(camera=='CAM_TAIL' for camera,_,_ in tail))
        self.upload('CAM_TAIL',2)
        with self.assertRaises(SourceMuxError):self.reader.poll()
        self.assertEqual(self.reader.sequence['CAM_TAIL'],1)
        with get_connection() as conn:
            row=conn.execute('SELECT valid_until FROM capture_context_registry WHERE context_id=?',(self.contexts['CAM_TAIL'],)).fetchone()
            self.assertIsNone(row['valid_until'])
