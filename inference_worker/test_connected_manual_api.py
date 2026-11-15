"""Optional source-composition test against the backend owner's real isolated API.

Run this module alone with PPE_CONNECTED_BACKEND_SOURCE set. No production API,
DB, model or camera is used. Private uploads are queued, never falsely published.
"""
import os,json,hashlib
from pathlib import Path
from unittest import TestCase,skipUnless
from unittest.mock import patch

from inference_worker import test_connected_manual_incident as fixtures
from inference_worker.continuous_transport import PrivateClipTransport


@skipUnless(os.environ.get('PPE_CONNECTED_BACKEND_SOURCE'),'explicit backend candidate source required')
class ConnectedManualApiTests(TestCase):
    def test_real_metadata_capture_and_private_upload_contract(self):
        self.run_case(False)

    def test_active_interruption_registers_finalized_partial_runs_before_close(self):
        self.run_case(True)

    def run_case(self,active):
        import backend
        candidate=Path(os.environ['PPE_CONNECTED_BACKEND_SOURCE']).resolve()
        contract_files=['backend/processing/lifecycle.py','backend/processing/capture.py',
                        'backend/media/service.py','backend/auto_decision/rules.py']
        hashes={f:hashlib.sha256((candidate/f).read_bytes()).hexdigest() for f in contract_files}
        self.assertTrue((candidate/'backend/processing/lifecycle.py').is_file())
        backend.__path__=[str(candidate/'backend')]
        from backend.tests.support import isolated_api,login
        from backend.db.event_repository import get_connection
        from backend.media import paths,service
        from backend.processing import routes
        self.enterContext(patch.dict(os.environ,{'PPE_CONTINUOUS_EVENTS_ENABLED':'true',
            'PPE_PROCESSING_MODE':'remote','PPE_DATABASE_URL':'','PPE_TEST_POSTGRES_URL':''}))
        api=self.enterContext(isolated_api());admin=login(api)
        token=api.post('/api/nodes',headers=admin,json={'node_id':'connected-test','name':'connected-test'}).json()['token']
        api.put('/api/processing/workers/connected-test',headers=admin,json={'enabled':True}).raise_for_status()
        with get_connection() as conn:
            for camera,seed in (('main','CAM_001'),('ref','CAM_002')):
                conn.execute('''INSERT INTO camera_info(camera_id,camera_location,zone_name,process_type,status)
                    SELECT ?,camera_location,zone_name,process_type,status FROM camera_info WHERE camera_id=?''',(camera,seed))
        api.post('/api/camera-groups',headers=admin,json={'name':'connected-pair','camera_ids':['main','ref']}).raise_for_status()
        fixtures.ConnectedManualTests.setUp(self)
        try:
            for module in (paths,service,routes):
                for name,value in dict(PROJECT_ROOT=self.root,STORAGE_ROOT=self.root/'storage',
                    SOURCE_ROOT=self.root/'private',WORK_ROOT=self.root/'work',PROCESSED_ROOT=self.root/'storage/processed').items():
                    if hasattr(module,name):self.enterContext(patch.object(module,name,value))
            self.transport.client.close()
            self.transport=PrivateClipTransport(server_url='http://127.0.0.1',token=token,private_root=self.box.root,
                max_upload_bytes=1000000,timeout_seconds=10,max_response_bytes=131072,total_seconds=30,
                transport=api._transport)
            self.dispatcher.transport=self.transport
            self.box.bind_runtime(dict(endpoint='http://127.0.0.1',primary='main',
                clocks={c:dict(offset=0,error_bound=0,verified=False) for c in ('main','ref')}))
            self.transport.uploads=[]
            upload=self.transport.upload
            def lost_ack(plan):
                self.transport.uploads.append(plan.request_id)
                result=upload(plan)
                if len(self.transport.uploads)==1:raise OSError('synthetic lost response after server commit')
                return result
            self.transport.upload=lost_ack
            starts,closes=fixtures.ConnectedManualTests.feed(self,seam=True,gap=True,missing_pre=True,
                interrupt_upload=not active,interrupt_active=active)
            if active:
                for cap in self.capture.events[starts[0]['event_id']]['captures'].values():
                    if not cap.writer.closed:cap.writer.abort(reason='synthetic_process_crash')
                    cap.done=True;cap.reasons.append('process_restart')
                self.runtime.journal.lifecycle.states.clear()
            from inference_worker.capture_outbox import CaptureOutbox
            from inference_worker.continuous_dispatcher import ContinuousDispatcher
            from inference_worker.session_recovery import SessionRecovery
            self.dispatcher.close();self.box.close()
            self.box=CaptureOutbox(self.root/'journal',private_root=self.root/'raw',max_db_bytes=2000000,
                max_records=300,max_body_bytes=20000,source_ttl_seconds=100,clock=lambda:self.now)
            self.dispatcher=ContinuousDispatcher(self.box,self.transport,enabled=True,max_delivery_records=30,max_delivery_db_bytes=2000000)
            self.runtime.outbox=self.box;self.runtime.journal.outbox=self.box;self.runtime.dispatcher=self.dispatcher
            from inference_worker.session_recovery import RecoveryRequired
            for attempt in range(2):
                try:
                    SessionRecovery(self.box,self.dispatcher,endpoint='http://127.0.0.1').run()
                    break
                except RecoveryRequired as error:
                    if str(error)!='restart_upload_pending' or attempt:raise
            from inference_worker.cleanup_supervisor import CleanupSupervisor
            for _ in range(4):
                spools={cap.spool.root.name:cap.spool for record in self.capture.events.values()
                        for cap in record['captures'].values() if cap.spool}
                CleanupSupervisor(self.box,spools=spools,verified_receipt=self.dispatcher.cleanup_receipt_verified).run_once()
                self.runtime.release_finished()
            old=self.box.db.execute('SELECT * FROM events WHERE id=?',(starts[0]['event_id'],)).fetchone()
            self.assertTrue(SessionRecovery(self.box,self.dispatcher,endpoint='http://127.0.0.1')._settled_history(old))
            self.assertEqual(len(starts),1);self.assertEqual(len(closes),0 if active else 1)
            self.assertTrue(self.dispatcher.status())
            self.assertTrue(all(r['state']=='accepted' for r in self.dispatcher.status()),self.dispatcher.status())
            self.assertEqual(len(self.transport.uploads),len(set(self.transport.uploads)))
            event=api.get('/api/events/'+starts[0]['event_id'],headers=admin).json()['event']
            self.assertEqual(event['camera_id'],'main')
            self.assertEqual({m['camera_id'] for m in event['media']},{'main','ref'})
            self.assertEqual({m['role'] for m in event['media']},{'clip','reference'})
            self.assertTrue(all(m['url'] is None for m in event['media']))
            with get_connection() as conn:
                row=conn.execute('SELECT timestamp_start,timestamp_end FROM candidate_event WHERE event_id=?',(starts[0]['event_id'],)).fetchone()
                from datetime import datetime
                self.assertGreaterEqual((datetime.fromisoformat(row['timestamp_end'])-datetime.fromisoformat(row['timestamp_start'])).total_seconds(),16 if active else 20)
                if active:
                    saved=self.box.db.execute("SELECT body FROM requests WHERE event=? AND kind='progress' ORDER BY seq DESC LIMIT 1",(starts[0]['event_id'],)).fetchone()
                    self.assertEqual(datetime.fromisoformat(row['timestamp_end']),datetime.fromisoformat(json.loads(saved['body'])['timestamp_end']))
            self.assertEqual(hashes,{f:hashlib.sha256((candidate/f).read_bytes()).hexdigest() for f in contract_files})
            if os.environ.get('PPE_CONNECTED_VALIDATION_OUTPUT'):
                target=Path(os.environ['PPE_CONNECTED_VALIDATION_OUTPUT']);target.mkdir(parents=True,exist_ok=True)
                (target/('api-active-recovery-result.json' if active else 'api-contract-result.json')).write_text(json.dumps(dict(
                    backend_source=str(candidate),backend_contract_hashes=hashes,origin=event['camera_id'],
                    event_id=starts[0]['event_id'],media=[dict(camera_id=m['camera_id'],role=m['role'],status=m['status'],public_url=m['url']) for m in event['media']],
                    accepted_uploads=len(self.dispatcher.status()),privacy_processing='queued, no model execution',
                    database='isolated temporary SQLite',passed=True),indent=2),encoding='utf-8')
        finally:
            fixtures.ConnectedManualTests.tearDown(self)
