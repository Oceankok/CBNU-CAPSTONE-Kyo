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
                    role='clip' if c=='main' else 'reference') for c in ('main','ref')]
            return httpx.Response(200,json={'event_id':event,'segments':rows,
                'cameras':[{'camera_id':c,'role':'clip' if c=='main' else 'reference'} for c in ('main','ref')],
                'accepted_command':dict(kind=kind,session_id=body['session_id'],sequence=body.get('sequence',0),camera_id=camera)})
        self.transport.client=httpx.Client(base_url='https://test',transport=httpx.MockTransport(handler))
        self.dispatcher=ContinuousDispatcher(self.box,self.transport,enabled=True,max_delivery_records=30,max_delivery_db_bytes=2000000)
        self.capture=MultiCameraCapture(self.root/'raw',cameras={c:CameraClock(0,0,True) for c in ('main','ref')},
            max_clock_error=0,pre_seconds=2,post_seconds=2,discovery_delay_limit=3.5,prebuffer_seconds=6,
            prebuffer_bytes=100000,max_buffer_frames=50,max_events=2,global_spool_bytes=4000000,
            spool_config=dict(max_bytes=1000000,max_segments=20,max_age_seconds=100,max_attempts=3,clock=lambda:self.now),
            writer_config=dict(segment_seconds=2,output_fps=2,max_frame_pixels=256,max_input_gap_seconds=.6,encoder_timeout_seconds=5))
        journal=LifecycleJournal(self.box,camera_id='main',camera_snapshot=['main','ref'],model_version='test',
            missing_grace_seconds=.6,threshold_seconds=3,max_sample_gap_seconds=.6,progress_emit_interval_seconds=1)
        self.runtime=ContinuousFrameRuntime(journal=journal,capture=self.capture,dispatcher=self.dispatcher,metadata_steps_per_pump=30)
        self.frame=np.zeros((16,16,3),np.uint8)
    def tearDown(self):
        self.runtime.stop('test_stop');self.dispatcher.close();self.transport.client.close();self.box.close();self.temp.cleanup()
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
    def test_reference_source_gap_preserves_valid_fragments_without_complete_success(self):
        starts=self.feed(fail_ref=True);self.assertEqual(len(starts),1)
        self.assertEqual(self.box.event_status(starts[0]['event_id'])['state'],'incomplete')
        for _ in range(20):self.runtime.pump(now=self.now)
        self.assertTrue(self.transport.uploads)
        event=starts[0]['event_id']
        close=json.loads(self.box.db.execute("SELECT body FROM requests WHERE event=? AND kind='incomplete'",(event,)).fetchone()[0])
        self.assertFalse(close['complete'])
        reports=[json.loads(r[0]) for r in self.box.db.execute("SELECT body FROM requests WHERE event=? AND kind='capture_report'",(event,))]
        self.assertEqual(len(reports),2)
        self.assertTrue(all(r['state']=='incomplete' for r in reports))
        self.assertTrue(all(r['captured_ranges'] for r in reports))
        self.assertTrue(any(r['missing_ranges'] for r in reports))
        self.assertTrue(any(not r['missing_ranges'] for r in reports))
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
