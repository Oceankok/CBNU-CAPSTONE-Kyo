"""Original-session close reconciliation, with immutable local commands and CAS."""
from copy import deepcopy
import hashlib,json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from types import SimpleNamespace
import httpx
from inference_worker.capture_outbox import CaptureOutbox
from inference_worker.continuous_dispatcher import ContinuousDispatcher
from inference_worker.continuous_transport import PrivateClipTransport
from inference_worker.session_recovery import SessionRecovery,RecoveryRequired


class RecoveryFixture(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.box=self.open()
        self.original_session=self.box.session;self.event="EVT_"+"1"*32
        self.box.bind_runtime({"endpoint":"https://test","primary":"main",
                              "clocks":{"main":{"offset":0,"error_bound":0}}})
        self.start=dict(event_id=self.event,camera_id="main",tracking_id="t",ppe_type="vest",
            timestamp_start="2026-01-01T00:00:00+00:00",timestamp_end="2026-01-01T00:00:03+00:00",
            confidence=None,frame_sample_count=13,review_only=True,model_version="fixture",
            session_id=self.original_session,policy_key="policy",observed_seconds=3.,gap_seconds=0.,gap_count=0)
        self.close=dict(session_id=self.original_session,sequence=1,kind="close",
            timestamp_end=self.start["timestamp_end"],frame_sample_count=13,confidence=None,
            observed_seconds=3.,gap_seconds=0.,gap_count=0,close_reason="ppe_present",
            complete=True,final_segment_count=1)
        self.box.create_event(self.event,camera_snapshot=["main"])
        self.box.enqueue(self.event,seq=0,kind="start",idempotency_key="start",body=self.start)
        self.box.acknowledge(self.event,0,receipt={"event_id":self.event})
        self.box.enqueue(self.event,seq=1,kind="close",idempotency_key="original-close",body=self.close)
        self.original_bytes=bytes(self.box.db.execute("SELECT body FROM requests WHERE seq=1").fetchone()[0])
        self.box.close();self.box=self.open()
        self.operations={};self.reports={};self.seen=[];self.blockers=[]
        self.late_original=False;self.lose_disposition=False;self.after_post=False
        self.transport=PrivateClipTransport(server_url="https://test",token="fixture",private_root=self.root/"raw",
            max_upload_bytes=1024,timeout_seconds=1,total_seconds=2,max_response_bytes=30000,
            transport=httpx.MockTransport(self.handler))
        self.dispatcher=ContinuousDispatcher(self.box,self.transport,enabled=True,
                                            max_delivery_records=20,max_delivery_db_bytes=2000000)
        self.addCleanup(self.cleanup)
    def open(self):
        return CaptureOutbox(self.root/"journal",private_root=self.root/"raw",max_db_bytes=2000000,
                             max_records=300,max_body_bytes=30000,source_ttl_seconds=30)
    def cleanup(self):
        self.dispatcher.close();self.transport.close();self.box.close()
    def public(self):
        operation=self.operations.get(1)
        return {"event_id":self.event,"lifecycle":{"phase":"closed" if operation and operation["complete"] else "interrupted" if operation else "open",
                "revision":1 if operation else 0},
            "segments":[{"camera_id":"main","sequence":0,"role":"clip","status":"pending"}],
            "cameras":[{"camera_id":"main","role":"clip","capture_report":self.reports.get("main"),
                "required_start_at":"2025-12-31T23:59:58+00:00","required_end_at":"2026-01-01T00:00:05+00:00"}]}
    def handler(self,request):
        body=json.loads(request.read()) if request.content else None
        self.seen.append((request.method,request.url.path,body))
        if request.url.path.endswith("/operation-receipt"):
            prior=self.operations.get(body["sequence"])
            sha=lambda b:hashlib.sha256(json.dumps(b,sort_keys=True).encode()).hexdigest()
            return httpx.Response(200,json={"event_id":self.event,"session_id":body["session_id"],
                "sequence":body["sequence"],"submitted_sha256":sha(body),
                "receipt_status":"not_committed" if prior is None else "committed_exact" if prior==body else "committed_different",
                "accepted_operation":{"sha256":sha(prior),"body":prior} if prior else None,
                "lifecycle":self.public()["lifecycle"],"recovery_blockers":self.blockers,
                "may_attempt_new_update":not prior and not self.blockers})
        if request.method=="GET":return httpx.Response(200,json=self.public())
        if request.url.path.endswith("/updates"):
            if self.late_original and body.get("complete") is False:
                self.operations[1]=deepcopy(self.close);self.late_original=False
            previous=self.operations.get(body["sequence"])
            if previous is not None and previous!=body:return httpx.Response(409,json={"detail":"continuous_sequence_conflict"})
            self.operations[body["sequence"]]=deepcopy(body);self.after_post=True
            if self.lose_disposition:
                self.lose_disposition=False;raise httpx.ReadTimeout("lost after commit")
            kind="close"
        elif request.url.path.endswith("/capture-reports"):
            old=self.reports.get("main")
            if old and old!=body:return httpx.Response(409)
            self.reports["main"]=deepcopy(body);kind="capture_report"
        else:raise AssertionError(request.url)
        return httpx.Response(200,json={**self.public(),"accepted_command":{"kind":kind,
            "session_id":body["session_id"],"sequence":body["sequence"],
            "camera_id":"main" if kind=="capture_report" else None}})
    def recover(self):
        return SessionRecovery(self.box,self.dispatcher,endpoint="https://test").run()
    def immutable(self):
        self.assertEqual(bytes(self.box.db.execute("SELECT body FROM requests WHERE seq=1").fetchone()[0]),self.original_bytes)
        self.assertEqual(len(self.operations),1)
        self.assertEqual(self.reports["main"]["state"],"incomplete")
        self.assertEqual(self.box.unacknowledged_count(),0)
    def test_committed_exact_close_with_lost_response_is_acknowledged_not_rewritten(self):
        self.operations[1]=deepcopy(self.close)
        self.recover();self.immutable()
        self.assertTrue(self.operations[1]["complete"])
        self.assertFalse(any(path.endswith("/updates") for _,path,_ in self.seen))
    def test_not_committed_complete_without_source_is_retired_and_dispositioned(self):
        self.recover();self.immutable()
        self.assertFalse(self.operations[1]["complete"])
        self.assertEqual(self.box.db.execute("SELECT reason FROM retired_requests").fetchone()[0],
                         "source_loss_uncommitted_complete")
    def test_late_old_complete_wins_without_overwrite(self):
        self.late_original=True;self.recover();self.immutable()
        self.assertTrue(self.operations[1]["complete"])
        self.assertEqual(self.box.db.execute("SELECT count(*) FROM retired_requests").fetchone()[0],2)
    def test_lost_incomplete_response_recovers_exact(self):
        self.lose_disposition=True;self.recover();self.immutable()
        self.assertFalse(self.operations[1]["complete"])
    def test_protected_and_different_operations_remain_pending(self):
        self.blockers=["already_reviewed"]
        with self.assertRaises(RecoveryRequired):self.recover()
        self.assertEqual(self.box.unacknowledged_count(),1)
        self.assertEqual(self.reports,{})
        self.blockers=[];self.operations[1]={**self.close,"complete":False,"close_reason":"other"}
        with self.assertRaises(RecoveryRequired):self.recover()
        self.assertEqual(self.box.unacknowledged_count(),1)
    def test_crash_before_retirement_append_rolls_back_both(self):
        original=self.box.enqueue
        def fail(*args,**kwargs):
            if kwargs.get("kind")=="incomplete":raise OSError("disk failure before disposition")
            return original(*args,**kwargs)
        with patch.object(self.box,"enqueue",side_effect=fail):
            with self.assertRaises(OSError):self.recover()
        self.assertEqual(self.box.db.execute("SELECT count(*) FROM retired_requests").fetchone()[0],0)
        self.recover();self.immutable()
    def test_crash_after_retirement_before_network_keeps_disposition(self):
        recovery=SessionRecovery(self.box,self.dispatcher,endpoint="https://test")
        recovery._terminal_ambiguities(self.event,self.original_session)
        self.assertEqual(self.box.db.execute("SELECT count(*) FROM retired_requests").fetchone()[0],1)
        self.assertEqual(self.box.db.execute("SELECT count(*) FROM requests WHERE kind='incomplete'").fetchone()[0],1)
        # Reopen process-owned ledgers; no identity/capture coordinator is restored.
        self.dispatcher.close();self.box.close();self.box=self.open()
        self.dispatcher=ContinuousDispatcher(self.box,self.transport,enabled=True,
                                            max_delivery_records=20,max_delivery_db_bytes=2000000)
        self.recover();self.immutable()

