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
            starts,closes=fixtures.ConnectedManualTests.feed(self,seam=True,gap=True,missing_pre=True)
            self.assertEqual(len(starts),1);self.assertEqual(len(closes),1)
            self.assertTrue(self.dispatcher.status())
            self.assertTrue(all(r['state']=='accepted' for r in self.dispatcher.status()),self.dispatcher.status())
            event=api.get('/api/events/'+starts[0]['event_id'],headers=admin).json()['event']
            self.assertEqual(event['camera_id'],'main')
            self.assertEqual({m['camera_id'] for m in event['media']},{'main','ref'})
            self.assertEqual({m['role'] for m in event['media']},{'clip','reference'})
            self.assertTrue(all(m['url'] is None for m in event['media']))
            with get_connection() as conn:
                row=conn.execute('SELECT timestamp_start,timestamp_end FROM candidate_event WHERE event_id=?',(starts[0]['event_id'],)).fetchone()
                from datetime import datetime
                self.assertGreaterEqual((datetime.fromisoformat(row['timestamp_end'])-datetime.fromisoformat(row['timestamp_start'])).total_seconds(),20)
            self.assertEqual(hashes,{f:hashlib.sha256((candidate/f).read_bytes()).hexdigest() for f in contract_files})
            if os.environ.get('PPE_CONNECTED_VALIDATION_OUTPUT'):
                target=Path(os.environ['PPE_CONNECTED_VALIDATION_OUTPUT']);target.mkdir(parents=True,exist_ok=True)
                (target/'api-contract-result.json').write_text(json.dumps(dict(
                    backend_source=str(candidate),backend_contract_hashes=hashes,origin=event['camera_id'],
                    event_id=starts[0]['event_id'],media=[dict(camera_id=m['camera_id'],role=m['role'],status=m['status'],public_url=m['url']) for m in event['media']],
                    accepted_uploads=len(self.dispatcher.status()),privacy_processing='queued, no model execution',
                    database='isolated temporary SQLite',passed=True),indent=2),encoding='utf-8')
        finally:
            fixtures.ConnectedManualTests.tearDown(self)
