"""Synthetic frames only; no camera, model, network or production state."""
from datetime import datetime, timezone
import shutil
import unittest
import json
from backend.capture_grid import required_capture_end
from inference_worker.continuous_payloads import capture_payload
from inference_worker.multicamera_capture import CameraClock
from inference_worker import test_multicamera_capture as fixtures
from inference_worker import test_continuous_runtime as runtime_fixtures


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
class CaptureOutputGridTests(unittest.TestCase):
    setUp = fixtures.MultiCameraTests.setUp
    tearDown = fixtures.MultiCameraTests.tearDown
    build = fixtures.MultiCameraTests.build

    def prepare(self, origin, violation_end):
        self.build(cameras={'main':CameraClock(0,0,True)}, max_clock_error=0,
            capture_output_fps=15, prebuffer_seconds=6,
            writer_config=dict(segment_seconds=2,output_fps=15,max_frame_pixels=256,
                               max_input_gap_seconds=.25,encoder_timeout_seconds=5))
        for i in range(-1,76):
            self.c.ingest('main',origin+i/15,self.frame)
        self.c.discover('e',start=origin+2,discovered_at=origin+5,camera_snapshot=['main'])
        self.c.end('e',violation_end=violation_end)

    def test_actual_offgrid_intervals_complete_only_with_real_grid_lookahead(self):
        for origin, nominal_end in [
            (1794758451.614161, 1794758463.144494),
            (1794758482.7284942, 1794758497.736494),
            (1794758506.1364942, 1794758524.6041613),
            (1794758523.0618272, 1794758539.5304942),
        ]:
            with self.subTest(origin=origin):
                self.prepare(origin,nominal_end-2)
                end=self.c.events['e']['end']
                self.assertGreaterEqual(end,nominal_end)
                self.assertLess(end-nominal_end,1/15)
                # Independent source phase within the unchanged gap policy.
                i=76
                while origin+i/15+.008 < end:
                    self.c.ingest('main',origin+i/15+.008,self.frame)
                    i+=1
                self.assertFalse(self.c.status('e')['complete'])
                self.c.ingest('main',origin+i/15+.008,self.frame)
                status=self.c.status('e')
                self.assertTrue(status['complete'], status)
                segments=status['cameras']['main']['segments']
                self.assertEqual(segments[-1].capture_end,end)
                report=capture_payload(status,camera_id='main',clock=CameraClock(0,0,True),
                    session_id='a'*32,sequence=1,time_basis='source_utc_corrected',required_end=end)
                self.assertEqual(report['state'],'complete')
                expected=required_capture_end(datetime.fromtimestamp(origin+2,tz=timezone.utc),
                    datetime.fromtimestamp(nominal_end-2,tz=timezone.utc),15)
                self.assertEqual(datetime.fromtimestamp(end,tz=timezone.utc),expected)
                # Each subcase owns an independent empty private spool root.
                self.c.close();self.c=None
                self.temp.cleanup();self.setUp()

    def test_no_lookahead_eos_remains_incomplete(self):
        self.prepare(0,6.008)
        for i in range(76,121):self.c.ingest('main',i/15,self.frame)
        self.c.ingest('main',8.02,self.frame)
        self.assertFalse(self.c.status('e')['complete'])
        self.c.end_source('main')
        status=self.c.status('e')
        self.assertFalse(status['complete'])
        self.assertIn('source_ended',status['cameras']['main']['reasons'])

    def test_real_input_gap_crossing_extended_boundary_stays_incomplete(self):
        self.prepare(0,6.008)
        for i in range(76,121):self.c.ingest('main',i/15,self.frame)
        self.c.ingest('main',8.5,self.frame)
        status=self.c.status('e')
        self.assertFalse(status['complete'])
        cap=status['cameras']['main']
        self.assertIn('source_input_gap',cap['reasons'])
        self.assertTrue(cap['segments'])
        self.assertLessEqual(max(segment.capture_end for segment in cap['segments']),8+1/15+1e-6)
        self.assertLess(max(segment.capture_end for segment in cap['segments']),8.5)


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
class GridRuntimeTests(unittest.TestCase):
    capture_output_fps=2
    setUp=runtime_fixtures.RuntimeTests.setUp
    tearDown=runtime_fixtures.RuntimeTests.tearDown

    def test_jittered_source_end_dispatches_unchanged_observation_and_real_extended_coverage(self):
        starts=[]
        for step in range(18):
            self.now=step/2+(.008 if step>=8 else 0)
            self.runtime.frame('ref',self.now,self.frame)
            rows=self.runtime.frame('main',self.now,self.frame,assessments=[{'tracking_id':'t',
                'ppe':{'helmet':{'status':'suspected_no_helmet' if step<12 else 'helmet'}}}])
            starts += [r for r in rows if r['kind']=='start']
            self.runtime.pump(now=self.now)
        for _ in range(20):self.runtime.pump(now=self.now)
        self.assertEqual(len(starts),1)
        event=starts[0]['event_id']
        bodies={kind:json.loads(body) for kind,body in self.box.db.execute(
            "SELECT kind,body FROM requests WHERE event=? AND kind IN ('start','close')",(event,))}
        self.assertEqual(bodies['start']['capture_output_fps'],2)
        self.assertTrue(bodies['close']['complete'])
        self.assertEqual(datetime.fromisoformat(bodies['close']['timestamp_end']).timestamp(),5.508)
        status=self.capture.status(event)
        self.assertTrue(status['complete'])
        self.assertEqual(status['end'],8)
        self.assertTrue(all(row['state']=='accepted' for row in self.dispatcher.status()))
