from copy import deepcopy
import unittest
import shutil
import httpx
from types import SimpleNamespace
from clock_contract import ClockPolicy, sample_interval
from inference_worker.clock_leases import ClockLeaseSet,ClockLeaseError
from inference_worker.event_lifecycle import EventLifecycle

POLICY=ClockPolicy(10,1,100,.005,.25,.05)
BINDING=dict(source_node_id='node',camera_id='main',stream_id='stream',capture_context_id='context',
             capture_session_id='capture-session',run_id='run',epoch='epoch')


class ClockLeaseTests(unittest.TestCase):
    def setUp(self):
        self.now=100.; self.wall=100.; self.seq=1; self.offset=2.5; self.delay=0; self.status=200
        self.proof=self.make_proof()
        def handler(request):
            proof=self.proof
            if 'proof_id' in request.url.params:proof=self.history[request.url.params['proof_id']]
            self.now+=self.delay;self.wall+=self.delay
            return httpx.Response(self.status,json=dict(version=1,server_time=self.now,server_epoch='server',server_boot_id='boot',proof=proof))
        self.client=SimpleNamespace(server_url='https://test',client=httpx.Client(base_url='https://test',transport=httpx.MockTransport(handler)))
        self.addCleanup(self.client.client.close)
        self.config=dict(version=1,origin='https://test',server_epoch='server',policy=POLICY.public(),
            bindings={'main':BINDING},poll_seconds=2,max_host_rtt_seconds=.5)
        self.clock=dict(offset=2.5,error_bound=.1,verified=True,epoch='common',valid_until='2000-01-01T00:00:00Z')
        self.leases=ClockLeaseSet(self.client,self.config,{'main':self.clock},wall=lambda:self.wall,mono=lambda:self.now)
        self.history={self.proof['proof_id']:deepcopy(self.proof)}

    def make_proof(self):
        server=self.now; sent=server-self.offset-.01;received=server-self.offset+.01
        low,high=sample_interval(server,sent,received,server-.01,server+.01,POLICY)
        return dict(version=1,proof_id='proof-'+str(self.seq),sample_sequence=self.seq,
            **BINDING,server_epoch='server',server_boot_id='boot',server_sample=server,source_sent_at=sent,source_received_at=received,
            monotonic_sent=server-.01,monotonic_received=server+.01,offset_low=low,offset_high=high,
            valid_until=server+POLICY.ttl_seconds,policy=POLICY.public())

    def advance(self,seconds):self.now+=seconds;self.wall+=seconds
    def renew(self):
        self.seq+=1;self.proof=self.make_proof();self.history[self.proof['proof_id']]=deepcopy(self.proof)
        self.leases.refresh(force=True)

    def test_multiple_renewals_preserve_one_episode_and_frozen_mapping(self):
        self.leases.refresh(force=True)
        lifecycle=EventLifecycle(camera_id='main',threshold_seconds=3,missing_grace_seconds=.6,
            max_sample_gap_seconds=.6)
        transitions=[]
        for step in range(80):
            if step:self.advance(.5)
            if step and step%8==0:self.renew()
            self.leases.check()
            transitions+=lifecycle.update([{'tracking_id':'same-person','ppe':{'helmet':{'status':'suspected_no_helmet'}}}],self.now)
        self.assertEqual(len([r for r in transitions if r['kind']=='start']),1)
        self.assertFalse(any(r['kind']=='close' for r in transitions))
        self.assertEqual(self.leases.clocks['main'],self.clock)
        self.assertGreater(self.leases.latest['main']['sample_sequence'],5)

    def test_changed_offset_epoch_server_epoch_and_replay_fail_closed(self):
        for change in ('offset','epoch','server_epoch','replay'):
            with self.subTest(change=change):
                self.setUp();self.leases.refresh(force=True);self.advance(2)
                if change=='offset':self.offset+=.2;self.seq+=1;self.proof=self.make_proof()
                elif change=='epoch':self.proof=dict(self.proof,epoch='new')
                elif change=='server_epoch':self.proof=dict(self.proof,server_epoch='new')
                else:self.proof=dict(self.proof,sample_sequence=0)
                with self.assertRaises(ClockLeaseError):self.leases.refresh(force=True)

    def test_network_failure_never_renews_deadline_and_expiry_during_io(self):
        self.leases.refresh(force=True);deadline=self.leases.deadlines['main']
        self.status=500;self.advance(2);self.leases.refresh(force=True)
        self.assertEqual(self.leases.deadlines['main'],deadline)
        self.advance(9)
        with self.assertRaisesRegex(ClockLeaseError,'expired'):self.leases.refresh(force=True)

    def test_processing_clock_step_and_slow_blocking_io_fail_closed(self):
        self.leases.refresh(force=True);self.wall+=1
        with self.assertRaisesRegex(ClockLeaseError,'step'):self.leases.check()
        self.setUp();self.delay=1
        with self.assertRaisesRegex(ClockLeaseError,'rtt'):self.leases.refresh(force=True)

    def test_chunk_proof_requires_capture_interval_and_context_binding(self):
        self.leases.refresh(force=True)
        from datetime import datetime,timezone
        chunk=dict(clock_proof_id=self.proof['proof_id'],capture_start_at=datetime.fromtimestamp(98,timezone.utc).isoformat(),
                   duration_ms=1000,capture_context_id='context',capture_session_id='capture-session',
                   stream_id='stream',clock_proof_version=1,clock_epoch='epoch',clock_run_id='run')
        self.leases.validate_chunk('main',chunk)
        with self.assertRaises(ClockLeaseError):self.leases.validate_chunk('main',dict(chunk,capture_session_id='other'))
        with self.assertRaises(ClockLeaseError):self.leases.validate_chunk('main',dict(chunk,capture_start_at=datetime.fromtimestamp(90,timezone.utc).isoformat()))

    def test_reference_expiry_degrades_only_reference_primary_keeps_renewing(self):
        reference=dict(BINDING,camera_id='ref',stream_id='reference-stream',epoch='reference-epoch')
        config=deepcopy(self.config);config['bindings']['ref']=reference
        reference_proof=dict(self.proof,**reference,proof_id='reference-proof')
        def handler(request):
            proof=reference_proof if 'reference-stream' in request.url.path else self.proof
            return httpx.Response(200,json=dict(version=1,server_time=self.now,server_epoch='server',server_boot_id='boot',proof=proof))
        self.client.client._transport=httpx.MockTransport(handler)
        leases=ClockLeaseSet(self.client,config,{'main':self.clock,'ref':self.clock},primary_camera='main',wall=lambda:self.wall,mono=lambda:self.now)
        leases.refresh(force=True)
        self.advance(11);self.seq+=1;self.proof=self.make_proof();leases.refresh(force=True)
        self.assertEqual(leases.failures,{'ref':'clock_reference_calibration_expired'})
        leases.check();self.assertEqual(leases.latest['main']['sample_sequence'],2)
        from inference_worker.continuous_source_reader import ContinuousSourceReader
        # The real reader marks the failed reference, removes its mux dependency,
        # and retains explicit failure metadata for incomplete event evidence.
        from inference_worker.continuous_source_mux import SourceMux
        mux=SourceMux(camera_ids=['main','ref'],primary_camera='main',max_bytes=1000,max_frames=10,
            max_frame_pixels=16,max_clock_error=.1,clock_error_by_camera={'main':.1,'ref':.1})
        reader=ContinuousSourceReader(self.client,mux,offsets={'main':2.5,'ref':2.5},
            max_chunk_bytes=1000,max_chunk_gap_seconds=.02,degrade_references=True)
        reader.fail_clock_references(leases.failures)
        self.assertEqual(mux.ended,{'ref'})
        self.assertEqual(reader.end_reasons['ref'],'source_failed')
        self.assertEqual(reader.reference_failures['ref']['failure_code'],'clock_reference_calibration_expired')

    def test_reference_invalid_epoch_degrades_but_primary_auth_failure_is_fatal(self):
        reference=dict(BINDING,camera_id='ref',stream_id='reference-stream',epoch='reference-epoch')
        config=deepcopy(self.config);config['bindings']['ref']=reference
        reference_proof={**self.proof,**reference,'epoch':'changed'}
        def handler(request):
            proof=reference_proof if 'reference-stream' in request.url.path else self.proof
            return httpx.Response(200,json=dict(version=1,server_time=self.now,server_epoch='server',server_boot_id='boot',proof=proof))
        self.client.client._transport=httpx.MockTransport(handler)
        leases=ClockLeaseSet(self.client,config,{'main':self.clock,'ref':self.clock},primary_camera='main',wall=lambda:self.wall,mono=lambda:self.now)
        leases.refresh(force=True);self.assertIn('ref',leases.failures);leases.check()
        self.client.client._transport=httpx.MockTransport(lambda request:httpx.Response(403,json={'detail':'not-authorized'}))
        with self.assertRaises(httpx.HTTPStatusError):leases.refresh(force=True)

    def test_static_reader_cannot_silently_consume_proof_bound_stream(self):
        from inference_worker import test_continuous_source_reader as fixture
        client,reader=fixture.ReaderTests.make(self)
        client.streams[0]=dict(client.streams[0],clock_proof_version=1)
        with self.assertRaisesRegex(ClockLeaseError.__base__,'clock_proof_profile_required'):reader.poll()
        # Manual review deliberately ignores clock claims and stays review-only.
        reader.allow_unverified_clock_proofs=True
        reader.poll()

    def test_observed_server_restart_is_explicit_clock_failure(self):
        self.leases.refresh(force=True)
        self.client.client._transport=httpx.MockTransport(lambda request:httpx.Response(409,json={'detail':'clock_server_restarted'}))
        with self.assertRaisesRegex(ClockLeaseError,'server_epoch_changed'):self.leases._read('main')


@unittest.skipUnless(shutil.which('ffmpeg'),'ffmpeg required')
class ClockRuntimeEpisodeTests(unittest.TestCase):
    def setUp(self):
        from inference_worker import test_continuous_runtime as fixture
        self.clock_error=.1;fixture.RuntimeTests.setUp(self)
        self.proofs={};self.sequences={'main':0,'ref':0}
        self.bindings={c:dict(BINDING,camera_id=c,stream_id=c+'-stream',epoch=c+'-epoch') for c in ('main','ref')}
        self.renew('main');self.renew('ref')
        def handler(request):
            camera='ref' if '/ref-stream/' in request.url.path else 'main'
            return httpx.Response(200,json=dict(version=1,server_time=self.now,server_epoch='server',server_boot_id='boot',proof=self.proofs[camera]))
        client=httpx.Client(base_url='https://test',transport=httpx.MockTransport(handler));self.addCleanup(client.close)
        self.leases=ClockLeaseSet(SimpleNamespace(client=client,server_url='https://test'),dict(version=1,
            origin='https://test',server_epoch='server',policy=POLICY.public(),bindings=self.bindings,
            poll_seconds=2,max_host_rtt_seconds=.5),{c:dict(offset=0,error_bound=.1) for c in self.bindings},
            primary_camera='main',wall=lambda:self.now,mono=lambda:self.now)
        self.leases.refresh(force=True)

    def tearDown(self):
        from inference_worker import test_continuous_runtime as fixture
        fixture.RuntimeTests.tearDown(self)

    def renew(self,camera):
        self.sequences[camera]+=1
        low,high=sample_interval(self.now,self.now-.01,self.now+.01,self.now-.01,self.now+.01,POLICY)
        self.proofs[camera]=dict(version=1,proof_id=camera+str(self.sequences[camera]),sample_sequence=self.sequences[camera],
            **self.bindings[camera],server_epoch='server',server_boot_id='boot',server_sample=self.now,
            source_sent_at=self.now-.01,source_received_at=self.now+.01,monotonic_sent=self.now-.01,monotonic_received=self.now+.01,
            offset_low=low,offset_high=high,valid_until=self.now+POLICY.ttl_seconds,policy=POLICY.public())

    def test_live_runtime_primary_survives_reference_expiry_renewals_do_not_split_episode(self):
        starts=[]
        for step in range(39):
            self.now=step/2
            if step and step%8==0:self.renew('main')
            self.leases.refresh(force=True)
            for camera in self.leases.failures:self.runtime.end_source(camera,'source_failed')
            if 'ref' not in self.leases.failures:self.runtime.frame('ref',self.now,self.frame)
            status='helmet' if 27<=step<30 else 'suspected_no_helmet'
            transitions=self.runtime.frame('main',self.now,self.frame,assessments=[{'tracking_id':'same-person','ppe':{'helmet':{'status':status}}}])
            starts.extend(row for row in transitions if row['kind']=='start')
            self.runtime.pump(now=self.now)
            if step<30:self.assertLessEqual(len(starts),1)
        self.assertEqual(len(starts),2)  # A new episode is caused by actual rewear, not renewal.
        self.assertEqual(self.leases.failures,{'ref':'clock_reference_calibration_expired'})
        self.assertEqual(self.box.event_status(starts[0]['event_id'])['state'],'incomplete')
        self.assertEqual({row['tracking_id'] for row in starts},{'same-person'})

    def test_primary_expiry_runs_existing_incomplete_observation_close(self):
        starts=[]
        for step in range(11):
            self.now=step/2
            self.runtime.frame('ref',self.now,self.frame)
            starts.extend(row for row in self.runtime.frame('main',self.now,self.frame,assessments=[{
                'tracking_id':'same-person','ppe':{'helmet':{'status':'suspected_no_helmet'}}}]) if row['kind']=='start')
        self.now=11
        with self.assertRaisesRegex(ClockLeaseError,'expired'):self.leases.check()
        self.runtime.prepare_stop('clock_calibration_expired')
        event=starts[0]['event_id'];self.assertEqual(self.box.event_status(event)['state'],'incomplete')
        intent=self.box.pending_close_intents()[0]
        import json
        transition=json.loads(intent['body']) if 'body' in intent else json.loads(intent['transition'])
        self.assertEqual(transition['end_time'],5)
