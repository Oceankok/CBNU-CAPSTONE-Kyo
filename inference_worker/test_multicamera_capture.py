import shutil
import tempfile
import unittest
from pathlib import Path
import numpy as np
from inference_worker.multicamera_capture import CameraClock, MultiCameraCapture


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
class MultiCameraTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.frame = np.zeros((16,16,3), np.uint8)
        self.now = [100.]
        self.c = None

    def tearDown(self):
        if self.c:
            self.c.close()
        self.temp.cleanup()

    def build(self, **overrides):
        config = dict(cameras={'main':CameraClock(0,.01,True), 'ref':CameraClock(-10,.01,True)},
                      max_clock_error=.02, pre_seconds=2, post_seconds=2,
                      discovery_delay_limit=3, prebuffer_seconds=5, prebuffer_bytes=100_000,
                      max_buffer_frames=200, max_events=3, global_spool_bytes=400_000,
                      spool_config=dict(max_bytes=100_000,max_segments=10,max_age_seconds=100,
                                        max_attempts=2,clock=lambda:self.now[0]),
                      writer_config=dict(segment_seconds=2,output_fps=2,max_frame_pixels=256,
                                         max_input_gap_seconds=.6,encoder_timeout_seconds=5))
        config.update(overrides)
        self.c = MultiCameraCapture(Path(self.temp.name)/'raw', **config)
        return self.c

    def feed(self, begin, end):
        for i in range(begin,end):
            self.c.ingest('main',i/2,self.frame)
            self.c.ingest('ref',i/2+10,self.frame)

    def start(self):
        return self.c.discover('e',start=2,discovered_at=5,camera_snapshot=['main','ref'])

    def test_two_camera_exact_full_interval(self):
        self.build(); self.feed(0,11); self.start()
        self.c.end('e',violation_end=6)
        self.feed(11,17)
        result=self.c.status('e')
        self.assertTrue(result['complete'])
        for camera in result['cameras'].values():
            rows=camera['segments']
            self.assertEqual([(s.capture_start,s.capture_end) for s in rows],[(0,2),(2,4),(4,6),(6,8)])
            self.assertEqual(sum(s.frame_count for s in rows),16)
        with self.assertRaises(ValueError): self.c.release('e')

    def test_missing_prebuffer_and_late_discovery(self):
        self.build(); self.feed(1,11)
        result=self.start()
        self.assertFalse(result['complete'])
        self.assertIn('missing_prebuffer_or_discovery_order',result['cameras']['ref']['reasons'])
        late=self.c.discover('late',start=2,discovered_at=6,camera_snapshot=['main'])
        self.assertIn('late_discovery',late['cameras']['main']['reasons'])

    def test_clock_skew(self):
        self.build(cameras={'main':CameraClock(0,.5,True),'ref':CameraClock(-10,.01,False)})
        self.feed(0,11)
        result=self.start()
        for cap in result['cameras'].values(): self.assertIn('clock_unverified_or_skew',cap['reasons'])

    def test_admission_and_overlap(self):
        self.build(global_spool_bytes=200_000); self.feed(0,11); self.start()
        second=self.c.discover('second',start=2,discovered_at=5,camera_snapshot=['main'])
        self.assertIn('global_spool_admission',second['cameras']['main']['reasons'])
        self.assertEqual(self.c.reserved,200_000)

    def test_late_end_never_claims_complete(self):
        self.build(); self.feed(0,11); self.start(); self.feed(11,19)
        result=self.c.end('e',violation_end=6)
        self.assertFalse(result['complete'])
        self.assertIn('end_arrived_after_capture_boundary',result['cameras']['main']['reasons'])

    def test_source_gap_and_ttl(self):
        self.build(); self.feed(0,11); self.start()
        self.c.ingest('main',6,self.frame)
        self.assertTrue(self.c.status('e')['cameras']['main']['reasons'])
        self.now[0]=201
        self.c.maintain(now=6)
        self.assertFalse(self.c.status('e')['complete'])
        self.assertTrue(self.c.status('e')['cameras']['ref']['reasons'])

    def test_buffer_byte_time_bounds(self):
        self.build(prebuffer_bytes=1600,max_buffer_frames=3)
        self.feed(0,20)
        self.assertLessEqual(sum(f.nbytes for q in self.c.buffers.values() for _,f in q),1600)
        self.assertFalse(self.start()['complete'])

    def test_missing_postroll_and_offgrid_end(self):
        self.build(); self.feed(0,11); self.start()
        self.c.end('e',violation_end=6.1); self.feed(11,18)
        self.assertFalse(self.c.status('e')['complete'])
        self.assertTrue(self.c.status('e')['cameras']['main']['reasons'])

    def test_release_keeps_quota_until_media_and_receipts_drained(self):
        self.build(); self.feed(0,11); self.start(); self.c.end('e',violation_end=6); self.feed(11,17)
        for cap in self.c.events['e']['captures'].values():
            for seg in cap.spool.pending(): cap.spool.acknowledge(seg.segment_id)
        self.c.release('e')
        self.assertEqual(self.c.reserved,0)
        self.assertFalse(list(self.c.root.iterdir()))

    def test_idle_buffer_expires_and_close_clears_frames(self):
        self.build(); self.feed(0,11)
        self.c.maintain(now=11)
        self.assertTrue(all(not q for q in self.c.buffers.values()))
        self.feed(23,25)
        self.c.close()
        self.assertTrue(all(not q for q in self.c.buffers.values()))

    def test_asynchronous_phase_uses_predecessor_not_future(self):
        self.build(prebuffer_seconds=6)
        for i in range(11):
            self.c.ingest('main',i/2,self.frame)
            self.c.ingest('ref',i/2+9.9,self.frame)
        result=self.start()
        seed=result['cameras']['ref']['seed']
        self.assertAlmostEqual(seed['source_sample_at'],-.1)
        self.assertEqual(seed['output_sample_at'],0)
        self.assertAlmostEqual(seed['hold_seconds'],.1)
        self.assertEqual(seed['clock_error_bound'],.01)
        self.c.end('e',violation_end=6)
        for i in range(11,18):
            self.c.ingest('main',i/2,self.frame)
            self.c.ingest('ref',i/2+9.9,self.frame)
        result=self.c.status('e')
        self.assertTrue(result['complete'])
        for cap in result['cameras'].values():
            self.assertEqual(sum(s.frame_count for s in cap['segments']),16)
            self.assertEqual(cap['segments'][0].capture_start,0)
            self.assertEqual(cap['segments'][-1].capture_end,8)

    def test_future_or_stale_seed_rejected(self):
        self.build(prebuffer_seconds=6)
        self.c.ingest('main',.1,self.frame)
        self.c.ingest('ref',9,self.frame)
        result=self.start()
        for cap in result['cameras'].values():
            self.assertIn('missing_prebuffer_or_discovery_order',cap['reasons'])

    def test_seed_does_not_hide_original_source_gap(self):
        self.build(prebuffer_seconds=6)
        self.c.ingest('main',-.4,self.frame)
        self.c.ingest('main',.4,self.frame)
        result=self.c.discover('e',start=2,discovered_at=5,camera_snapshot=['main'])
        self.assertTrue(result['cameras']['main']['reasons'])
        self.assertFalse(result['complete'])

    def test_eos_marks_current_and_future_reference_evidence_incomplete(self):
        self.build();self.feed(0,11);self.start()
        self.c.end_source('ref')
        status=self.c.status('e')
        self.assertIn('source_ended',status['cameras']['ref']['reasons'])
        self.assertFalse(status['complete'])
        self.assertTrue(status['cameras']['ref']['done'])
        future=self.c.discover('future',start=2,discovered_at=5,camera_snapshot=['main','ref'])
        self.assertIn('source_ended',future['cameras']['ref']['reasons'])
        with self.assertRaisesRegex(ValueError,'frame_after_source_end'):
            self.c.ingest('ref',16,self.frame)
