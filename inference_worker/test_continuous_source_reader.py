from unittest import TestCase
import numpy as np
from inference_worker.continuous_source_mux import SourceMux,SourceMuxError
from inference_worker.continuous_source_reader import ContinuousSourceReader

class ReaderTests(TestCase):
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

    def test_bounded_http_rejects_oversized_or_encoded_sources(self):
        import httpx
        from types import SimpleNamespace
        from inference_worker.continuous_source_reader import bounded_chunk
        for response in (httpx.Response(200,content=b'x'*101),
                         httpx.Response(200,content=b'x',headers={'content-encoding':'br'}),
                         httpx.Response(307,headers={'location':'https://other.test'})):
            with httpx.Client(base_url='https://test',transport=httpx.MockTransport(lambda request:response),follow_redirects=False) as client:
                with self.assertRaises(SourceMuxError):bounded_chunk(SimpleNamespace(client=client),'CHUNK_1',100)
