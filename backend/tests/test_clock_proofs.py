import json
import os
import time
import unittest
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import patch
from backend.tests import test_video
from types import SimpleNamespace
from backend.db.event_repository import get_connection
from clock_contract import ClockPolicy

POLICY = ClockPolicy(30, 1, 100, .02, .25, .05)


class ClockProofApiTests(unittest.TestCase):
    def setUp(self):
        test_video.VideoIngestTests.setUp(self)
        values = {'PPE_CAPTURE_CONTEXT_ENABLED': 'true', 'PPE_CLOCK_PROOF_ENABLED': 'true',
                  'PPE_CLOCK_SERVER_EPOCH': 'test-clock-'+uuid4().hex,
                  'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS': '.25'}
        values.update({'PPE_CLOCK_'+k.upper(): str(v) for k,v in POLICY.public().items()})
        scope = patch.dict(os.environ, values); scope.start(); self.addCleanup(scope.stop)
        self.context = self.client.post('/api/video/capture-sessions', headers=self.nodes['source'],
            json={'camera_id':'CAM_001','session_id':str(uuid4())}).json()
        self.stream = str(uuid4()); self.run_id = str(uuid4()); self.epoch = str(uuid4())
        response = self.client.post('/api/video/streams', headers=self.nodes['source'], json={
            'camera_id':'CAM_001','capture_context_id':self.context['context_id'],'stream_id':self.stream})
        self.assertEqual(response.status_code,201,response.text)
        self.sequence = 0

    def challenge(self, *, headers=None, sequence=None, offset=0):
        self.sent = time.time()-offset; self.mono = time.monotonic()
        body = dict(version=1,run_id=self.run_id,epoch=self.epoch,sample_sequence=sequence or self.sequence+1,
                    source_sent_at=self.sent,monotonic_sent=self.mono)
        self.challenge_body=body
        result = self.client.post(f'/api/video/streams/{self.stream}/clock-challenges',
            headers=headers or self.nodes['source'],json=body)
        self.received = time.time()-offset; self.received_mono = time.monotonic()
        return result

    def publish(self, challenge, *, headers=None, received=None):
        return self.client.post(f'/api/video/streams/{self.stream}/clock-proofs/{challenge["proof_id"]}',
            headers=headers or self.nodes['source'],json=dict(version=1,
                source_received_at=self.received if received is None else received,monotonic_received=self.received_mono))

    def proof(self, offset=0):
        result=self.challenge(offset=offset);self.assertEqual(result.status_code,201,result.text)
        published=self.publish(result.json());self.assertEqual(published.status_code,200,published.text)
        self.sequence += 1
        return result.json(),published.json()

    def test_opt_in_and_source_processor_scope(self):
        with patch.dict(os.environ,{'PPE_CLOCK_PROOF_ENABLED':'false'}):
            self.assertEqual(self.challenge().status_code,503)
        self.assertEqual(self.challenge(headers=self.nodes['other']).status_code,403)
        challenge,proof=self.proof(offset=2.5)
        self.assertEqual(proof['source_node_id'],'source')
        self.assertEqual(proof['capture_context_id'],self.context['context_id'])
        self.assertLess(proof['offset_low'],2.5);self.assertGreater(proof['offset_high'],2.5)
        path=f'/api/processing/video/streams/{self.stream}/clock-proofs'
        self.assertEqual(self.client.get(path,headers=self.nodes['source']).status_code,403)
        self.assertEqual(self.client.get(path,headers=self.admin).status_code,401)
        result=self.client.get(path,headers=self.nodes['processor'])
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['proof'],proof)
        self.assertEqual(self.publish(challenge,headers=self.nodes['other']).status_code,403)

    def test_exact_retry_no_expiry_extension_replay_and_epoch_change(self):
        challenge,proof=self.proof()
        self.assertEqual(self.client.post(f'/api/video/streams/{self.stream}/clock-challenges',
            headers=self.nodes['source'],json=self.challenge_body).json(),challenge)
        self.assertEqual(self.publish(challenge).json(),proof)
        self.assertEqual(self.publish(challenge,received=self.received+.01).status_code,409)
        self.assertEqual(self.challenge(sequence=1).status_code,409)
        self.epoch=str(uuid4());self.assertEqual(self.challenge().status_code,409)

    def test_active_pending_proof_survives_two_ttls_then_only_settled_unused_samples_prune(self):
        _,proof=self.proof()
        self.captured=datetime.fromtimestamp(proof['source_received_at']+.02,timezone.utc).isoformat()
        from backend.capture_context import clock_proofs
        with patch.object(clock_proofs,'server_now',return_value=proof['server_sample']+2*POLICY.ttl_seconds+1):
            response=self.challenge(sequence=2)
            self.assertEqual(response.status_code,201,response.text)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT proof FROM capture_clock_sample WHERE proof_id=?',(proof['proof_id'],)).fetchone()[0],json.dumps(proof,sort_keys=True,allow_nan=False))
        time.sleep(1.1)
        self.assertEqual(self.upload(proof).status_code,202)
        result=self.client.post('/api/video/capture-sessions/'+self.context['context_id']+'/stop',
            headers=self.nodes['source'],json={'capture_stopped':True,'final_sequences':{self.stream:0}})
        self.assertEqual(result.status_code,200,result.text)
        with get_connection() as conn:
            rows=conn.execute('SELECT proof_id FROM capture_clock_sample WHERE stream_id=?',(self.stream,)).fetchall()
            self.assertEqual([row['proof_id'] for row in rows],[proof['proof_id']])

    def publisher_fixture(self,directory):
        from pathlib import Path
        from video_sender.clock_source import ClockPublisher
        from video_sender.context_sender import atomic_json
        class Client:
            origin='https://test.invalid'
            lose=None
            def post(inner,path,body):
                response=self.client.post(path,headers=self.nodes['source'],json=body)
                response.raise_for_status()
                if inner.lose and inner.lose in path:
                    inner.lose=None;raise TimeoutError('synthetic lost committed response')
                return response.json()
            def get(inner,path):
                response=self.client.get(path,headers=self.nodes['source']);response.raise_for_status();return response.json()
            def upload(inner,stream,sequence,path,captured,clock_proof_id=None):
                headers={**self.nodes['source'],'Content-Type':'video/mp4','X-Capture-Start':captured,
                         'X-Duration-Ms':'1000','X-Clock-Proof-Id':clock_proof_id}
                response=self.client.post(f'/api/video/streams/{stream}/chunks?sequence={sequence}',headers=headers,content=path.read_bytes())
                response.raise_for_status()
        root=Path(directory);state_path=root/'session.json';client=Client()
        runner=SimpleNamespace(root=root,client=client,state=dict(server_origin=client.origin,
            camera_id='CAM_001',run_id=self.run_id,stream_id=self.stream,context_id=self.context['context_id'],
            session_id=self.context['session_id'],acknowledged_sequence=-1,capture_started=True,closed=False))
        runner.save=lambda:atomic_json(state_path,runner.state)
        runner.save()
        config=(POLICY,os.environ['PPE_CLOCK_SERVER_EPOCH'],'source',5)
        publisher=ClockPublisher(runner,config);publisher.refresh(force=True)
        return runner,publisher,config

    def test_committed_publication_response_loss_recovers_exact_pending_exchange(self):
        from tempfile import TemporaryDirectory
        from video_sender.clock_source import ClockPublisher
        with TemporaryDirectory() as directory:
            runner,publisher,config=self.publisher_fixture(directory)
            runner.client.lose='/clock-proofs/'
            with self.assertRaises(TimeoutError):publisher.refresh(force=True)
            durable=json.loads((runner.root/'session.json').read_text())
            self.assertEqual(durable['clock']['sample_sequence'],2)
            pending=durable['clock']['pending'];ident=pending['challenge']['proof_id']
            with get_connection() as conn:original=conn.execute('SELECT proof FROM capture_clock_sample WHERE proof_id=?',(ident,)).fetchone()[0]
            runner.state=durable
            ClockPublisher(runner,config).refresh(force=True)
            self.assertEqual(runner.state['clock']['sample_sequence'],3)
            self.assertNotIn('pending',runner.state['clock'])
            with get_connection() as conn:self.assertEqual(conn.execute('SELECT proof FROM capture_clock_sample WHERE proof_id=?',(ident,)).fetchone()[0],original)

    def test_lost_challenge_response_after_rtt_recovers_cursor_without_fabricating_proof(self):
        from tempfile import TemporaryDirectory
        from video_sender.clock_source import ClockPublisher
        with TemporaryDirectory() as directory:
            runner,publisher,config=self.publisher_fixture(directory)
            runner.client.lose='/clock-challenges'
            with self.assertRaises(TimeoutError):publisher.refresh(force=True)
            durable=json.loads((runner.root/'session.json').read_text())
            self.assertEqual(durable['clock']['sample_sequence'],1)
            self.assertIsNone(durable['clock']['pending']['challenge'])
            time.sleep(1.05)
            runner.state=durable;ClockPublisher(runner,config).refresh(force=True)
            self.assertEqual(runner.state['clock']['sample_sequence'],3)
            with get_connection() as conn:
                abandoned=conn.execute('SELECT proof FROM capture_clock_sample WHERE stream_id=? AND sample_sequence=2',(self.stream,)).fetchone()
                self.assertIsNone(abandoned['proof'])

    def test_uncommitted_publication_expires_then_resynchronizes_without_reusing_evidence(self):
        from tempfile import TemporaryDirectory
        from video_sender.clock_source import ClockPublisher
        with TemporaryDirectory() as directory:
            runner,publisher,config=self.publisher_fixture(directory)
            original_post=runner.client.post
            def fail_before_commit(path,body):
                if '/clock-proofs/' in path:raise TimeoutError('synthetic request never reached server')
                return original_post(path,body)
            runner.client.post=fail_before_commit
            with self.assertRaises(TimeoutError):publisher.refresh(force=True)
            runner.state=json.loads((runner.root/'session.json').read_text())
            runner.client.post=original_post
            time.sleep(1.05)
            ClockPublisher(runner,config).refresh(force=True)
            self.assertEqual(runner.state['clock']['sample_sequence'],3)
            with get_connection() as conn:
                self.assertIsNone(conn.execute('SELECT proof FROM capture_clock_sample WHERE stream_id=? AND sample_sequence=2',(self.stream,)).fetchone()['proof'])

    def test_capacity_backpressure_preserves_pending_proof_and_permits_original_upload(self):
        _,proof=self.proof()
        from backend.capture_context import clock_proofs
        with patch.object(clock_proofs,'MAX_STREAM_SAMPLES',2):
            self.assertEqual(self.challenge(sequence=2).status_code,201)
            response=self.challenge(sequence=3)
            self.assertEqual(response.status_code,409,response.text)
            self.assertEqual(response.json()['detail'],'clock_proof_capacity')
        self.captured=datetime.fromtimestamp(proof['source_received_at']+.02,timezone.utc).isoformat()
        time.sleep(1.1)
        self.assertEqual(self.upload(proof).status_code,202)

    def test_completed_source_does_not_regress_latest_consumer_proof(self):
        _,first=self.proof()
        self.captured=datetime.fromtimestamp(first['source_received_at']+.02,timezone.utc).isoformat()
        time.sleep(1.1);self.assertEqual(self.upload(first).status_code,202)
        _,latest=self.proof()
        result=self.client.post('/api/video/capture-sessions/'+self.context['context_id']+'/stop',
            headers=self.nodes['source'],json={'capture_stopped':True,'final_sequences':{self.stream:0}})
        self.assertEqual(result.status_code,200,result.text)
        response=self.client.get(f'/api/processing/video/streams/{self.stream}/clock-proofs',headers=self.nodes['processor'])
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['proof'],latest)

    def test_api_restart_blocks_live_renewal_but_cli_drains_original_historical_chunks(self):
        from tempfile import TemporaryDirectory
        from video_sender.context_sender import SessionRunner,main,atomic_json
        from video_sender.capture_control import publish_result
        from backend.capture_context import clock_proofs
        with TemporaryDirectory() as directory:
            runner,publisher,config=self.publisher_fixture(directory)
            original=json.loads(json.dumps(runner.state['clock']['proof']))
            captured=datetime.fromtimestamp(original['source_received_at']+.02,timezone.utc).isoformat()
            path=runner.root/'chunk_00000.mp4';path.write_bytes(self.data)
            path.with_suffix('.txt').write_text(captured)
            path.with_suffix('.clock.json').write_text(json.dumps({'proof_id':original['proof_id']}))
            path.with_suffix('.ready').touch()
            publish_result(runner.root,self.run_id,stopped_by_request=True,ffmpeg_exit=0,start_sequence=0,frame_count=15,fps=15)
            time.sleep(1.1)
            with patch.object(clock_proofs,'SERVER_BOOT_ID',str(uuid4())):
                with self.assertRaisesRegex(ValueError,'restarted'):publisher.refresh(force=True)
                # Offline source wall/monotonic anchor is deliberately unusable:
                # only current server observations may gate historical upload.
                runner.state['clock']['anchor']={'utc':0,'monotonic':0};runner.save()
                with patch('sys.argv',['sender','--directory',str(runner.root),'--resume-drain']),\
                     patch.dict(os.environ,{'VIDEO_SERVER_URL':runner.client.origin,'VIDEO_NODE_TOKEN':'synthetic','VIDEO_CLOCK_PROOF_ENABLED':'false'}),\
                     patch('video_sender.context_sender.ContextClient',return_value=runner.client),\
                     patch('video_sender.context_sender.get_video_duration_ms',return_value=1000),\
                     patch('video_sender.clock_source.ClockPublisher',side_effect=AssertionError('historical drain must not publish')):
                    main()
            settled=json.loads((runner.root/'session.json').read_text())
            self.assertTrue(settled['closed']);self.assertEqual(settled['acknowledged_sequence'],0)
            self.assertFalse(path.exists())
            with get_connection() as conn:
                binding=conn.execute('SELECT proof_id FROM capture_clock_chunk WHERE stream_id=? AND sequence=0',(self.stream,)).fetchone()
                self.assertEqual(binding['proof_id'],original['proof_id'])
                self.assertEqual(json.loads(conn.execute('SELECT proof FROM capture_clock_sample WHERE proof_id=?',(original['proof_id'],)).fetchone()[0]),original)

    def test_stale_challenge_and_invalid_measurement_fail_closed(self):
        result=self.challenge().json()
        with get_connection() as conn:
            conn.execute('UPDATE capture_clock_sample SET server_sample=server_sample-2 WHERE proof_id=?',(result['proof_id'],))
        self.assertEqual(self.publish(result).status_code,409)
        result=self.challenge(sequence=2).json()
        self.assertEqual(self.publish(result,received=self.sent-1).status_code,422)

    def test_server_clock_step_is_sticky(self):
        self.proof()
        from backend.capture_context import clock_proofs
        real=time.time()
        with patch.object(clock_proofs.time,'time',return_value=real+1):
            self.assertEqual(self.challenge().status_code,503)
        self.assertEqual(self.challenge().status_code,503)

    def test_server_process_restart_invalidates_pre_restart_proofs(self):
        _,proof=self.proof()
        from backend.capture_context import clock_proofs
        with patch.object(clock_proofs,'SERVER_BOOT_ID',str(uuid4())):
            response=self.client.get(f'/api/processing/video/streams/{self.stream}/clock-proofs',headers=self.nodes['processor'])
            self.assertEqual(response.status_code,409,response.text)
            self.assertEqual(response.json()['detail'],'clock_server_restarted')

    def test_actual_source_publication_host_verification_and_reader_correction_once(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from video_sender.clock_source import ClockPublisher,capture_stamp
        from inference_worker.clock_leases import ClockLeaseSet,IDENTITY
        from inference_worker.continuous_source_reader import ContinuousSourceReader
        from inference_worker.continuous_source_mux import SourceMux
        import numpy as np
        class SourceClient:
            def post(inner,path,body):
                response=self.client.post(path,headers=self.nodes['source'],json=body)
                response.raise_for_status();return response.json()
        with TemporaryDirectory() as directory:
            runner=SimpleNamespace(root=Path(directory),save=lambda:None,client=SourceClient(),state=dict(
                camera_id='CAM_001',run_id=self.run_id,stream_id=self.stream,
                context_id=self.context['context_id'],session_id=self.context['session_id']))
            publisher=ClockPublisher(runner,(POLICY,os.environ['PPE_CLOCK_SERVER_EPOCH'],'source',5))
            publisher.refresh(force=True);proof=publisher.state['proof']
            offset=(proof['offset_low']+proof['offset_high'])/2
            class ProcessorClient:
                server_url='https://test'
                def get(inner,path,params=None):return self.client.get(path,params=params,headers=self.nodes['processor'])
                def list_streams(inner):return inner.get('/api/processing/video/streams').json()['items']
                def list_chunks(inner,stream,sequence):return inner.get(f'/api/processing/video/streams/{stream}/chunks',{'after_sequence':sequence}).json()['items']
            processor=ProcessorClient();processor.client=processor
            config=dict(version=1,origin='https://test',server_epoch=os.environ['PPE_CLOCK_SERVER_EPOCH'],policy=POLICY.public(),
                poll_seconds=5,max_host_rtt_seconds=1,bindings={'CAM_001':{k:proof[k] for k in IDENTITY}})
            leases=ClockLeaseSet(processor,config,{'CAM_001':dict(offset=offset,error_bound=.25)},primary_camera='CAM_001')
            leases.refresh(force=True)
            mux=SourceMux(camera_ids=['CAM_001'],primary_camera='CAM_001',max_bytes=1000,max_frames=10,
                max_frame_pixels=16,max_clock_error=.25,clock_error_by_camera={'CAM_001':.25})
            reader=ContinuousSourceReader(processor,mux,offsets={'CAM_001':offset},max_chunk_bytes=1000000,
                max_chunk_gap_seconds=.02,require_capture_context=True,clock_verifier=leases.validate_chunk,
                downloader=lambda ident:b'synthetic fixture',decoder=lambda *args,**kwargs:iter([(0,1,np.zeros((2,2,3),np.uint8))]))
            self.assertEqual(reader.poll(),[])
            publisher.wait(valid_from=datetime.fromisoformat(self.context['valid_from']).timestamp())
            stamp,proof_id=capture_stamp(publisher.path)
            self.captured=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
            publisher.wait(raw_end=stamp+1)
            response=self.upload(proof);self.assertEqual(response.status_code,202,response.text)
            frames=reader.poll();self.assertEqual(len(frames),1)
            from backend.video.service import capture_time
            accepted_raw=datetime.fromisoformat(capture_time(self.captured)).timestamp()
            self.assertLess(abs(accepted_raw-stamp),.0011)  # Existing millisecond wire quantization.
            self.assertAlmostEqual(frames[0][1],accepted_raw+offset,places=5)
            self.assertEqual(leases.latest['CAM_001']['proof_id'],proof_id)

    def upload(self, proof, sequence=0, captured=None):
        if captured is None: captured=self.captured
        headers={**self.nodes['source'],'Content-Type':'video/mp4','X-Capture-Start':captured,
                 'X-Duration-Ms':'1000'}
        if proof is not None:headers['X-Clock-Proof-Id']=proof['proof_id']
        return self.client.post(f'/api/video/streams/{self.stream}/chunks?sequence={sequence}',headers=headers,content=self.data)

    def test_raw_gates_unchanged_and_chunk_binding_immutable(self):
        _,proof=self.proof()
        self.captured=datetime.fromtimestamp(proof['source_received_at']+.02,timezone.utc).isoformat()
        # A valid proof cannot authorize raw media from the future.
        self.assertEqual(self.upload(proof).status_code,422)
        time.sleep(1.1)  # Actual wait: both strict raw gates now admit the completed fixture.
        result=self.upload(proof);self.assertEqual(result.status_code,202,result.text)
        self.assertEqual(self.upload(None).status_code,409)
        self.assertTrue(self.upload(proof).json()['duplicate'])
        rows=self.client.get(f'/api/processing/video/streams/{self.stream}/chunks',headers=self.nodes['processor']).json()['items']
        self.assertEqual(rows[0]['clock_proof_id'],proof['proof_id'])
        self.assertEqual(self.upload(proof,sequence=1,captured=self.context['valid_from']).status_code,422)

    def test_migration_additive_reinitialize_preserves_existing_rows(self):
        from backend.db.connection import is_postgres,schema_version
        postgres=is_postgres()
        with get_connection() as conn:
            conn.execute('INSERT INTO capture_chunk_receipt VALUES(?,?,?,?,?,?)',
                         (self.stream,0,'representative-preserved-chunk','a'*64,self.context['valid_from'],1000))
            for table in ('capture_clock_chunk','capture_clock_sample','capture_clock_stream'):
                conn.execute('DROP TABLE '+table)
            if postgres:
                conn.execute('UPDATE app_schema_version SET version=36 WHERE singleton=1')
                tables=[r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE' AND table_name!='app_schema_version'")]
            else:
                conn.execute('PRAGMA user_version=36')
                tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            import re
            self.assertTrue(all(re.fullmatch('[a-z_]+',name) for name in tables))
            before={name:sorted(repr(tuple(r)) for r in conn.execute('SELECT * FROM '+name)) for name in tables}
        if postgres:
            from backend.db.postgres import initialize
            initialize(seed=False)
        else:
            from backend.db.migrations import apply_migrations
            with get_connection() as conn:apply_migrations(conn)
        with get_connection() as conn:
            self.assertEqual(schema_version(conn),37)
            for name in tables:
                self.assertEqual(sorted(repr(tuple(r)) for r in conn.execute('SELECT * FROM '+name)),before[name],name)
        if postgres:initialize(seed=False)
        else:
            with get_connection() as conn:apply_migrations(conn)  # Already-current is a no-op.
