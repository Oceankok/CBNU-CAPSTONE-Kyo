from unittest import TestCase
from unittest.mock import patch
import numpy as np
from inference_worker.continuous_source_mux import SourceMux,SourceMuxError
from inference_worker.continuous_source_reader import ContinuousSourceReader

class ReaderTests(TestCase):
    def stop_fixture(self):
        c,r=self.make();r.clock_verifier=lambda camera,chunk:None
        run='00000000-0000-4000-8000-000000000001';proof='00000000-0000-4000-8000-000000000002'
        marker=dict(stream_id='a',run_id=run,sequence=0,frame_index=1,fps=2,proof_id=proof)
        for row in c.streams:
            row.update(clock_run_id=run,capture_context_id='ctx'+row['camera_id'],capture_session_id='session',assignment_version=1)
        c.streams[0]['observation_stop']=marker
        original=c.list_chunks
        def chunks(stream,after):
            context=next(x for x in c.streams if x['stream_id']==stream)
            return [dict(x,**{k:v for k,v in context.items() if k not in ('stream_id','last_sequence','camera_id')},clock_proof_id=proof) for x in original(stream,after)]
        c.list_chunks=chunks
        return c,r,marker

    def test_observation_boundary_uses_actual_decoded_frame(self):
        c,r,marker=self.stop_fixture()
        batch=r.poll()
        self.assertEqual(len(batch),4)
        self.assertEqual(r.observation_cutoffs['main'],1767225600.5)
        self.assertEqual(sum(cam=='main' and t>=r.observation_cutoffs['main'] for cam,t,_ in batch),1)
        self.assertEqual(r.poll(),[])

    def test_observation_marker_cannot_change_revert_or_arrive_late(self):
        for fault in ('changed','reverted','late','binding'):
            with self.subTest(fault=fault):
                c,r,marker=self.stop_fixture()
                if fault=='late':
                    del c.streams[0]['observation_stop'];r.poll();c.streams[0]['observation_stop']=marker
                elif fault=='binding':marker['run_id']='00000000-0000-4000-8000-000000000003'
                else:
                    r.poll()
                    if fault=='changed':c.streams[0]['observation_stop']=dict(marker,frame_index=0)
                    else:del c.streams[0]['observation_stop']
                with self.assertRaises(SourceMuxError):r.poll()

    def test_observation_boundary_rejects_fps_mismatch_and_missing_frame(self):
        c,r,marker=self.stop_fixture();marker['fps']=3
        with self.assertRaisesRegex(SourceMuxError,'observation_stop_frame_binding_mismatch'):r.poll()
        c,r,marker=self.stop_fixture()
        r.decoder=lambda *args,**kwargs:iter([(0,2,np.zeros((2,2,3),np.uint8))])
        with self.assertRaisesRegex(SourceMuxError,'observation_stop_frame_missing'):r.poll()

    def make(self):
        class Client:
            seq=0
            streams=[{'camera_id':'main','stream_id':'a','last_sequence':-1},{'camera_id':'ref','stream_id':'b','last_sequence':-1}]
            def list_streams(self):return self.streams
            def list_chunks(self,stream,after_sequence):
                if self.seq<=after_sequence:return []
                return [{'sequence':self.seq,'chunk_id':stream+str(self.seq),
                    'capture_start_at':f'2026-01-01T00:00:0{self.seq}Z','duration_ms':1000}]
            def download_chunk(self,ident):return b'synthetic'
        c=Client()
        mux=SourceMux(camera_ids=['main','ref'],primary_camera='main',max_bytes=10000,
            max_frames=20,max_frame_pixels=16,max_clock_error=0,clock_error_by_camera={'main':0,'ref':0})
        def decode(*args,**kwargs):
            for i in range(2):yield i,2,np.zeros((2,2,3),np.uint8)
        return c,ContinuousSourceReader(c,mux,offsets={'main':0,'ref':0},decoder=decode,
            max_chunk_bytes=100,max_chunk_gap_seconds=.1,downloader=c.download_chunk)
    def test_two_camera_chunks_and_duplicate_poll(self):
        c,r=self.make();out=r.poll();self.assertEqual(len(out),4)
        self.assertEqual([cam for cam,_,_ in out],['ref','main','ref','main'])
        self.assertEqual(r.poll(),[])
        c.seq=1;self.assertEqual(len(r.poll()),4)
    def test_stream_reset_and_sequence_gap_fail_closed(self):
        c,r=self.make();r.poll();c.seq=2
        with self.assertRaises(SourceMuxError):r.poll()
        c,r=self.make();r.poll();c.streams[0]={'camera_id':'main','stream_id':'new'}
        with self.assertRaises(SourceMuxError):r.poll()
    def test_missing_camera_fails_without_implicit_reference_skip(self):
        c,r=self.make();c.streams=c.streams[:1]
        with self.assertRaises(SourceMuxError):r.poll()

    def test_live_boundary_never_replays_buffered_chunks(self):
        c,r=self.make()
        for stream in c.streams:stream['last_sequence']=0
        self.assertEqual(r.poll(),[])
        c.seq=1;self.assertEqual(len(r.poll()),4)

    def test_verified_startup_preroll_only_reads_a_bounded_suffix(self):
        c,r,_=self.stop_fixture()
        del c.streams[0]['observation_stop']
        r.require_capture_context=True
        r.startup_preroll_seconds=2.25
        for stream in c.streams:
            stream['last_sequence']=4
        def chunks(stream,after):
            row=next(s for s in c.streams if s['stream_id']==stream)
            return [dict(sequence=i,chunk_id=stream+str(i),duration_ms=1000,
                         capture_start_at=f'2026-01-01T00:00:0{i}Z',
                         **{k:row[k] for k in ('capture_context_id','capture_session_id','assignment_version')})
                    for i in range(after+1,row['last_sequence']+1)]
        c.list_chunks=chunks
        for second in (2,3,4):
            batch=r.poll()
            self.assertEqual(len(batch),4)
            self.assertEqual(batch[0][1],1767225600+second)
            self.assertTrue(all(r.is_startup_evidence(cam,t) for cam,t,_ in batch))
        for stream in c.streams:
            stream['last_sequence']=5
        batch=r.poll()
        self.assertEqual(len(batch),4)
        self.assertFalse(any(r.is_startup_evidence(cam,t) for cam,t,_ in batch))

    def test_pressure_fails_only_blocking_reference_preserves_buffer_and_capacity(self):
        for kind in ('frames','bytes'):
            with self.subTest(kind=kind):
                c,r=self.make();m=r.mux;r.degrade_references=True
                m.max_frames=20 if kind=='frames' else 100
                m.max_bytes=10000 if kind=='frames' else 240
                frame=np.zeros((2,2,3),np.uint8)
                r.streams={'main':'a','ref':'b'};r.sequence={'main':-1,'ref':-1}
                m.push('ref',0,frame);m.push('main',0,frame);m.pop_ready()
                m.push('ref',.25,frame)
                for i in range(1,15):m.push('main',float(i),frame)
                self.assertLessEqual(len(m.queue),m.max_frames);self.assertLessEqual(m.bytes,m.max_bytes)
                batch=r.poll()
                self.assertEqual(r.reference_failures['ref']['failure_code'],'reference_watermark_pressure')
                self.assertIn(('ref',.25),[(cam,t) for cam,t,_ in batch])
                self.assertEqual(m.bytes,0);self.assertFalse(m.failure)
                self.assertEqual(r.end_reasons['ref'],'source_failed')
                self.assertNotIn('main',m.ended)

    def test_pressure_prioritizes_advertised_reference_chunk(self):
        c,r=self.make();m=r.mux;r.degrade_references=True
        frame=np.zeros((2,2,3),np.uint8)
        r.streams={'main':'a','ref':'b'};r.sequence={'main':-1,'ref':-1}
        c.streams[1]['last_sequence']=0
        m.push('ref',0,frame);m.push('main',0,frame);m.pop_ready()
        for i in range(1,16):m.push('main',1767225600.+i,frame)
        result=r.poll()
        self.assertFalse(r.reference_failures)
        self.assertEqual(r.sequence['ref'],0)
        self.assertEqual(r.sequence['main'],-1)
        self.assertTrue(any(cam=='ref' for cam,_,_ in result))

    def test_pressure_does_not_hide_reference_authentication_failure(self):
        import httpx
        c,r=self.make();m=r.mux;r.degrade_references=True
        frame=np.zeros((2,2,3),np.uint8)
        r.streams={'main':'a','ref':'b'};r.sequence={'main':-1,'ref':-1}
        c.streams[1]['last_sequence']=0
        m.push('ref',0,frame);m.push('main',0,frame);m.pop_ready()
        for i in range(1,16):m.push('main',float(i),frame)
        request=httpx.Request('GET','https://test')
        def unauthorized(*args):raise httpx.HTTPStatusError('denied',request=request,response=httpx.Response(401,request=request))
        c.list_chunks=unauthorized
        with self.assertRaises(httpx.HTTPStatusError):r.poll()
        self.assertEqual(r.reference_failures,{})

    def test_mid_chunk_pressure_resumes_exact_decoder_without_sequence_loss(self):
        for limit in ('frames','bytes'):
            with self.subTest(limit=limit):
                c,r=self.make();m=r.mux;r.degrade_references=True
                m.max_frames=20 if limit=='frames' else 100
                m.max_bytes=10000 if limit=='frames' else 240
                r.streams={'main':'a','ref':'b'};r.sequence={'main':-1,'ref':-1}
                c.streams[0]['last_sequence']=0
                original_chunks=c.list_chunks
                c.list_chunks=lambda stream,after: [] if stream=='b' else original_chunks(stream,after)
                frame=np.zeros((2,2,3),np.uint8);closed=[]
                m.push('ref',0,frame);m.push('main',0,frame);m.pop_ready()
                def decode(*args,**kwargs):
                    try:
                        for i in range(45):yield i,45,frame
                    finally:closed.append(True)
                r.decoder=decode
                output=[]
                for _ in range(10):
                    output.extend(r.poll())
                    self.assertLessEqual(len(m.queue),m.max_frames)
                    self.assertLessEqual(m.bytes,m.max_bytes)
                    if r.sequence['main']==0 and r.pending_decode is None:break
                output.extend(m.pop_ready())
                self.assertEqual(len(output),45)
                self.assertEqual([t for _,t,_ in output],sorted(t for _,t,_ in output))
                self.assertEqual(r.sequence['main'],0);self.assertEqual(closed,[True])
                self.assertEqual(r.reference_failures['ref']['failure_code'],'reference_watermark_pressure')
                self.assertNotIn('main',m.ended)

    def test_reserve_checked_again_before_second_chunk_in_same_poll(self):
        c,r=self.make();m=r.mux;r.degrade_references=True
        r.reserve_chunk_frames=5;r.reserve_chunk_bytes=60
        m.max_frames=20;m.max_bytes=10000
        c.streams[0]['last_sequence']=0;c.streams[1]['last_sequence']=0
        r.streams={'main':'a','ref':'b'};r.sequence={'main':-1,'ref':-1}
        frame=np.zeros((2,2,3),np.uint8)
        for i in range(14):m.push('main',float(i),frame)
        seen=[];original=r.decoder
        def decode(*args,**kwargs):seen.append(True);return original(*args,**kwargs)
        r.decoder=decode
        with patch.object(m,'pressure_blockers',return_value=()), patch.object(m,'pop_ready',return_value=[]):
            with self.assertRaisesRegex(SourceMuxError,'chunk_reserve_unavailable'):r.poll()
        self.assertEqual(len(seen),1)
        self.assertEqual(r.sequence['ref'],0)
        self.assertEqual(r.sequence['main'],-1)
        self.assertEqual(len(m.queue),16)
        self.assertEqual(r.reference_failures,{})

    def test_pressure_stalled_primary_fails_primary_without_masking(self):
        c,r=self.make();m=r.mux;r.degrade_references=True
        frame=np.zeros((2,2,3),np.uint8)
        m.push('ref',0,frame);m.push('main',0,frame);m.pop_ready()
        for i in range(1,16):m.push('ref',float(i),frame)
        with self.assertRaisesRegex(SourceMuxError,'primary_source_pressure_stalled'):r.poll()
        self.assertEqual(r.reference_failures,{})

    def test_bounded_http_rejects_oversized_or_encoded_sources(self):
        import httpx
        from types import SimpleNamespace
        from inference_worker.continuous_source_reader import bounded_chunk
        for response in (httpx.Response(200,content=b'x'*101),
                         httpx.Response(200,content=b'x',headers={'content-encoding':'br'}),
                         httpx.Response(307,headers={'location':'https://other.test'})):
            with httpx.Client(base_url='https://test',transport=httpx.MockTransport(lambda request:response),follow_redirects=False) as client:
                with self.assertRaises(SourceMuxError):bounded_chunk(SimpleNamespace(client=client),'CHUNK_1',100)
