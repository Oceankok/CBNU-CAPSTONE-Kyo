from unittest import TestCase
import numpy as np
from inference_worker.continuous_source_mux import SourceMux,SourceMuxError

class MuxTests(TestCase):
    def make(self,**kw):
        args=dict(camera_ids=['main','ref'],primary_camera='main',max_bytes=10000,
            max_frames=20,max_frame_pixels=16,max_clock_error=.1,clock_error_by_camera={'main':0,'ref':.1})
        args.update(kw);return SourceMux(**args)
    def frame(self):return np.zeros((2,2,3),np.uint8)
    def test_two_camera_order_and_reference_before_qualification(self):
        m=self.make()
        for t in (0,1,2):m.push('main',t,self.frame())
        self.assertEqual(m.pop_ready(),[])
        for t in (0,.5,1,1.5,2):m.push('ref',t,self.frame())
        output=m.pop_ready()
        self.assertEqual([t for _,t,_ in output],sorted(t for _,t,_ in output))
        self.assertEqual([c for c,t,_ in output if t==2],['ref','main'])
        self.assertEqual(m.bytes,0)
    def test_caps_and_stall_fail_without_silent_drop(self):
        m=self.make(max_frames=1);m.push('main',0,self.frame())
        with self.assertRaises(SourceMuxError):m.push('main',1,self.frame())
        self.assertEqual(len(m.queue),1)
        m=self.make();m.push('main',0,self.frame())
        with self.assertRaises(SourceMuxError):m.check_stall(now=2,max_lag_seconds=1)
        m.close();self.assertEqual(m.bytes,0)
    def test_regression_and_clock_policy(self):
        m=self.make();m.push('main',1,self.frame())
        with self.assertRaises(SourceMuxError):m.push('main',.5,self.frame())
        with self.assertRaises(ValueError):self.make(clock_error_by_camera={'main':0,'ref':2})
    def test_eos_releases_asymmetric_tail_in_order_and_rejects_reopen(self):
        m=self.make()
        for t in (0,1,2):m.push('main',t,self.frame())
        m.push('ref',0,self.frame())
        first=m.pop_ready();self.assertEqual(len(first),2)
        m.end_source('ref')
        tail=m.pop_ready();self.assertEqual([t for _,t,_ in tail],[1,2])
        m.check_stall(now=2,max_lag_seconds=1)
        with self.assertRaisesRegex(SourceMuxError,'frame_after_source_end'):m.push('ref',3,self.frame())
        m.end_source('main');self.assertEqual(m.pop_ready(),[])
        self.assertEqual(m.drained_sources(),{'main','ref'})

    def test_eos_empty_camera_and_remaining_camera_stall(self):
        m=self.make();m.push('main',1,self.frame());m.end_source('ref')
        self.assertEqual(len(m.pop_ready()),1)
        with self.assertRaisesRegex(SourceMuxError,'stalled'):m.check_stall(now=3,max_lag_seconds=1)

    def test_final_queued_frames_remain_ordered_until_all_eos(self):
        m=self.make()
        for t in (0,1,2):m.push('ref',t,self.frame())
        m.push('main',0,self.frame());m.end_source('ref')
        self.assertEqual(len(m.pop_ready()),2)
        self.assertEqual(m.drained_sources(),set())
        m.end_source('main')
        self.assertEqual([t for _,t,_ in m.pop_ready()],[1,2])
        self.assertEqual(m.bytes,0)
