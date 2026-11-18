import json,shutil,tempfile
from pathlib import Path
from unittest import TestCase,skipUnless
import httpx,numpy as np
from inference_worker.capture_outbox import CaptureOutbox
from inference_worker.lifecycle_journal import LifecycleJournal
from inference_worker.multicamera_capture import CameraClock,MultiCameraCapture
from inference_worker.continuous_runtime import ContinuousFrameRuntime
from inference_worker.continuous_dispatcher import ContinuousDispatcher
from inference_worker.test_continuous_dispatcher import FakeTransport

@skipUnless(shutil.which('ffmpeg'),'ffmpeg required')
class RuntimeTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.now=0
        self.box=CaptureOutbox(self.root/'journal',private_root=self.root/'raw',max_db_bytes=2000000,
            max_records=300,max_body_bytes=20000,source_ttl_seconds=100,clock=lambda:self.now)
        self.transport=FakeTransport();self.manifests={}
        def handler(request):
            body=json.loads(request.read());parts=request.url.path.strip('/').split('/')
            event=body.get('event_id') or parts[3]
            kind='start' if body.get('event_id') else 'manifest' if parts[-1]=='segments' else 'capture_report' if parts[-1]=='capture-reports' else body['kind']
            camera=parts[5] if kind=='capture_report' else None
            rows=self.manifests.setdefault(event,[])
            if kind=='manifest':
                rows += [dict(camera_id=c,sequence=body['sequence'],request_id=event+c+str(body['sequence']),
                    role='clip' if c=='main' else 'reference') for c in getattr(self,'camera_ids',('main','ref'))]
            return httpx.Response(200,json={'event_id':event,'segments':rows,
                'cameras':[{'camera_id':c,'role':'clip' if c=='main' else 'reference'} for c in getattr(self,'camera_ids',('main','ref'))],
                'accepted_command':dict(kind=kind,session_id=body['session_id'],sequence=body.get('sequence',0),camera_id=camera)})
        self.transport.client=httpx.Client(base_url='https://test',transport=httpx.MockTransport(handler))
        self.dispatcher=ContinuousDispatcher(self.box,self.transport,enabled=True,max_delivery_records=30,max_delivery_db_bytes=2000000)
        self.capture=MultiCameraCapture(self.root/'raw',cameras={c:CameraClock(0,getattr(self,'clock_error',0),True) for c in getattr(self,'camera_ids',('main','ref'))},
            capture_output_fps=getattr(self,'capture_output_fps',None),
            max_clock_error=getattr(self,'clock_error',0),pre_seconds=2,post_seconds=2,discovery_delay_limit=3.5,prebuffer_seconds=6,
            prebuffer_bytes=100000,max_buffer_frames=50,max_events=2,global_spool_bytes=4000000,
            spool_config=dict(max_bytes=1000000,max_segments=20,max_age_seconds=100,max_attempts=3,clock=lambda:self.now),
            writer_config=dict(segment_seconds=2,output_fps=2,max_frame_pixels=256,max_input_gap_seconds=.6,encoder_timeout_seconds=5))
        journal=LifecycleJournal(self.box,camera_id='main',camera_snapshot=list(self.capture.cameras),model_version='test',
            capture_output_fps=getattr(self,'capture_output_fps',None),
            missing_grace_seconds=.6,threshold_seconds=3,max_sample_gap_seconds=.6,progress_emit_interval_seconds=1)
        self.runtime=ContinuousFrameRuntime(journal=journal,capture=self.capture,dispatcher=self.dispatcher,metadata_steps_per_pump=30)
        self.frame=np.zeros((16,16,3),np.uint8)
        # Explicit synthetic pre-roll, independent of whether a person appears.
        for timestamp in (-2,-1.5,-1,-.5):
            if 'ref' in self.capture.cameras:self.runtime.frame('ref',timestamp,self.frame)
            self.runtime.frame('main',timestamp,self.frame,assessments=[])

    def tearDown(self):
        self.runtime.stop('test_stop');self.dispatcher.close();self.transport.client.close();self.box.close();self.temp.cleanup()

    def test_normal_stop_closes_observations_but_captures_real_postroll(self):
        starts=[]
        for step in range(18):
            timestamp=step/2;self.now=timestamp
            if step==11:self.runtime.set_observation_cutoff('main',timestamp)
            self.runtime.frame('ref',timestamp,self.frame)
            rows=self.runtime.frame('main',timestamp,self.frame,assessments=[{'tracking_id':'after' if step>=11 else 'before',
                'ppe':{'helmet':{'status':'suspected_no_helmet'}}}])
            starts.extend(row for row in rows if row['kind']=='start')
            self.runtime.pump(now=timestamp)
        self.assertEqual(len(starts),1)
        event=starts[0]['event_id']
        self.assertFalse(self.runtime.observing('main',8.5))
        for camera in ('main','ref'):self.runtime.end_source(camera)
        for _ in range(20):self.runtime.pump(now=self.now)
        rows=self.box.db.execute('SELECT kind,body FROM requests WHERE event=?',(event,)).fetchall()
        close=json.loads(next(r['body'] for r in rows if r['kind']=='close'))
        self.assertEqual(close['close_reason'],'operator_stop')
        from datetime import datetime
        self.assertEqual(datetime.fromisoformat(close['timestamp_end']).timestamp(),5)
        self.assertTrue(close['complete'])
        reports=[json.loads(r['body']) for r in rows if r['kind']=='capture_report']
        self.assertEqual({r['state'] for r in reports},{'complete'})
        self.assertTrue(all(not r['missing_ranges'] for r in reports))
        self.assertFalse(self.box.pending())

    def test_observation_cutoff_cannot_backdate_or_change(self):
        self.runtime.frame('main',0,self.frame,assessments=[])
        with self.assertRaisesRegex(ValueError,'arrived late'):self.runtime.set_observation_cutoff('main',0)
        self.runtime.set_observation_cutoff('main',1)
        self.runtime.set_observation_cutoff('main',1)
        with self.assertRaisesRegex(ValueError,'changed'):self.runtime.set_observation_cutoff('main',2)
    def feed(self,fail_ref=False):
        starts=[]
        for step in range(18):
            self.now=step/2
            if not(fail_ref and step==11):self.runtime.frame('ref',self.now,self.frame)
            rows=self.runtime.frame('main',self.now,self.frame,assessments=[{'tracking_id':'t',
                'ppe':{'helmet':{'status':'suspected_no_helmet' if step<12 else 'helmet'}}}])
            starts += [r for r in rows if r['kind']=='start']
            self.runtime.pump(now=self.now)
        return starts
    def test_full_frame_lifecycle_spool_dispatch_path(self):
        starts=self.feed();self.assertEqual(len(starts),1)
        for _ in range(20):self.runtime.pump(now=self.now)
        self.assertTrue(self.dispatcher.status())
        self.assertTrue(all(row['state']=='accepted' for row in self.dispatcher.status()))
        self.assertEqual(self.box.pending(),[])
        self.assertEqual(self.box.pending_close_intents()[0]['state'],'finalized')
    def test_missing_preroll_reports_before_scoped_partial_media(self):
        for queue in self.capture.buffers.values():queue.clear()
        # Relative to the observed main event start, the first reference frame
        # arrived 0.273824s earlier; both still miss the required -2s pre-roll.
        starts=[]
        for step in range(18):
            self.now=step/2
            self.runtime.frame('ref',self.now-.273824,self.frame)
            starts += [row for row in self.runtime.frame('main',self.now,self.frame,assessments=[{'tracking_id':'t',
                'ppe':{'helmet':{'status':'suspected_no_helmet' if step<12 else 'helmet'}}}]) if row['kind']=='start']
            self.runtime.pump(now=self.now)
        self.assertEqual(len(starts),1);event=starts[0]['event_id']
        for _ in range(20):self.runtime.pump(now=self.now)
        rows=self.box.db.execute("SELECT seq,kind,body,ack FROM requests WHERE event=? ORDER BY seq",(event,)).fetchall()
        reports=[r for r in rows if r['kind']=='capture_report']
        manifests=[r for r in rows if r['kind']=='manifest']
        self.assertEqual(len(reports),2);self.assertTrue(manifests)
        self.assertLess(max(r['seq'] for r in reports),min(r['seq'] for r in manifests))
        self.assertTrue(all(r['ack'] is not None for r in reports))
        self.assertEqual({json.loads(r['body'])['camera_id'] for r in manifests},{'main','ref'})
        self.assertEqual({row['camera_id'] for row in self.transport.receipts.values()},{'main','ref'})
        for report in reports:
            body=json.loads(report['body']);self.assertEqual(body['state'],'incomplete')
            self.assertTrue(body['captured_ranges']);self.assertTrue(body['missing_ranges'])
            self.assertEqual(body['missing_ranges'][0]['start_at'],'1969-12-31T23:59:58.000000+00:00')
            self.assertEqual(body['missing_ranges'][0]['end_at'],body['captured_ranges'][0]['start_at'])
        close=json.loads(next(r['body'] for r in rows if r['kind']=='incomplete'))
        self.assertFalse(close['complete'])
        self.assertEqual(close['final_segment_count'],sum(json.loads(r['body'])['camera_id']=='main' for r in manifests))

    def test_unacknowledged_incomplete_report_never_publishes_partial_manifest(self):
        for queue in self.capture.buffers.values():queue.clear()
        handler=self.transport.client._transport.handler
        def unavailable(request):
            if request.url.path.endswith('/capture-reports'):
                return httpx.Response(503,json={'detail':'temporarily unavailable'})
            return handler(request)
        self.transport.client._transport.handler=unavailable
        starts=self.feed();self.assertEqual(len(starts),1)
        for _ in range(5):self.runtime.pump(now=self.now)
        event=starts[0]['event_id']
        self.assertTrue(self.box.db.execute("SELECT 1 FROM requests WHERE event=? AND kind='capture_report'",(event,)).fetchone())
        self.assertFalse(self.box.db.execute("SELECT 1 FROM requests WHERE event=? AND kind IN ('manifest','close','incomplete')",(event,)).fetchone())
        self.assertEqual(self.box.pending_close_intents()[0]['state'],'pending')

    def test_reference_source_gap_preserves_valid_fragments_without_complete_success(self):
        starts=self.feed(fail_ref=True);self.assertEqual(len(starts),1)
        self.assertEqual(self.box.event_status(starts[0]['event_id'])['state'],'incomplete')
        for _ in range(20):self.runtime.pump(now=self.now)
        self.assertTrue(self.transport.uploads)
        event=starts[0]['event_id']
        close=json.loads(self.box.db.execute("SELECT body FROM requests WHERE event=? AND kind='incomplete'",(event,)).fetchone()[0])
        self.assertFalse(close['complete'])
        reports=[(r[0].split(':',1)[0],json.loads(r[1])) for r in self.box.db.execute(
            "SELECT idem,body FROM requests WHERE event=? AND kind='capture_report' ORDER BY seq",(event,))]
        main=[body for camera,body in reports if camera=='main']
        reference=[body for camera,body in reports if camera=='ref']
        self.assertEqual([body['state'] for body in main],['collecting','complete'])
        self.assertEqual([body['sequence'] for body in main],[1,2])
        final_ack=self.box.db.execute("SELECT ack FROM requests WHERE event=? AND idem=?",(event,'main:'+event+':report2')).fetchone()
        self.assertIsNotNone(final_ack['ack'])
        self.assertEqual([body['state'] for body in reference],['incomplete'])
        self.assertTrue(all(body['captured_ranges'] for _,body in reports))
        self.assertTrue(reference[0]['missing_ranges'])
        self.assertFalse(main[-1]['missing_ranges'])
        self.assertTrue(self.transport.uploads)
    def test_expired_source_after_collecting_preserves_history_and_reports_unavailable(self):
        handler=self.transport.client._transport.handler;allow_close=[False]
        def hold_close(request):
            body=json.loads(request.read())
            if request.url.path.endswith('/updates') and body.get('kind')=='close' and not allow_close[0]:
                return httpx.Response(503,json={'detail':'temporary'})
            return handler(request)
        self.transport.client._transport.handler=hold_close
        starts=self.feed(fail_ref=True);event=starts[0]['event_id']
        first=self.box.db.execute("SELECT body,ack FROM requests WHERE event=? AND idem=?",(event,'main:'+event+':report1')).fetchone()
        self.assertIsNotNone(first);self.assertIsNotNone(first['ack'])
        first=json.loads(first['body']);self.assertEqual(first['state'],'collecting')
        self.now=200;allow_close[0]=True
        for _ in range(20):self.runtime.pump(now=self.now)
        final=self.box.db.execute("SELECT body,ack FROM requests WHERE event=? AND idem=?",(event,'main:'+event+':report2')).fetchone()
        self.assertIsNotNone(final);self.assertIsNotNone(final['ack'])
        final=json.loads(final['body']);self.assertEqual(final['state'],'incomplete')
        self.assertEqual(final['captured_ranges'],first['captured_ranges'])
        self.assertTrue(final['missing_ranges'])
        self.assertTrue(all(r['reason']=='source_segment_unavailable' for r in final['missing_ranges']))
        self.assertFalse(self.transport.uploads)
        self.assertEqual(self.box.event_status(event)['state'],'incomplete')

    def test_incomplete_event_with_two_healthy_cameras_reports_both_complete(self):
        starts=[]
        for step in range(18):
            self.now=step/2
            self.runtime.frame('ref',self.now,self.frame)
            rows=self.runtime.frame('main',self.now,self.frame,assessments=[{'tracking_id':'t',
                'ppe':{'helmet':{'status':'suspected_no_helmet' if step<12 else 'helmet'}}}])
            for row in rows:
                if row['kind']=='start':
                    starts.append(row);self.box.mark_incomplete(row['event_id'],reason='observation_interrupted')
            self.runtime.pump(now=self.now)
        self.assertEqual(len(starts),1);event=starts[0]['event_id']
        for _ in range(20):self.runtime.pump(now=self.now)
        rows=self.box.db.execute("SELECT idem,kind,body,ack FROM requests WHERE event=? ORDER BY seq",(event,)).fetchall()
        for camera in ('main','ref'):
            reports=[json.loads(r['body']) for r in rows if r['kind']=='capture_report' and r['idem'].startswith(camera+':')]
            self.assertEqual([r['state'] for r in reports],['collecting','complete'])
            self.assertTrue(all(not r['missing_ranges'] for r in reports))
            self.assertTrue(all(r['ack'] is not None for r in rows if r['kind']=='capture_report' and r['idem'].startswith(camera+':')))
        manifests=[json.loads(r['body']) for r in rows if r['kind']=='manifest']
        self.assertTrue(manifests);self.assertTrue(all('camera_id' not in m for m in manifests))
        close=json.loads(next(r['body'] for r in rows if r['kind']=='incomplete'))
        self.assertFalse(close['complete']);self.assertEqual(self.box.event_status(event)['state'],'incomplete')

    def test_partial_complete_dispatch_rejects_missing_ack_prerequisites(self):
        from unittest.mock import patch
        starts=self.feed(fail_ref=True);event=starts[0]['event_id']
        for _ in range(20):self.runtime.pump(now=self.now)
        target=self.box.db.execute("SELECT * FROM requests WHERE event=? AND idem=?",(event,'main:'+event+':report2')).fetchone()
        self.assertIsNotNone(target['ack'])
        prerequisites=[self.box.db.execute("SELECT seq,ack FROM requests WHERE event=? AND idem=?",(event,'main:'+event+':report1')).fetchone(),
                       self.box.db.execute("SELECT seq,ack FROM requests WHERE event=? AND kind='incomplete'",(event,)).fetchone()]
        for prerequisite in prerequisites:
            with self.subTest(sequence=prerequisite['seq']):
                with self.box.transaction():self.box.db.execute('UPDATE requests SET ack=NULL WHERE event=? AND seq=?',(event,prerequisite['seq']))
                with patch.object(self.box,'pending',return_value=[target]):
                    self.assertEqual(self.dispatcher.metadata_once(),[(event,'capture_completion_blocked')])
                with self.box.transaction():self.box.db.execute('UPDATE requests SET ack=? WHERE event=? AND seq=?',(prerequisite['ack'],event,prerequisite['seq']))

    def test_stop_journals_interruption_and_releases_raw_prebuffers(self):
        for step in range(11):
            self.now=step/2
            self.runtime.frame('ref',self.now,self.frame)
            self.runtime.frame('main',self.now,self.frame,assessments=[{'tracking_id':'t','ppe':{'helmet':{'status':'suspected_no_helmet'}}}])
        self.runtime.stop('stream_changed')
        self.assertTrue(all(not q for q in self.capture.buffers.values()))
        self.assertEqual(self.box.db.execute('SELECT state FROM events').fetchone()[0],'incomplete')

    def test_manifest_network_time_expiry_rechecks_before_complete_close(self):
        handler=self.transport.client._transport.handler
        expired=[False]
        def delayed(request):
            response=handler(request)
            if request.url.path.endswith('/segments') and not expired[0]:
                self.now=200;expired[0]=True
            return response
        self.transport.client._transport.handler=delayed
        starts=self.feed()
        for _ in range(20):self.runtime.pump(now=self.now)
        self.assertTrue(expired[0])
        self.assertEqual(self.box.event_status(starts[0]['event_id'])['state'],'incomplete')
        self.assertEqual(self.transport.uploads,[])

    def test_shorter_outbox_ttl_finalizes_incomplete_without_reviving_spool(self):
        self.box.ttl=1
        starts=self.feed()
        for _ in range(20):self.runtime.pump(now=self.now)
        self.assertEqual(self.box.event_status(starts[0]['event_id'])['state'],'incomplete')
        self.assertEqual(self.box.pending_close_intents()[0]['state'],'finalized')
        self.assertEqual(self.transport.uploads,[])

    def test_reference_eos_does_not_turn_primary_violation_into_normal(self):
        self.capture.end_source('ref')  # No reference prebuffer: explicitly missing, not normal PPE.
        transitions=[]
        for step in range(14):
            self.now=step/2
            transitions += self.runtime.frame('main',self.now,self.frame,assessments=[{
                'tracking_id':'t','ppe':{'helmet':{'status':'suspected_no_helmet'}}}])
        starts=[r for r in transitions if r['kind']=='start']
        self.assertEqual(len(starts),1)
        self.assertFalse(any(r['kind']=='close' for r in transitions))
        event=starts[0]['event_id']
        self.assertIn('source_ended',self.capture.status(event)['cameras']['ref']['reasons'])
        self.runtime.stop('session_stopped')
        self.assertEqual(self.box.event_status(event)['state'],'incomplete')
        self.assertTrue(self.box.pending())

    def test_primary_eos_closes_at_last_primary_frame_before_reference_drain(self):
        for step in range(11):
            self.now=step/2
            self.runtime.frame('ref',self.now,self.frame)
            self.runtime.frame('main',self.now,self.frame,assessments=[{
                'tracking_id':'t','ppe':{'helmet':{'status':'suspected_no_helmet'}}}])
        self.runtime.end_source('main')
        intents=self.box.pending_close_intents()
        self.assertEqual(len(intents),1)
        self.assertEqual(self.runtime.journal.api_sequences,{})
        event=intents[0]['event']
        self.assertEqual(self.capture.status(event)['end'],7)
        self.runtime.frame('ref',5.5,self.frame)
        self.assertEqual(self.capture.status(event)['end'],7)
        self.runtime.end_source('ref','reference_drain_cutoff')
        self.assertIn('reference_drain_cutoff',self.capture.status(event)['cameras']['ref']['reasons'])
        self.assertFalse(self.capture.status(event)['complete'])
        self.assertEqual(len(self.box.pending_close_intents()),1)

    def test_reference_pressure_is_durable_incomplete_capture_evidence(self):
        from inference_worker import test_continuous_source_reader as fixtures
        c,r=fixtures.ReaderTests().make();r.degrade_references=True
        r.streams={'main':'a','ref':'b'};r.sequence={'main':-1,'ref':-1}
        frame=np.zeros((2,2,3),np.uint8)
        r.mux.push('main',0,frame);r.mux.push('ref',0,frame);r.mux.pop_ready()
        for i in range(1,16):r.mux.push('main',i,frame)
        r.poll();metadata=r.reference_failures['ref']
        for step in range(11):
            self.now=step/2
            self.runtime.frame('ref',self.now,self.frame)
            self.runtime.frame('main',self.now,self.frame,assessments=[{
                'tracking_id':'t','ppe':{'helmet':{'status':'suspected_no_helmet'}}}])
        self.box.record_reference_failure(metadata)
        self.runtime.end_source('ref','source_failed')
        event=self.box.db.execute('SELECT id FROM events').fetchone()[0]
        status=self.capture.status(event)
        self.assertFalse(status['complete'])
        self.assertIn('source_failed',status['cameras']['ref']['reasons'])
        stored=json.loads(self.box.db.execute('SELECT metadata FROM reference_failures').fetchone()[0])
        self.assertEqual(stored['failure_code'],'reference_watermark_pressure')

    def test_expired_bound_source_keeps_durable_incomplete_and_failure_metadata(self):
        from inference_worker import test_processor_capture_context as context_tests
        from inference_worker.continuous_source_reader import SourceUnavailableError
        for step in range(11):
            self.now=step/2
            self.runtime.frame('ref',self.now,self.frame)
            self.runtime.frame('main',self.now,self.frame,assessments=[{
                'tracking_id':'t','ppe':{'helmet':{'status':'suspected_no_helmet'}}}])
        c,r=context_tests.ContextReaderTests().make();r.poll();c.list_streams=lambda:[]
        c.stream_availability=lambda stream,after:dict(c.streams[0],last_sequence=1,
            next_sequence=after+1,next_chunk_state='unavailable',reason='raw_expired')
        with self.assertRaises(SourceUnavailableError) as caught:r.poll()
        error=caught.exception
        metadata={key:getattr(error,key) for key in
            ('reason','camera','stream','expected_sequence','availability_reason')}
        self.box.record_source_failure(metadata)
        self.runtime.stop(str(error))
        event=self.box.db.execute('SELECT state,reason FROM events').fetchone()
        self.assertEqual(tuple(event),('incomplete','source_chunk_unavailable'))
        self.assertEqual(json.loads(self.box.db.execute('SELECT metadata FROM source_failures').fetchone()[0]),metadata)
        self.assertTrue(self.box.pending_close_intents())
