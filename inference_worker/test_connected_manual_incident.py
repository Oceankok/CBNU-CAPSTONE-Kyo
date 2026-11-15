"""Real H264 files through manual journal/capture/dispatcher, fake network only."""
import json
from pathlib import Path
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import patch

import cv2
import httpx

from inference_worker import test_continuous_runtime as fixtures
from inference_worker.multicamera_capture import CameraClock
from inference_worker.continuous_dispatcher import ContinuousDispatcher
from inference_worker.test_legacy_required_episode import person


class ConnectedManualTests(TestCase):
    def setUp(self):
        fixtures.RuntimeTests.setUp(self)
        self.capture.manual_review=True
        self.capture.cameras=MappingProxyType({c:CameraClock(0,0,False) for c in self.capture.cameras})
        self.runtime.journal.review_episode_continuity=True
        self.runtime.journal.evidence_mode='manual_review'
        self.runtime.journal.lifecycle.grace=1.0  # Same as configured manual runner.
        self.commands=[];self.probes=[]
        def handler(request):
            body=json.loads(request.read());self.commands.append((request.url.path,body))
            parts=request.url.path.strip('/').split('/')
            event=body.get('event_id') or parts[3]
            kind='start' if body.get('event_id') else 'manifest' if parts[-1]=='segments' else 'capture_report' if parts[-1]=='capture-reports' else body['kind']
            camera=parts[5] if kind=='capture_report' else None
            rows=self.manifests.setdefault(event,[])
            if kind=='manifest':
                c=body['camera_id']
                rows.append(dict(camera_id=c,sequence=body['sequence'],request_id=event+c+str(body['sequence']),role='clip' if c=='main' else 'reference'))
            return httpx.Response(200,json={'event_id':event,'segments':rows,
                'cameras':[dict(camera_id=c,role='clip' if c=='main' else 'reference') for c in ('main','ref')],
                'accepted_command':dict(kind=kind,session_id=body['session_id'],sequence=body.get('sequence',0),camera_id=camera)})
        self.transport.client.close()
        self.transport.client=httpx.Client(base_url='https://test',transport=httpx.MockTransport(handler))
        upload=self.transport.upload
        def inspect_upload(plan):
            p=self.box.root/plan.relative_path
            cap=cv2.VideoCapture(str(p))
            try:
                self.assertTrue(cap.isOpened())
                duration=cap.get(cv2.CAP_PROP_FRAME_COUNT)/cap.get(cv2.CAP_PROP_FPS)
                self.assertAlmostEqual(duration,plan.capture_end-plan.capture_start,places=5)
                self.probes.append((plan.camera_id,plan.capture_start,plan.capture_end,duration))
            finally:cap.release()
            return upload(plan)
        self.transport.upload=inspect_upload

    def tearDown(self):fixtures.RuntimeTests.tearDown(self)

    def feed(self, *, seam=False, gap=False, rewear=False, missing_pre=False, interrupt_upload=False,early_end=False,interrupt_active=False):
        if missing_pre:
            for q in self.capture.buffers.values():q.clear()
        starts=[];closes=[]
        for step in range(53):
            self.now=step*.5+(.072 if seam and step>=20 else 0)
            if gap and step==22:continue
            wearing=step>=41 or (rewear and 16<=step<=18)
            status='helmet' if wearing else 'suspected_no_helmet'
            row=person(x=60 if step>=12 else 20,statuses={'helmet':status})
            row['ppe'].pop('vest')
            self.runtime.frame('ref',self.now,self.frame)
            transitions=self.runtime.frame('main',self.now,self.frame,assessments=[row])
            starts += [r for r in transitions if r['kind']=='start']
            closes += [r for r in transitions if r['kind']=='close']
            self.runtime.pump(now=self.now)
            if interrupt_upload and self.transport.uploads:return starts,closes
            if early_end and step==40:
                self.runtime.end_source('main');self.runtime.end_source('ref')
                break
            if interrupt_active and step==34:return starts,closes
        for _ in range(80):self.runtime.pump(now=self.now)
        return starts,closes

    def test_twenty_second_incident_has_monotonic_progress_and_playable_both_camera_media(self):
        starts,closes=self.feed()
        self.assertEqual(len(starts),1);self.assertEqual(len(closes),1)
        self.assertEqual(starts[0]['event_id'],closes[0]['event_id'])
        self.assertEqual(closes[0]['end_time'],20.)
        for camera in ('main','ref'):
            rows=[r for r in self.probes if r[0]==camera]
            self.assertTrue(rows)
            self.assertAlmostEqual(sum(r[3] for r in rows),24.)
        updates=[b for path,b in self.commands if path.endswith('/updates')]
        self.assertEqual([b['timestamp_end'] for b in updates],sorted(b['timestamp_end'] for b in updates))
        self.assertEqual(updates[-1]['kind'],'close')
        start=next(b for _,b in self.commands if 'event_id' in b)
        self.assertTrue(start['review_only']);self.assertEqual(start['evidence_mode'],'manual_review')
        reports=[b for path,b in self.commands if path.endswith('/capture-reports')]
        self.assertEqual(len(reports),2)
        self.assertTrue(all(r['time_basis']=='source_utc_unverified' and r['clock_uncertainty_seconds'] is None for r in reports))
        self.assertTrue(all(row['state']=='accepted' for row in self.dispatcher.status()))

    def test_seam_does_not_split_event_and_real_gap_is_explicit_with_available_media(self):
        starts,_=self.feed(seam=True,gap=True,missing_pre=True)
        self.assertEqual(len(starts),1)
        self.assertEqual({r[0] for r in self.probes},{'main','ref'})
        reports=[b for path,b in self.commands if path.endswith('/capture-reports')]
        self.assertTrue(all(r['state']=='incomplete' and r['missing_ranges'] for r in reports))
        self.assertTrue(any('missing_pre_roll'==r['reason'] for r in reports))
        self.assertTrue(any(len(r['captured_ranges'])>1 for r in reports))

    def test_verified_rewear_is_second_incident(self):
        starts,_=self.feed(rewear=True)
        self.assertEqual(len(starts),2)
        self.assertEqual(len({r['event_id'] for r in starts}),2)

    def test_missing_post_roll_retains_available_private_media(self):
        starts,_=self.feed(early_end=True)
        self.assertEqual(len(starts),1)
        self.assertEqual({r[0] for r in self.probes},{'main','ref'})
        reports=[b for path,b in self.commands if path.endswith('/capture-reports')]
        self.assertTrue(all(any(m['reason']=='missing_post_roll' for m in r['missing_ranges']) for r in reports))

    def test_default_review_dispatches_connected_runner_before_detector(self):
        from inference_worker.run_server import main
        with patch('sys.argv',['run','--camera-id','main','--submit-review-candidates']), \
             patch('inference_worker.run_continuous.run',return_value='connected') as connected, \
             patch('inference_worker.run_server.create_detector') as detector:
            self.assertEqual(main(),'connected')
            connected.assert_called_once();detector.assert_not_called()

    def test_lost_upload_ack_recovers_after_reopening_durable_ledgers(self):
        from inference_worker.capture_outbox import CaptureOutbox
        self.transport.lose=True
        self.feed(interrupt_upload=True)
        self.assertTrue(self.transport.uploads)
        self.dispatcher.close();self.box.close()
        self.box=CaptureOutbox(self.root/'journal',private_root=self.root/'raw',max_db_bytes=2000000,
            max_records=300,max_body_bytes=20000,source_ttl_seconds=100,clock=lambda:self.now)
        self.dispatcher=ContinuousDispatcher(self.box,self.transport,enabled=True,
            max_delivery_records=30,max_delivery_db_bytes=2000000)
        self.runtime.outbox=self.box;self.runtime.journal.outbox=self.box;self.runtime.dispatcher=self.dispatcher
        self.transport.lose=False
        for _ in range(80):self.runtime.pump(now=self.now)
        self.assertTrue(all(r['state']=='accepted' for r in self.dispatcher.status()))
        self.assertEqual(len(self.transport.uploads),len(set(self.transport.uploads)))
