"""Future-only finalization using observed frames, real CPU H264, fake HTTP."""
from unittest import TestCase
from unittest.mock import patch
from types import SimpleNamespace

from inference_worker import test_connected_manual_incident as fixtures
from inference_worker.eos_delivery import drain_delivery
from inference_worker.final_capture_boundary import frame_overlap


class FrameOverlapTests(TestCase):
    def test_exact_grid_edge_uses_utc_sample_times(self):
        start=1795169011.780430
        segment=SimpleNamespace(capture_start=start, fps=15, frame_count=21)
        end=start+16/15
        self.assertEqual(frame_overlap(segment,start,end),(0,16))
        self.assertEqual(frame_overlap(segment,start,end-.001),(0,15))


class FinalCaptureBoundaryTests(TestCase):
    def setUp(self): fixtures.ConnectedManualTests.setUp(self)
    def tearDown(self): fixtures.ConnectedManualTests.tearDown(self)

    def prepare_early_timeout(self):
        starts=[]
        for step in range(17):
            self.now=step/2
            self.runtime.frame('ref',self.now,self.frame)
            rows=self.runtime.frame('main',self.now,self.frame,assessments=[{
                'tracking_id':'one-person','ppe':{'helmet':{
                    'status':'suspected_no_helmet' if self.now<=5 else 'unknown'}}}])
            starts.extend(row for row in rows if row['kind']=='start')
            self.runtime.pump(now=self.now)
        for step in range(17,25):
            self.now=step/2
            self.runtime.frame('ref',self.now,self.frame)
        self.assertEqual(len(starts),1)
        event=starts[0]['event_id']
        self.runtime.prepare_stop('primary_arrival_timeout')
        status=self.capture.status(event)
        self.assertEqual(status['end'],7)
        original=list(self.box.db.execute('SELECT id,start,end,path FROM segments WHERE event=?',(event,)))
        self.assertTrue(any(row['start']<7<row['end'] for row in original))
        return event,original

    def test_late_final_end_reconciles_before_manifest_and_drains(self):
        event,original=self.prepare_early_timeout()
        result=drain_delivery(self.runtime)
        self.assertEqual(result['state'],'drained',result)
        manifests=[body for path,body in self.commands if path.endswith('/segments')]
        self.assertTrue(manifests)
        from datetime import datetime
        epoch=lambda value:datetime.fromisoformat(value).timestamp()
        self.assertTrue(all(-2<=epoch(b['capture_start_at'])<epoch(b['capture_end_at'])<=7 for b in manifests))
        close_index=next(i for i,(_,b) in enumerate(self.commands) if b.get('kind')=='close')
        self.assertTrue(all(i<close_index for i,(p,_) in enumerate(self.commands) if p.endswith('/segments')))
        close=self.commands[close_index][1]
        self.assertFalse(close['complete'])
        self.assertEqual(close['timestamp_end'],'1970-01-01T00:00:05.000000+00:00')
        self.assertEqual(close['final_segment_count'],sum(b['camera_id']=='main' for b in manifests))
        self.assertTrue(any(epoch(b['capture_end_at'])==7 for b in manifests))
        self.assertTrue(all(row['state']=='accepted' for row in self.dispatcher.status()))
        retired=self.box.db.execute("SELECT reason FROM segments WHERE reason LIKE 'final_boundary_%'").fetchall()
        self.assertTrue(any(row['reason'].startswith('final_boundary_replaced_') for row in retired))
        self.assertTrue(any(row['reason']=='final_boundary_no_overlap' for row in retired))

    def test_failed_trim_keeps_original_bytes_and_reports_incomplete(self):
        event,original=self.prepare_early_timeout()
        crossing=[r for r in original if r['start']<7<r['end']]
        before={r['id']:self.box._path(r['path']).read_bytes() for r in crossing}
        with patch('inference_worker.final_capture_boundary.trim_segment',side_effect=ValueError('fixture')):
            self.runtime.pump(now=self.now)
        for row in crossing:
            self.assertEqual(self.box._path(row['path']).read_bytes(),before[row['id']])
            self.assertEqual(self.box.segment_status(row['id'])['state'],'present')
        self.assertEqual(self.box.event_status(event)['state'],'incomplete')
        self.assertTrue(any('final_boundary_trim_failed' in cap.reasons
                            for cap in self.capture.events[event]['captures'].values()))
        self.assertFalse(any(row['segment'] in before for row in self.dispatcher.status()))
