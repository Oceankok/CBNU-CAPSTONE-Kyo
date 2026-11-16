from dataclasses import replace
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import httpx
from inference_worker.continuous_transport import PrivateClipTransport, UploadPlan


class TransportTests(TestCase):
    def test_partial_manifest_prefix_allows_only_outer_wire_edge_not_internal_gap(self):
        from datetime import datetime,timedelta
        from types import SimpleNamespace
        import sqlite3
        from inference_worker.continuous_runtime import ContinuousFrameRuntime
        start=datetime.fromisoformat('2026-11-15T10:36:30.216333+00:00')
        end=start+timedelta(seconds=4)
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TABLE requests(event TEXT,kind TEXT,seq INTEGER,body TEXT)')
        for left,gap,expected in [(-1,0,2),(1,0,2),(-1,1,1),(1,1,1)]:
            segments=[SimpleNamespace(segment_id='S1',capture_start=(start+timedelta(microseconds=left)).timestamp(),
                                      capture_end=(start+timedelta(seconds=2)).timestamp()),
                      SimpleNamespace(segment_id='S2',capture_start=(start+timedelta(seconds=2,microseconds=gap)).timestamp(),
                                      capture_end=end.timestamp())]
            runtime=object.__new__(ContinuousFrameRuntime)
            runtime.capture=SimpleNamespace(manual_review=False,events={'EVT_1':{'captures':{'CAM_1':SimpleNamespace(
                segments=segments,spool=SimpleNamespace(path_for_upload=lambda _:self.root/'clip.mp4'))}}})
            runtime.outbox=SimpleNamespace(db=db,session='a'*32,segment_status=lambda _:{'state':'present'})
            queued=[];runtime._enqueue=lambda event,kind,key,body:queued.append(body)
            self.assertEqual(runtime._partial_manifests('EVT_1',{'start':start.timestamp(),'end':end.timestamp()}),expected)
            self.assertEqual(len(queued),expected)

    def test_native_wire_outer_edges_traverse_payload_selection_and_verified_upload(self):
        from datetime import datetime,timedelta
        from types import SimpleNamespace
        from inference_worker.continuous_payloads import capture_payload
        from inference_worker.continuous_runtime import ContinuousFrameRuntime
        start=datetime.fromisoformat('2026-11-15T10:36:30.216333+00:00')
        end=datetime.fromisoformat('2026-11-15T10:36:41.549667+00:00')
        client=self.client()
        for left in [-1,0,1]:
            for right in [-1,0,1]:
                with self.subTest(left=left,right=right):
                    segment=SimpleNamespace(segment_id='S1',
                        capture_start=(start+timedelta(microseconds=left)).timestamp(),
                        capture_end=(end+timedelta(microseconds=right)).timestamp())
                    cap=SimpleNamespace(segments=[segment],spool=SimpleNamespace(path_for_upload=lambda _:self.root/'clip.mp4'))
                    runtime=object.__new__(ContinuousFrameRuntime)
                    runtime.capture=SimpleNamespace(events={'EVT_1':{'captures':{'CAM_1':cap}}})
                    runtime.outbox=SimpleNamespace(segment_status=lambda _:{'state':'present'})
                    status={'start':start.timestamp(),'end':end.timestamp(),'cameras':{
                        'CAM_1':{'done':True,'reasons':[],'segments':[segment]}}}
                    body=capture_payload(status,camera_id='CAM_1',clock=SimpleNamespace(
                        offset=0,error_bound=0,verified=True),session_id='a'*32,sequence=1,
                        time_basis='source_utc_corrected',required_end=end.timestamp())
                    self.assertEqual(body['state'],'complete')
                    self.assertEqual(body['captured_ranges'][0]['end_at'],
                                     (end+timedelta(microseconds=right)).isoformat(timespec='microseconds'))
                    self.assertEqual(runtime._safe_segments('EVT_1',status)['CAM_1'],[segment])
                    plan=replace(self.plan,capture_start=segment.capture_start,capture_end=segment.capture_end,
                                 required_start=start.timestamp(),required_end=end.timestamp())
                    self.assertEqual(client.upload(plan)['status'],'processing')
        for left,right in [(-2,0),(0,2),(-66667,0),(0,66667)]:
            segment.capture_start=(start+timedelta(microseconds=left)).timestamp()
            segment.capture_end=(end+timedelta(microseconds=right)).timestamp()
            self.assertEqual(runtime._safe_segments('EVT_1',status)['CAM_1'],[])
            with self.assertRaises(ValueError):
                client.upload(replace(plan,capture_start=segment.capture_start,capture_end=segment.capture_end))

    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.raw=b'private synthetic bytes'
        (self.root/'clip.mp4').write_bytes(self.raw)
        self.plan=UploadPlan('EVT_1','REQ_1','CAM_1','clip.mp4',len(self.raw),
            hashlib.sha256(self.raw).hexdigest(),0,7,0,7,True)
        self.seen=[]

    def client(self, handler=None):
        def default(request):
            self.seen.append(request)
            self.assertEqual(request.read(),self.raw)
            return httpx.Response(201,json={'media_id':'MEDIA_1','status':'processing','event_id':'EVT_1',
                'camera_id':'CAM_1','clip_request_id':'REQ_1'})
        c=PrivateClipTransport(server_url='https://private.test',token='synthetic',
            private_root=self.root,max_upload_bytes=1024,timeout_seconds=2,total_seconds=5,
            max_response_bytes=256,transport=httpx.MockTransport(handler or default))
        self.addCleanup(c.close);return c

    def test_private_upload_does_not_delete_or_claim_ready(self):
        result=self.client().upload(self.plan)
        self.assertEqual(result['status'],'processing')
        self.assertTrue((self.root/'clip.mp4').exists())
        self.assertEqual(self.seen[0].url.params['camera_id'],'CAM_1')

    def test_path_digest_range_and_id_checks_precede_network(self):
        client=self.client()
        for change in ({'relative_path':'../escape'},{'relative_path':'/tmp/raw'},
                       {'sha256':'0'*64},{'capture_end':8},{'final_range_verified':False},
                       {'event_id':'../other'},{'byte_count':1}):
            with self.assertRaises(ValueError):client.upload(replace(self.plan,**change))
        self.assertEqual(self.seen,[])

    def test_symlink_rejected(self):
        try:(self.root/'link.mp4').symlink_to(self.root/'clip.mp4')
        except OSError as error:
            if getattr(error,"winerror",None)==1314:
                self.skipTest("Windows symlink privilege unavailable; Linux CI must exercise this case")
            raise
        with self.assertRaisesRegex(ValueError,'symlink'):
            self.client().upload(replace(self.plan,relative_path='link.mp4'))

    def test_redirect_never_forwards_private_source(self):
        calls=[]
        def handler(request):
            calls.append(str(request.url));return httpx.Response(307,headers={'location':'https://other.test'})
        with self.assertRaisesRegex(RuntimeError,'307'):self.client(handler).upload(self.plan)
        self.assertEqual(len(calls),1)

    def test_http_errors_and_unbounded_response_fail_without_cleanup(self):
        for response in (httpx.Response(503,text='private internals'),httpx.Response(201,text='x'*300)):
            with self.assertRaises((RuntimeError,ValueError)):
                self.client(lambda request:response).upload(self.plan)
        self.assertTrue((self.root/'clip.mp4').exists())

    def test_insecure_remote_endpoint_rejected(self):
        for url in ('http://example.com','https://user:pass@example.com','https://example.com/path'):
            with self.assertRaises(ValueError):
                PrivateClipTransport(server_url=url,token='x',private_root=self.root,
                    max_upload_bytes=100,timeout_seconds=1,total_seconds=5,max_response_bytes=100)

    def test_wrong_receipt_binding_cannot_acknowledge_source(self):
        with self.assertRaisesRegex(ValueError,'binding'):
            self.client(lambda r:httpx.Response(201,json={'media_id':'OTHER','status':'ready'})).upload(self.plan)
        self.assertTrue((self.root/'clip.mp4').exists())

    def test_source_mutation_after_snapshot_cannot_change_upload(self):
        def handler(request):
            (self.root/'clip.mp4').write_bytes(b'changed source')
            self.assertEqual(request.read(),self.raw)
            return httpx.Response(201,json={'media_id':'M','status':'processing',
                'event_id':'EVT_1','camera_id':'CAM_1','clip_request_id':'REQ_1'})
        self.assertEqual(self.client(handler).upload(self.plan)['media_id'],'M')

    def receipt(self, **changes):
        return {'event_id':'EVT_1','request_id':'REQ_1','camera_id':'CAM_1','role':'clip',
            'media_id':'M','source_sha256':self.plan.sha256,'source_bytes':len(self.raw),
            'accepted':True,'source_state':'available',**changes}

    def recover(self, body, **kwargs):
        client=self.client(lambda r:httpx.Response(200,json=body))
        client.response_max=2048
        return client.recover_receipt(self.plan,session_id='a'*32,role='clip',**kwargs)

    def test_historical_receipt_is_not_current_source_or_redaction(self):
        for state in ('available','source_expired','retention_expired','deleted','unavailable'):
            body=self.recover(self.receipt(source_state=state))
            self.assertEqual(body['source_state'],state)
            self.assertTrue((self.root/'clip.mp4').exists())

    def test_receipt_all_bindings_are_required(self):
        for change in ({'event_id':'other'},{'request_id':'other'},{'camera_id':'other'},
                       {'role':'reference'},{'source_sha256':'0'*64},{'source_bytes':1},
                       {'accepted':False},{'media_id':None},{'source_state':'ready'}):
            with self.assertRaises(ValueError):self.recover(self.receipt(**change))
        with self.assertRaises(ValueError):self.recover(self.receipt(),expected_media_id='other')

    def test_unknown_and_unauthorized_receipt_never_grants_retry_or_cleanup(self):
        client=self.client(lambda r:httpx.Response(404))
        self.assertIsNone(client.recover_receipt(self.plan,session_id='a'*32,role='clip'))
        client=self.client(lambda r:httpx.Response(403))
        with self.assertRaises(httpx.HTTPStatusError):client.recover_receipt(self.plan,session_id='a'*32,role='clip')
        self.assertTrue((self.root/'clip.mp4').exists())

    def test_epoch_float_rounding_uses_same_wire_timestamp(self):
        plan=replace(self.plan,capture_start=1794503407.426237,capture_end=1794503415.3262372,
            required_start=1794503407.426237,required_end=1794503415.326237)
        self.assertEqual(self.client().upload(plan)['status'],'processing')
        with self.assertRaises(ValueError):self.client().upload(replace(plan,capture_end=plan.required_end+.000002))
