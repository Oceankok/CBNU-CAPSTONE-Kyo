"""Actual SessionRecovery -> authenticated ASGI routes -> isolated PostgreSQL.

No TCP, camera, weights or GPU. The backend fixture encodes a synthetic clip;
consumer cases remove or preserve that registered source before recovery. No operating data.
"""
import os,json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import httpx

from inference_worker.capture_outbox import CaptureOutbox
from inference_worker.continuous_dispatcher import ContinuousDispatcher
from inference_worker.continuous_transport import PrivateClipTransport
from inference_worker.session_recovery import SessionRecovery,RecoveryRequired


@unittest.skipUnless(os.environ.get("PPE_TEST_POSTGRES_URL"),"isolated PostgreSQL required")
class RecoveryAPITests(unittest.TestCase):
    def setUp(self):
        from backend.tests import test_auto_rules_pipeline as fixture
        self.pipe=fixture.AutoRulesPipelineTests()
        self.addCleanup(self.pipe.doCleanups);self.pipe.setUp()
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.box=None;self.dispatcher=None;self.transport=None
        self.addCleanup(self.close)
    def close(self):
        if self.dispatcher:self.dispatcher.close()
        if self.transport:self.transport.close()
        if self.box:self.box.close()
    def open(self):
        return CaptureOutbox(self.root/"journal",private_root=self.root/"raw",
            max_db_bytes=2000000,max_records=1000,max_body_bytes=100000,source_ttl_seconds=30)
    def prepare(self,*,committed=False,late_original=False,lost_incomplete=False,preserve_source=False,queued_close=True):
        p=self.pipe;p.create(mode="off",leave_open=not committed)
        if not preserve_source:p.source.unlink()
        self.box=self.open();self.box.session=p.session
        self.box.bind_runtime({"endpoint":"http://localhost","primary":"CAM_001",
            "clocks":{c:{"offset":0,"error_bound":0} for c in ("CAM_001","CAM_002")}})
        self.box.create_event(p.ident,camera_snapshot=["CAM_001","CAM_002"])
        self.box.enqueue(p.ident,seq=0,kind="start",idempotency_key="start",body=p.start_body)
        self.box.acknowledge(p.ident,0,receipt={"event_id":p.ident})
        if preserve_source:
            import time
            from uuid import uuid4
            from inference_worker.segment_spool import Segment
            for camera in ("CAM_001","CAM_002"):
                directory,sid=uuid4().hex,uuid4().hex
                self.box.begin_capture(p.ident,camera=camera,spool_directory=directory)
                target=self.box.root/directory/(sid+".mp4")
                target.parent.mkdir();target.write_bytes(p.data)
                segment=Segment(sid,0,p.epoch-2,p.epoch+5.2,36,5,len(p.data),time.time())
                self.box.bind_segment(p.ident,camera=camera,segment=segment,relative_path=directory+"/"+sid+".mp4")
        if queued_close:
            self.box.enqueue(p.ident,seq=1,kind="close",idempotency_key="original-complete",body=p.close_body)
            self.original=bytes(self.box.db.execute("SELECT body FROM requests WHERE seq=1").fetchone()[0])
        else:
            from inference_worker.continuous_payloads import update_payload
            self.saved_progress=update_payload(p.progress,session_id=p.session,sequence=1)
            self.box.enqueue(p.ident,seq=1,kind="progress",idempotency_key="last-observation",body=self.saved_progress)
            self.box.acknowledge(p.ident,1,receipt={"event_id":p.ident})
        self.box.close();self.box=self.open()
        state={"late":late_original,"lost":lost_incomplete}
        def handle(request):
            body=json.loads(request.read()) if request.content and request.headers.get("content-type","").startswith("application/json") else {}
            if request.url.path.endswith("/updates") and body.get("complete") is False:
                if state["late"]:
                    state["late"]=False
                    p.client.post(p.base+"/"+p.ident+"/updates",headers=p.node,json=p.close_body).raise_for_status()
                response=p.handle(request)
                if state["lost"] and response.status_code==200:
                    state["lost"]=False
                    raise httpx.ReadTimeout("lost actual API incomplete-close response")
                return response
            return p.handle(request)
        self.transport=PrivateClipTransport(server_url="http://localhost",token=p.node["Authorization"][7:],
            private_root=self.root/"raw",max_upload_bytes=1000000,timeout_seconds=3,total_seconds=10,
            max_response_bytes=100000,transport=httpx.MockTransport(handle))
        self.dispatcher=ContinuousDispatcher(self.box,self.transport,enabled=True,
                                            max_delivery_records=100,max_delivery_db_bytes=2000000)
    def recover(self):
        SessionRecovery(self.box,self.dispatcher,endpoint="http://localhost").run()
    def assert_recovered(self,phase):
        from backend.db.event_repository import get_connection
        p=self.pipe
        self.assertEqual(bytes(self.box.db.execute("SELECT body FROM requests WHERE seq=1").fetchone()[0]),self.original)
        remote=p.client.get(p.base+"/"+p.ident,headers=p.node,params={"session_id":p.session}).raise_for_status().json()
        self.assertEqual(remote["lifecycle"]["phase"],phase)
        self.assertTrue(all(c["capture_report"]["state"]=="incomplete" for c in remote["cameras"]))
        self.assertEqual(self.box.unacknowledged_count(),0)
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM processing_event_operation WHERE event_id=? AND sequence=2",(p.ident,)).fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT count(*) FROM event_review WHERE event_id=?",(p.ident,)).fetchone()[0],0)
        self.assertFalse(p.preview()["rule_evaluation"]["eligible"])
    def test_committed_complete_response_lost_source_gone_actual_receipt(self):
        self.prepare(committed=True);self.recover();self.assert_recovered("closed")
    def test_uncommitted_complete_is_atomically_retired_actual_incomplete_close(self):
        self.prepare();self.recover();self.assert_recovered("interrupted")
    def test_late_complete_commit_race_keeps_server_operation_and_reports_incomplete(self):
        self.prepare(late_original=True);self.recover();self.assert_recovered("closed")
    def test_lost_incomplete_response_uses_original_session_receipt(self):
        self.prepare(lost_incomplete=True);self.recover();self.assert_recovered("interrupted")


    def test_still_owned_complete_sources_are_delivered_after_restart_without_reclassification(self):
        self.prepare(preserve_source=True)
        self.recover()
        p=self.pipe
        remote=p.client.get(p.base+"/"+p.ident,headers=p.node,params={"session_id":p.session}).raise_for_status().json()
        self.assertEqual(remote["lifecycle"]["phase"],"closed")
        self.assertTrue(all(c["capture_report"]["state"]=="complete" for c in remote["cameras"]))
        self.assertTrue(all(s["status"]=="received" for s in remote["segments"]))
        self.assertTrue(all(r["state"]=="accepted" for r in self.dispatcher.status()))
        self.assertEqual(bytes(self.box.db.execute("SELECT body FROM requests WHERE seq=1").fetchone()[0]),self.original)
        self.assertEqual(self.box.db.execute("SELECT count(*) FROM retired_requests").fetchone()[0],0)
        p.no_reviews()

    def test_open_episode_closes_from_durable_last_observation_without_resuming_identity(self):
        self.prepare(queued_close=False)
        self.assertNotEqual(self.box.session,self.pipe.session)
        self.recover()
        p=self.pipe
        remote=p.client.get(p.base+"/"+p.ident,headers=p.node,params={"session_id":p.session}).raise_for_status().json()
        self.assertEqual(remote["lifecycle"]["phase"],"interrupted")
        close=json.loads(self.box.db.execute("SELECT body FROM requests WHERE kind='incomplete'").fetchone()[0])
        self.assertEqual(close["close_reason"],"process_restart")
        self.assertEqual(close["session_id"],p.session)
        self.assertEqual(close["timestamp_end"],self.saved_progress["timestamp_end"])
        self.assertEqual(close["frame_sample_count"],self.saved_progress["frame_sample_count"])
        self.assertTrue(all(c["capture_report"]["state"]=="incomplete" for c in remote["cameras"]))
        self.assertEqual(self.box.unacknowledged_count(),0)
        p.no_reviews()
