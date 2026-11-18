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


@unittest.skipUnless(os.environ.get("PPE_TEST_POSTGRES_URL") and __import__("shutil").which("ffmpeg"),
                     "isolated PostgreSQL and FFmpeg required")
class SettledProducerAPITests(unittest.TestCase):
    setUp=RecoveryAPITests.setUp
    close=RecoveryAPITests.close
    open=RecoveryAPITests.open

    def produce(self,*,incomplete=False,lost_complete=False,interrupted_healthy=False,expire_after_collecting=False,normal_stop=False):
        import numpy as np
        from inference_worker.cleanup_supervisor import CleanupSupervisor
        from inference_worker.continuous_runtime import ContinuousFrameRuntime
        from inference_worker.eos_delivery import delivery_snapshot
        from inference_worker.lifecycle_journal import LifecycleJournal
        from inference_worker.multicamera_capture import CameraClock,MultiCameraCapture
        p=self.pipe;p.settings("off")
        self.box=self.open()
        self.box.bind_runtime({"endpoint":"http://localhost","primary":"CAM_001",
            "clocks":{c:{"offset":0,"error_bound":0} for c in ("CAM_001","CAM_002")}})
        self.calls=[];self.lose_complete=lost_complete;self.complete_response_lost=False;self.expired_segment=None
        def handle(request):
            self.calls.append((request.method,request.url.path))
            response=p.handle(request)
            if (expire_after_collecting and self.expired_segment is None
                    and request.url.path.endswith('/capture-reports')
                    and json.loads(request.read()).get('state')=='collecting' and response.status_code==200):
                row=self.box.db.execute("SELECT id FROM segments WHERE camera='CAM_001' AND state='present' ORDER BY start LIMIT 1").fetchone()
                self.assertIsNotNone(row)
                self.expired_segment=row['id']
                self.box.request_cleanup(row['id'],reason='source_expired_or_clock_rollback')
            if (self.lose_complete and request.url.path.endswith("/updates")
                    and json.loads(request.read()).get("complete") is True and response.status_code==200):
                self.complete_response_lost=True
                raise httpx.ReadTimeout("native committed COMPLETE response lost")
            return response
        self.transport=PrivateClipTransport(server_url="http://localhost",token=p.node["Authorization"][7:],
            private_root=self.root/"raw",max_upload_bytes=1000000,timeout_seconds=3,total_seconds=10,
            max_response_bytes=100000,transport=httpx.MockTransport(handle))
        self.dispatcher=ContinuousDispatcher(self.box,self.transport,enabled=True,
            max_delivery_records=100,max_delivery_db_bytes=2000000)
        capture=MultiCameraCapture(self.root/"raw",cameras={c:CameraClock(0,0,True) for c in ("CAM_001","CAM_002")},
            max_clock_error=0,pre_seconds=2,post_seconds=2,discovery_delay_limit=3.5,prebuffer_seconds=6,
            prebuffer_bytes=1000000,max_buffer_frames=60,max_events=2,global_spool_bytes=4000000,
            spool_config=dict(max_bytes=1000000,max_segments=20,max_age_seconds=60,max_attempts=3),
            writer_config=dict(segment_seconds=2,output_fps=2,max_frame_pixels=1024,max_input_gap_seconds=.6,encoder_timeout_seconds=5))
        journal=LifecycleJournal(self.box,camera_id="CAM_001",camera_snapshot=["CAM_001","CAM_002"],
            model_version="fixture-model",missing_grace_seconds=1,threshold_seconds=3,max_sample_gap_seconds=.6,
            progress_emit_interval_seconds=1,rewear_seconds=.5)
        runtime=ContinuousFrameRuntime(journal=journal,capture=capture,dispatcher=self.dispatcher,metadata_steps_per_pump=30)
        self.addCleanup(lambda:runtime.stop("test_cleanup"))
        frame=np.zeros((32,32,3),np.uint8)
        for step in range(-4,0):
            for camera in ("CAM_002","CAM_001"):
                runtime.frame(camera,p.epoch+step*.5,frame,assessments=[] if camera=="CAM_001" else None)
        if incomplete:runtime.end_source("CAM_002","source_failed")
        starts=[]
        for step in range(19):
            now=p.epoch+step*.5
            if normal_stop and step==11:runtime.set_observation_cutoff('CAM_001',now)
            if not incomplete:runtime.frame("CAM_002",now,frame)
            transitions=runtime.frame("CAM_001",now,frame,assessments=[{"tracking_id":"native-track",
                "ppe":{"helmet":{"status":"suspected_no_helmet" if step<12 else "helmet"}}}])
            starts+=transitions
            if interrupted_healthy:
                for row in transitions:
                    if row['kind']=='start':self.box.mark_incomplete(row['event_id'],reason='observation_interrupted')
            runtime.pump(now=now)
            if self.complete_response_lost:break
        self.event=next(r["event_id"] for r in starts if r["kind"]=="start")
        self.session=self.box.session
        if lost_complete:
            self.assertTrue(self.complete_response_lost)
            self.assertIsNone(self.box.db.execute("SELECT ack FROM requests WHERE kind='close'").fetchone()[0])
            runtime.stop("simulated_process_exit")
            return
        for _ in range(32):
            runtime.pump(now=now)
            spools={cap.spool.root.name:cap.spool for event in capture.events.values()
                    for cap in event["captures"].values() if cap.spool}
            CleanupSupervisor(self.box,spools=spools,verified_receipt=self.dispatcher.cleanup_receipt_verified).run_once()
            runtime.release_finished()
            pending=delivery_snapshot(runtime)
            if not any(pending[k] for k in ("metadata","close_intents","uploads","sources","recovery","captures")):break
        self.assertTrue(all(not pending[k] for k in ("metadata","close_intents","uploads","sources","recovery","captures")),pending)
        self.assertEqual(list((self.root/"raw").iterdir()),[])
        self.assertEqual(self.box.db.execute("SELECT count(*) FROM recovery_states").fetchone()[0],0)
        self.event=next(r["event_id"] for r in starts if r["kind"]=="start")
        self.session=self.box.session
        runtime.stop("test_stop")

    def process_synthetic_sources(self):
        import cv2
        # This is the existing synthetic result-provider pattern, not a face model.
        p=self.pipe
        for _ in range(32):
            job=p.client.post("/api/processing/claim",headers=p.node).raise_for_status().json()["job"]
            if job is None:return
            headers={**p.node,"X-Processing-Lease":job["lease_token"]}
            data=p.client.get(job["source_url"],headers=headers).raise_for_status().content
            source=self.root/"synthetic-result-provider.mp4";source.write_bytes(data)
            video=cv2.VideoCapture(str(source))
            count=int(video.get(cv2.CAP_PROP_FRAME_COUNT));video.release();self.assertGreater(count,0)
            metadata={"faces_detected":0,"processed_frames":count,"redaction_mode":"scrfd",
                      "inference_backend":"synthetic-output-provider","processing_seconds":.01}
            result_headers={**headers,"Content-Type":"video/mp4","X-Processing-Metadata":json.dumps(metadata)}
            p.client.post("/api/processing/jobs/"+job["job_id"]+"/result",headers=result_headers,content=data).raise_for_status()
            p.client.post("/api/processing/jobs/"+job["job_id"]+"/cleanup",headers=p.node).raise_for_status()
        self.fail("synthetic processing queue did not drain")

    def restart_without_io(self):
        self.dispatcher.close();self.box.close();self.box=self.open()
        self.dispatcher=ContinuousDispatcher(self.box,self.transport,enabled=True,
            max_delivery_records=100,max_delivery_db_bytes=2000000)
        self.assertNotEqual(self.box.session,self.session)
        before=self.box.db.total_changes
        markers=self.box.db.execute("SELECT count(*) FROM recovery_states").fetchone()[0]
        self.calls.clear()
        self.assertEqual(SessionRecovery(self.box,self.dispatcher,endpoint="http://localhost").run(),[self.event])
        self.assertEqual(self.calls,[])
        self.assertEqual(self.box.db.total_changes,before)
        self.assertEqual(self.box.db.execute("SELECT count(*) FROM recovery_states").fetchone()[0],markers)

    def test_normal_producer_received_cleaned_reviewed_history_restarts_without_io(self):
        self.produce();self.process_synthetic_sources()
        p=self.pipe
        p.client.post("/api/events/"+self.event+"/review",headers=p.admin,
            json={"review_result":"confirmed","review_reason_code":"confirmed_no_helmet"}).raise_for_status()
        close=json.loads(self.box.db.execute("SELECT body FROM requests WHERE kind='close'").fetchone()[0])
        proof=p.client.post(p.base+"/"+self.event+"/operation-receipt",headers=p.node,json=close).raise_for_status().json()
        self.assertIn("already_reviewed",proof["recovery_blockers"])
        self.restart_without_io()
        from backend.db.event_repository import get_connection
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM event_review WHERE event_id=?",(self.event,)).fetchone()[0],1)

    def test_normal_incomplete_producer_is_settled_and_hold_does_not_confirm_unredacted_media(self):
        self.produce(incomplete=True)
        reports=[json.loads(r[0]) for r in self.box.db.execute("SELECT body FROM requests WHERE kind='capture_report' ORDER BY seq")]
        self.assertEqual([r['state'] for r in reports].count('collecting'),1)
        self.assertEqual({r['state'] for r in reports},{'collecting','complete','incomplete'})
        p=self.pipe
        response=p.client.post("/api/events/"+self.event+"/review",headers=p.admin,
            json={"review_result":"confirmed","review_reason_code":"confirmed_no_helmet"})
        self.assertEqual(response.status_code,409,response.text)
        self.assertIn("usable_redacted_media_required",response.text)
        response=p.client.post("/api/events/"+self.event+"/review",headers=p.admin,
            json={"review_result":"hold","review_reason_code":"unclear"})
        self.assertEqual(response.status_code,200,response.text)
        from backend.db.event_repository import get_connection
        from backend.processing.lifecycle import require_finalizable
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT review_result FROM event_review WHERE event_id=?",(self.event,)).fetchone()[0],"hold")
            with self.assertRaisesRegex(ValueError,"continuous_event_not_closed"):
                require_finalizable(conn,self.event)
        self.restart_without_io()

    def test_interrupted_lifecycle_with_healthy_cameras_restarts_without_io(self):
        self.produce(interrupted_healthy=True)
        reports=[json.loads(r[0]) for r in self.box.db.execute("SELECT body FROM requests WHERE kind='capture_report' ORDER BY seq")]
        self.assertEqual([r['state'] for r in reports].count('complete'),2)
        self.assertNotIn('incomplete',[r['state'] for r in reports])
        self.restart_without_io()

    def test_normal_stop_with_complete_postroll_restarts_and_still_requires_automatic_review(self):
        self.produce(normal_stop=True)
        reports=[json.loads(r[0]) for r in self.box.db.execute("SELECT body FROM requests WHERE kind='capture_report'")]
        self.assertEqual(len(reports),2)
        self.assertEqual({r['state'] for r in reports},{'complete'})
        self.assertTrue(all(not r['missing_ranges'] for r in reports))
        close=json.loads(self.box.db.execute("SELECT body FROM requests WHERE kind='close'").fetchone()[0])
        self.assertEqual(close['close_reason'],'operator_stop')
        self.assertTrue(close['complete'])
        self.process_synthetic_sources()
        self.pipe.ident=self.event
        result=self.pipe.preview()['rule_evaluation']
        self.assertFalse(result['eligible'])
        self.assertIn('abnormal_event_close_requires_review',result['blocking_reasons'])
        self.restart_without_io()

    def test_partial_report_missing_ack_or_tampered_body_is_not_certified(self):
        self.produce(incomplete=True)
        recovery=SessionRecovery(self.box,self.dispatcher,endpoint='http://localhost')
        event=self.box.db.execute('SELECT * FROM events WHERE id=?',(self.event,)).fetchone()
        self.assertTrue(recovery._settled_history(event))
        row=self.box.db.execute("SELECT seq,body,ack FROM requests WHERE kind='capture_report' ORDER BY seq LIMIT 1").fetchone()
        self.box.db.execute('UPDATE requests SET ack=NULL WHERE event=? AND seq=?',(self.event,row['seq']))
        self.assertFalse(recovery._settled_history(event))
        self.box.db.execute('UPDATE requests SET ack=?,body=? WHERE event=? AND seq=?',(row['ack'],b'{}',self.event,row['seq']))
        self.assertFalse(recovery._settled_history(event))

    def test_expired_collecting_source_is_settled_after_hold_without_io(self):
        self.produce(incomplete=True,expire_after_collecting=True)
        self.assertIsNotNone(self.expired_segment)
        rows=self.box.db.execute("SELECT body FROM requests WHERE kind='capture_report' AND idem LIKE 'CAM_001:%' ORDER BY seq").fetchall()
        reports=[json.loads(r[0]) for r in rows]
        self.assertEqual([r['state'] for r in reports],['collecting','incomplete'])
        self.assertEqual(reports[0]['captured_ranges'],reports[1]['captured_ranges'])
        self.assertTrue(reports[1]['missing_ranges'])
        self.assertTrue(all(r['reason']=='source_segment_unavailable' for r in reports[1]['missing_ranges']))
        self.assertEqual(self.box.db.execute('SELECT state FROM segments WHERE id=?',(self.expired_segment,)).fetchone()[0],'unavailable')
        self.assertIsNone(self.dispatcher.db.execute('SELECT 1 FROM uploads WHERE segment=?',(self.expired_segment,)).fetchone())
        self.pipe.client.post('/api/events/'+self.event+'/review',headers=self.pipe.admin,
            json={'review_result':'hold','review_reason_code':'unclear'}).raise_for_status()
        recovery=SessionRecovery(self.box,self.dispatcher,endpoint='http://localhost')
        event=self.box.db.execute('SELECT * FROM events WHERE id=?',(self.event,)).fetchone()
        self.assertTrue(recovery._settled_history(event))
        segment=self.box.db.execute('SELECT reason FROM segments WHERE id=?',(self.expired_segment,)).fetchone()
        self.box.db.execute('UPDATE segments SET reason=NULL WHERE id=?',(self.expired_segment,))
        self.assertFalse(recovery._settled_history(event))
        self.box.db.execute('UPDATE segments SET reason=? WHERE id=?',(segment['reason'],self.expired_segment))
        self.restart_without_io()

    def test_committed_lost_complete_recovers_then_reviewed_second_restart_is_a_noop(self):
        from inference_worker.session_recovery import dispose_previous_sources
        self.produce(lost_complete=True)
        self.lose_complete=False
        self.dispatcher.close();self.box.close();self.box=self.open()
        self.dispatcher=ContinuousDispatcher(self.box,self.transport,enabled=True,
            max_delivery_records=100,max_delivery_db_bytes=2000000)
        SessionRecovery(self.box,self.dispatcher,endpoint="http://localhost").run()
        dispose_previous_sources(self.box,
            spool_config=dict(max_bytes=1000000,max_segments=20,max_age_seconds=60,max_attempts=3),
            verified_receipt=self.dispatcher.cleanup_receipt_verified)
        self.assertEqual(list((self.root/"raw").iterdir()),[])
        ack=json.loads(self.box.db.execute("SELECT ack FROM requests WHERE kind='close'").fetchone()[0])
        self.assertNotIn("lifecycle",ack)
        self.assertEqual(ack["operation_receipt"]["receipt_status"],"committed_exact")
        self.assertEqual(self.box.db.execute("SELECT state FROM recovery_states").fetchone()[0],"reconciled")
        self.process_synthetic_sources()
        p=self.pipe
        p.client.post("/api/events/"+self.event+"/review",headers=p.admin,
            json={"review_result":"confirmed","review_reason_code":"confirmed_no_helmet"}).raise_for_status()
        close=json.loads(self.box.db.execute("SELECT body FROM requests WHERE kind='close'").fetchone()[0])
        proof=p.client.post(p.base+"/"+self.event+"/operation-receipt",headers=p.node,json=close).raise_for_status().json()
        self.assertIn("already_reviewed",proof["recovery_blockers"])
        self.restart_without_io()
