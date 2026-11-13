from dataclasses import replace
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import httpx
from inference_worker.continuous_transport import PrivateClipTransport, UploadPlan


class TransportTests(TestCase):
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
        (self.root/'link.mp4').symlink_to(self.root/'clip.mp4')
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
        with self.assertRaises(RuntimeError):client.recover_receipt(self.plan,session_id='a'*32,role='clip')
        self.assertTrue((self.root/'clip.mp4').exists())

    def test_epoch_float_rounding_uses_same_wire_timestamp(self):
        plan=replace(self.plan,capture_start=1794503407.426237,capture_end=1794503415.3262372,
            required_start=1794503407.426237,required_end=1794503415.326237)
        self.assertEqual(self.client().upload(plan)['status'],'processing')
        with self.assertRaises(ValueError):self.client().upload(replace(plan,capture_end=plan.required_end+.000002))
