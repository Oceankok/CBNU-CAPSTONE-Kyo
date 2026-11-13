import hashlib,json,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from uuid import uuid4
import httpx
from inference_worker.capture_outbox import CaptureOutbox
from inference_worker.segment_spool import Segment
from inference_worker.continuous_transport import UploadPlan
from inference_worker.continuous_dispatcher import ContinuousDispatcher


class FakeTransport:
    total_seconds=5;response_max=10000
    def __init__(self):self.uploads=[];self.receipts={};self.lose=False
    def recover_receipt(self,p,**kw):return self.receipts.get(p.request_id)
    def upload(self,p):
        self.uploads.append(p.request_id)
        self.receipts[p.request_id]=dict(event_id=p.event_id,camera_id=p.camera_id,
            request_id=p.request_id,role='clip' if p.camera_id=='main' else 'reference',
            source_sha256=p.sha256,source_bytes=p.byte_count,media_id='M'+p.camera_id,
            accepted=True,source_state='available')
        if self.lose:raise OSError('lost')


class DispatcherTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.clock=[100];self.box=self.open();self.transport=FakeTransport()
        self.d=self.dispatch();self.box.create_event('EVT',camera_snapshot=['main','ref'])
    def open(self):return CaptureOutbox(self.root/'journal',private_root=self.root/'raw',
        max_db_bytes=1000000,max_records=100,max_body_bytes=10000,source_ttl_seconds=20,clock=lambda:self.clock[0])
    def dispatch(self):return ContinuousDispatcher(self.box,self.transport,enabled=True,max_delivery_records=20,max_delivery_db_bytes=1000000)
    def tearDown(self):self.d.close();self.box.close();self.temp.cleanup()
    def source(self,camera='main'):
        folder,sid=uuid4().hex,uuid4().hex
        self.box.begin_capture('EVT',camera=camera,spool_directory=folder)
        path=self.root/'raw'/folder/(sid+'.mp4');path.parent.mkdir();path.write_bytes(b'test')
        segment=Segment(sid,0,1,2,10,10,4,100)
        self.box.bind_segment('EVT',camera=camera,segment=segment,relative_path=folder+'/'+path.name)
        plan=UploadPlan('EVT','REQ'+camera,camera,folder+'/'+path.name,4,hashlib.sha256(b'test').hexdigest(),1,2,1,2,True)
        self.d.register_upload(sid,plan,session_id=self.box.session,role='clip' if camera=='main' else 'reference')
        return sid,path
    def finalize(self):
        self.box.enqueue('EVT',seq=0,kind='start',idempotency_key='start',body={})
        self.box.acknowledge('EVT',0,receipt={})
        self.box.enqueue('EVT',seq=1,kind='close',idempotency_key='close',body={'kind':'close','complete':True})
        self.box.acknowledge('EVT',1,receipt={'event_id':'EVT'})
    def test_two_cameras_duplicate_ticks_keep_one_upload_and_no_delete(self):
        a,pa=self.source();b,pb=self.source('ref');self.finalize()
        self.d.upload_once();self.d.upload_once()
        self.assertEqual(len(self.transport.uploads),2)
        self.assertTrue(pa.exists() and pb.exists())
        self.assertTrue(all(self.d.cleanup_receipt_verified(row) for row in self.box.cleanup_obligations()))
    def test_lost_response_restart_recovers_without_upload(self):
        sid,path=self.source();self.finalize();self.transport.lose=True
        self.d.upload_once();self.assertEqual(len(self.transport.uploads),1)
        self.d.close();self.box.close();self.box=self.open();self.d=self.dispatch()
        self.d.upload_once()
        self.assertEqual(len(self.transport.uploads),1)
        self.assertEqual(self.d.status()[0]['state'],'accepted')
        self.assertTrue(path.exists())
    def test_ambiguous_404_never_reuploads(self):
        sid,path=self.source();self.finalize();self.transport.lose=True;self.d.upload_once()
        self.transport.receipts.clear();self.d.upload_once();self.d.upload_once()
        self.assertEqual(len(self.transport.uploads),1)
        self.assertEqual(self.d.status()[0]['state'],'unknown')
        self.assertTrue(path.exists())
    def test_ttl_blocks_unsent_upload_and_keeps_failure(self):
        self.source();self.clock[0]=121;self.d.upload_once()
        self.assertEqual(self.transport.uploads,[])
        self.assertEqual(self.box.event_status('EVT')['state'],'incomplete')
    def test_metadata_exact_retry_then_ack(self):
        bodies=[]
        def handler(request):
            bodies.append(request.read())
            return httpx.Response(503 if len(bodies)==1 else 200,json={'event_id':'EVT','cameras':[{'camera_id':'main','role':'clip'},{'camera_id':'ref','role':'reference'}],'accepted_command':{'kind':'start',
                'session_id':self.box.session,'sequence':0,'camera_id':None}})
        self.transport.client=httpx.Client(base_url='https://test',transport=httpx.MockTransport(handler))
        self.addCleanup(self.transport.client.close)
        raw=self.box.enqueue('EVT',seq=0,kind='start',idempotency_key='start',body={'event_id':'EVT','camera_id':'main','session_id':self.box.session})
        self.d.metadata_once();self.d.metadata_once()
        self.assertEqual(bodies,[raw,raw]);self.assertEqual(self.box.pending(),[])

    def test_reduced_disk_budget_rejects_existing_larger_ledger(self):
        self.d.db.execute('CREATE TABLE filler(value BLOB)')
        self.d.db.execute('INSERT INTO filler VALUES(?)',(b'x'*100000,))
        self.d.db.commit();self.d.close()
        with self.assertRaisesRegex(ValueError,'exceeds budget'):
            ContinuousDispatcher(self.box,self.transport,enabled=True,max_delivery_records=20,max_delivery_db_bytes=65536)
        self.d=self.dispatch()

    def test_wrong_command_ack_is_not_accepted(self):
        self.transport.client=httpx.Client(base_url='https://test',transport=httpx.MockTransport(
            lambda r:httpx.Response(200,json={'event_id':'EVT','accepted_command':{
                'kind':'progress','session_id':self.box.session,'sequence':7,'camera_id':None}})))
        self.addCleanup(self.transport.client.close)
        self.box.enqueue('EVT',seq=0,kind='start',idempotency_key='start',body={'event_id':'EVT','camera_id':'main','session_id':self.box.session})
        self.assertEqual(self.d.metadata_once(),[('EVT','metadata_response_binding')])
        self.assertEqual(len(self.box.pending()),1)

    def test_incomplete_row_cannot_send_complete_close(self):
        self.box.enqueue('EVT',seq=0,kind='start',idempotency_key='start',body={})
        self.box.acknowledge('EVT',0,receipt={})
        self.box.enqueue('EVT',seq=1,kind='incomplete',idempotency_key='bad',body={
            'kind':'close','complete':True,'session_id':self.box.session,'sequence':1})
        self.assertEqual(self.d.metadata_once(),[('EVT','command_kind_mismatch')])
        self.assertEqual(len(self.box.pending()),1)

    def test_provisional_event_never_uploads_before_verified_close(self):
        self.source()
        self.assertEqual(self.d.upload_once()[0][1],'waiting_verified_close')
        self.assertEqual(self.transport.uploads,[])

    def test_bounded_upload_steps_do_not_starve_other_camera(self):
        self.source();self.source('ref');self.finalize();self.transport.lose=True
        self.d.upload_once(max_items=1)
        self.transport.receipts.clear();self.transport.lose=False
        self.d.upload_once(max_items=1)
        self.assertEqual(len(self.transport.uploads),2)
        self.assertEqual(sum(row['state']=='accepted' for row in self.d.status()),1)

    def test_bounded_metadata_steps_rotate_past_blocked_event(self):
        self.box.create_event('EVT2',camera_snapshot=['main','ref'])
        for event in ('EVT','EVT2'):
            self.box.enqueue(event,seq=0,kind='start',idempotency_key=event+':start',
                body={'event_id':event,'camera_id':'main','session_id':self.box.session})
        def handler(request):
            body=json.loads(request.read());event=body['event_id']
            return httpx.Response(503 if event=='EVT' else 200,json={'event_id':event,
                'cameras':[{'camera_id':'main','role':'clip'},{'camera_id':'ref','role':'reference'}],
                'accepted_command':dict(kind='start',session_id=self.box.session,sequence=0,camera_id=None)})
        self.transport.client=httpx.Client(base_url='https://test',transport=httpx.MockTransport(handler))
        self.addCleanup(self.transport.client.close)
        self.assertEqual(self.d.metadata_once(max_items=1),[('EVT','blocked')])
        self.assertEqual(self.d.metadata_once(max_items=1),[('EVT2','acknowledged')])

    def test_incomplete_close_ack_allows_good_camera_private_upload(self):
        sid,path=self.source()
        self.box.enqueue('EVT',seq=0,kind='start',idempotency_key='start',body={})
        self.box.acknowledge('EVT',0,receipt={})
        self.box.mark_incomplete('EVT',reason='reference_missing')
        self.box.enqueue('EVT',seq=1,kind='incomplete',idempotency_key='partial-close',
            body={'session_id':self.box.session,'kind':'close','complete':False})
        self.d.upload_once();self.assertFalse(self.transport.uploads)
        self.box.acknowledge('EVT',1,receipt={'event_id':'EVT','verified':True})
        self.d.upload_once()
        self.assertEqual(len(self.transport.uploads),1)
        self.assertEqual(self.box.event_status('EVT')['state'],'incomplete')
        self.assertEqual(self.box.event_status('EVT')['reason'],'reference_missing')
        self.assertTrue(path.exists())
        self.assertTrue(self.d.cleanup_receipt_verified(self.box.segment_status(sid)))

    def test_incomplete_close_wrong_session_never_uploads(self):
        self.source()
        self.box.enqueue('EVT',seq=0,kind='start',idempotency_key='start',body={})
        self.box.acknowledge('EVT',0,receipt={})
        self.box.enqueue('EVT',seq=1,kind='incomplete',idempotency_key='bad-close',
            body={'session_id':'0'*32,'kind':'close','complete':False})
        self.box.acknowledge('EVT',1,receipt={'event_id':'EVT'})
        self.d.upload_once();self.assertFalse(self.transport.uploads)
