"""Model-free contract, recovery-status and degraded-source regression tests."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock, patch
import numpy as np
import httpx
from inference_worker.detection_contract import DetectionContractError, validate_detections
from inference_worker.assessment_pipeline import assess_detection_frame, detect_assess
from inference_worker.person_tracker import PersonTracker
from inference_worker.violation_tracker import ViolationTracker
from inference_worker.pending_events import EventSaveOutbox
from inference_worker import test_continuous_source_reader as source_fixture
from inference_worker import test_server_mock as server_fixture
from inference_worker import test_continuous_runtime as runtime_fixture

PERSON={"class_name":"person","confidence":.95,"bbox":[30,20,130,220]}
VEST={"class_name":"vest","confidence":.9,"bbox":[50,70,110,145]}
FRAME=np.zeros((300,400,3),np.uint8)


class DetectorInputTests(unittest.TestCase):
    def test_missing_ppe_score_is_unknown_not_absence_with_identity_preserved(self):
        track,timer=PersonTracker(),ViolationTracker()
        for step in range(100):
            rows=assess_detection_frame([PERSON,{k:v for k,v in VEST.items() if k!="confidence"}],FRAME.shape)
            self.assertEqual(rows[0]["ppe"]["vest"]["status"],"unknown")
            self.assertIsNone(rows[0]["ppe"]["vest"]["verified_ppe_confidence"])
            self.assertEqual(timer.update(track.update(rows,step*.25),step*.25),[])
        self.assertEqual(track.update(rows,25)[0]["tracking_id"],"track-1")

    def test_invalid_person_score_never_fabricates_track(self):
        tracker=PersonTracker()
        for i,value in enumerate((None,True,-1,1.1,float("nan"),"0.9")):
            rows=assess_detection_frame([{**PERSON,"confidence":value}],FRAME.shape)
            self.assertEqual(rows,[])
            self.assertEqual(tracker.update(rows,i),[])

    def test_bad_geometry_and_incomplete_frame_preserve_uncertainty(self):
        for bad in ({**VEST,"bbox":[1,2,float("nan"),4]},
                    {k:v for k,v in VEST.items() if k!="bbox"},
                    {**VEST,"confidence":None}):
            rows=assess_detection_frame([PERSON,bad],FRAME.shape)
            self.assertTrue(all(v["status"]=="unknown" for v in rows[0]["ppe"].values()))

    def test_review_mode_keeps_vest_even_with_explicit_negative_class(self):
        negative={"class_name":"no_helmet","confidence":.9,"bbox":[60,22,90,50]}
        rows=assess_detection_frame([PERSON,negative],FRAME.shape,mode="review")
        self.assertEqual(set(rows[0]["ppe"]),{"helmet","vest"})
        self.assertTrue(all(v["verified_ppe_confidence"] is None for v in rows[0]["ppe"].values()))
        verified=assess_detection_frame([PERSON,negative],FRAME.shape,mode="verified_helmet")
        self.assertEqual(verified[0]["verified_ppe_confidence"],.9)

    def test_unsupported_ppe_remains_unknown(self):
        rows=assess_detection_frame([PERSON],FRAME.shape,supported_ppe={"helmet"})
        self.assertEqual(rows[0]["ppe"]["vest"]["reason"],"unsupported_ppe")

    def test_adapter_failure_is_not_successful_empty_detection(self):
        detector=SimpleNamespace(detect=Mock(side_effect=DetectionContractError("missing score")))
        self.assertEqual(detect_assess(detector,FRAME),[])
        # Genuine model execution errors are not silently converted to absence.
        detector.detect.side_effect=RuntimeError("model execution failed")
        with self.assertRaises(RuntimeError):detect_assess(detector,FRAME)

    def test_class_binding_is_checked_without_fixed_numeric_order(self):
        validate_detections([{**PERSON,"class_id":7}],class_map={7:"person"})
        with self.assertRaises(DetectionContractError):
            validate_detections([{**PERSON,"class_id":7}],class_map={7:"vest"})

    def test_raw_adapter_checks_missing_score_before_threshold_filter(self):
        from inference_worker.ppe_detector import PPEDetector
        detector=PPEDetector.__new__(PPEDetector);detector.confidence=.5
        detector.model=Mock(names={0:"person",1:"helmet",2:"vest"})
        detector.model.predict.return_value=[SimpleNamespace(boxes=[
            SimpleNamespace(cls=np.array(2),xyxy=np.array([[1,2,20,40]]))])]
        with self.assertRaises(DetectionContractError):
            detector.detect(FRAME)


class LegacyClipDispositionTests(unittest.TestCase):
    def event(self):
        from inference_worker.test_pending_events import event
        return event()

    def test_restart_reports_unfinished_clip_without_raw_or_new_event(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/"queue.sqlite"
            body=self.event()
            box=EventSaveOutbox(path,camera_id="CAM_001")
            box.enqueue(body);box.arm_clip(body["event_id"])
            box.pump(lambda b:b,now=0);box.close()
            box=EventSaveOutbox(path,camera_id="CAM_001")
            client=server_fixture.FakeVideoClient()
            box.pump_clip_reports(client,now=0)
            self.assertEqual(client.clip_failures,[(body["event_id"],"test-request","process_restart_before_clip","unavailable")])
            self.assertEqual(client.submitted_events,[])
            self.assertEqual(box.clip_counts(),{});box.close()

    def test_lost_failure_response_recovers_terminal_without_downgrade(self):
        body=self.event();box=EventSaveOutbox(":memory:",camera_id="CAM_001")
        self.addCleanup(box.close);box.arm_clip(body["event_id"]);box.unavailable_clips("source_ended")
        client=server_fixture.FakeVideoClient()
        original=client.report_clip_failure
        def lost(*args,**kwargs):
            original(*args,**kwargs)
            raise httpx.ReadTimeout("lost")
        client.report_clip_failure=lost
        box.pump_clip_reports(client,now=0)
        self.assertEqual(box.clip_counts(),{"pending":1})
        client.get_clip_requests=lambda _:{"items":[{"camera_id":"CAM_001","role":"clip","status":"received","request_id":"test-request"}]}
        box.pump_clip_reports(client,now=1)
        self.assertEqual(len(client.clip_failures),1)
        self.assertEqual(box.clip_counts(),{})

    def test_failed_status_retries_are_bounded_and_keep_diagnostic(self):
        body=self.event();box=EventSaveOutbox(":memory:",camera_id="CAM_001")
        self.addCleanup(box.close);box.arm_clip(body["event_id"]);box.unavailable_clips("source_ended")
        client=server_fixture.FakeVideoClient()
        client.get_clip_requests=Mock(side_effect=httpx.ReadTimeout("no response"))
        for step in range(10):box.pump_clip_reports(client,now=step*40)
        self.assertEqual(client.get_clip_requests.call_count,6)
        self.assertEqual(box.clip_counts(),{"manual":1})


class ReferenceDegradationTests(unittest.TestCase):
    def make(self):
        client,reader=source_fixture.ReaderTests().make()
        reader.degrade_references=True
        return client,reader

    def test_initial_missing_reference_primary_continues_without_clean_eos(self):
        c,r=self.make();c.streams=c.streams[:1]
        frames=r.poll()
        self.assertEqual([row[0] for row in frames],["main","main"])
        self.assertEqual(r.end_reasons["ref"],"source_failed")
        self.assertIn("ref",r.mux.failed_sources)
        self.assertEqual(r.mux.cameras,("main","ref"))

    def test_midstream_reference_missing_primary_keeps_source_identity(self):
        c,r=self.make();r.poll();c.seq=1;c.streams=c.streams[:1]
        frames=r.poll()
        self.assertEqual([row[0] for row in frames],["main","main"])
        self.assertEqual(r.streams["main"],"a");self.assertEqual(r.sequence["main"],1)
        self.assertEqual(r.end_reasons["ref"],"source_failed")

    def test_reference_stall_does_not_stop_primary_and_does_not_reattach(self):
        c,r=self.make();frames=r.poll()
        t=frames[-1][1];r.expire_reference_stalls(now=t+2,max_lag_seconds=1)
        self.assertEqual(r.end_reasons["ref"],"source_failed")
        c.seq=1
        self.assertEqual([row[0] for row in r.poll()],["main","main"])
        self.assertNotIn("ref",r.mux.failure or "")
        # A genuinely fresh reader may bind reference again, with fresh session.
        c2,r2=self.make()
        self.assertEqual(len(r2.poll()),4)
        self.assertEqual(r2.reference_failures,{})

    def test_primary_failure_still_fails_instead_of_mislabelling_reference(self):
        c,r=self.make();r.poll();c.seq=1;c.streams=c.streams[1:]
        with self.assertRaises(Exception):r.poll()
        self.assertNotIn("main",r.mux.failed_sources)

    def test_chunk_auth_remains_global_http_failure(self):
        from inference_worker.continuous_source_reader import bounded_chunk
        for status in (401,403):
            with httpx.Client(base_url="http://test",transport=httpx.MockTransport(lambda _:httpx.Response(status))) as client:
                with self.assertRaises(httpx.HTTPStatusError):
                    bounded_chunk(SimpleNamespace(client=client),"chunk",100)


class HarnessTests(unittest.TestCase):
    def detector(self,rows=None):
        manifest={"contract":"scored_xyxy_v1","artifact_sha256":"a"*64,
            "class_map":{0:"person",1:"helmet",2:"vest",3:"no_helmet"},
            "confidence_semantics":"detection_score_only","coordinate_basis":"original_frame_xyxy"}
        return SimpleNamespace(contract_manifest=manifest,model_version="fixture",
            supported_ppe={"helmet","vest"},
            detect=lambda _:deepcopy(rows if rows is not None else [{**PERSON,"class_id":0}]))
    def test_review_harness_has_two_ppe_and_single_episode_each(self):
        from inference_worker.validation_harness import replay_frames
        out=replay_frames(self.detector(),[(i*.25,FRAME) for i in range(41)])
        starts=[r for r in out["transitions"] if r["kind"]=="start"]
        self.assertEqual({r["ppe_type"] for r in starts},{"helmet","vest"})
        self.assertEqual(len(starts),2)
        self.assertTrue(all(r["review_only"] and r["verified_ppe_confidence"] is None for r in starts))
        self.assertFalse(out["collection_complete"])
        self.assertFalse(out["automatic_confirmation_enabled"])
    def test_new_unscored_or_changed_class_io_is_rejected_not_adapted(self):
        from inference_worker.validation_harness import replay_frames
        bad=self.detector([{k:v for k,v in {**PERSON,"class_id":0}.items() if k!="confidence"}])
        with self.assertRaises(DetectionContractError):replay_frames(bad,[(0,FRAME)])
        bad=self.detector([{**PERSON,"class_id":9}])
        with self.assertRaises(DetectionContractError):replay_frames(bad,[(0,FRAME)])
    def test_cache_requires_exact_artifact_and_scored_contract(self):
        from inference_worker.validation_harness import validate_cached_detections
        detector=self.detector()
        document={"metadata":{"model_sha256":"a"*64,"frame_count":1},
                  "detector_contract":detector.contract_manifest,"detections":[detector.detect(FRAME)]}
        validate_cached_detections(document)
        document["metadata"]["model_sha256"]="b"*64
        with self.assertRaises(DetectionContractError):validate_cached_detections(document)


@unittest.skipUnless(__import__("shutil").which("ffmpeg"),"ffmpeg required")
class CompletedRuntimeTests(unittest.TestCase):
    def make(self,cameras=("main","ref")):
        fixture=runtime_fixture.RuntimeTests();fixture.camera_ids=cameras;fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture
    def feed(self,f,*,reference_failed=False,missing_preroll=False):
        if missing_preroll:
            for q in f.capture.buffers.values():q.clear()
        if reference_failed:f.runtime.end_source("ref","source_failed")
        starts=[]
        for step in range(18):
            f.now=step*.5
            if "ref" in f.capture.cameras and not reference_failed:
                f.runtime.frame("ref",f.now,f.frame)
            starts+=f.runtime.frame("main",f.now,f.frame,assessments=[{"tracking_id":"t",
                "ppe":{"helmet":{"status":"suspected_no_helmet" if step<12 else "helmet"}}}])
            f.runtime.pump(now=f.now)
        for _ in range(20):f.runtime.pump(now=f.now)
        return [r for r in starts if r["kind"]=="start"]
    def test_single_primary_collects_and_delivers_complete_episode(self):
        f=self.make(("main",));starts=self.feed(f)
        self.assertEqual(len(starts),1)
        event=starts[0]["event_id"]
        self.assertEqual(f.box.event_status(event)["state"],"closed")
        reports=[json.loads(r[0]) for r in f.box.db.execute("SELECT body FROM requests WHERE kind='capture_report'")]
        self.assertEqual([r["state"] for r in reports],["complete"])
        self.assertTrue(all(r["state"]=="accepted" for r in f.dispatcher.status()))
    def test_reference_failure_keeps_primary_event_and_mandatory_incomplete_snapshot(self):
        f=self.make();starts=self.feed(f,reference_failed=True)
        self.assertEqual(len(starts),1)
        event=starts[0]["event_id"]
        self.assertEqual(json.loads(f.box.event_status(event)["snapshot"]),["main","ref"])
        self.assertEqual(f.box.event_status(event)["state"],"incomplete")
        reports=[json.loads(r[0]) for r in f.box.db.execute("SELECT body FROM requests WHERE kind='capture_report'")]
        self.assertEqual(len(reports),2)
        self.assertTrue(all(r["state"]=="incomplete" for r in reports))
        self.assertTrue(any(r["missing_ranges"] for r in reports))
        self.assertTrue(any(not r["missing_ranges"] for r in reports))
        self.assertTrue(f.transport.uploads)  # healthy primary fragments still delivered
    def test_no_preroll_does_not_erase_event_or_fabricate_complete_evidence(self):
        f=self.make(("main",));starts=self.feed(f,missing_preroll=True)
        self.assertEqual(len(starts),1);self.assertEqual(starts[0]["start_time"],0)
        self.assertEqual(starts[0]["end_time"],3)
        self.assertEqual(f.box.event_status(starts[0]["event_id"])["state"],"incomplete")
        close=json.loads(f.box.db.execute("SELECT body FROM requests WHERE kind='incomplete'").fetchone()[0])
        self.assertFalse(close["complete"])

class ProfileTests(unittest.TestCase):
    def profile(self,root):
        return dict(state_directory=str(root/"state"),
            clocks={"main":dict(offset=0,error_bound=0,verified=True,epoch="source-utc",valid_until="2099-01-01T00:00:00+00:00")},
            capture=dict(max_clock_error=0,pre_seconds=2,post_seconds=2,discovery_delay_limit=3.5,
                prebuffer_seconds=6,prebuffer_bytes=1000000,max_buffer_frames=120,max_events=2,
                global_spool_bytes=4000000,
                spool_config=dict(max_bytes=1000000,max_segments=20,max_age_seconds=60,max_attempts=3),
                writer_config=dict(segment_seconds=2,output_fps=2,max_frame_pixels=307200,max_input_gap_seconds=.5,encoder_timeout_seconds=5)),
            mux=dict(max_bytes=1000000,max_frames=100,max_frame_pixels=307200,max_clock_error=0),
            outbox=dict(max_db_bytes=2000000,max_records=1000,max_body_bytes=30000,source_ttl_seconds=60),
            delivery=dict(max_delivery_records=100,max_delivery_db_bytes=2000000),
            transport=dict(max_upload_bytes=1000000,timeout_seconds=2,max_response_bytes=30000,total_seconds=5),
            lifecycle=dict(missing_grace_seconds=1,threshold_seconds=3,max_sample_gap_seconds=.5,progress_emit_interval_seconds=1),
            source=dict(max_chunk_bytes=1000000,max_chunk_gap_seconds=.25,require_capture_context=True),
            decoder=dict(max_bytes=1000000,max_frames=180,max_frame_pixels=307200,min_fps=1,max_fps=60,
                duration_tolerance_ms=250,ttl_seconds=10,max_audit_records=100,max_metadata_bytes=1000000),
            metadata_steps_per_pump=10,max_lag_seconds=10)
    def test_single_primary_validates_without_creating_state_or_loading_model(self):
        from inference_worker.run_continuous import read_config
        with TemporaryDirectory() as temp:
            root=Path(temp);profile=self.profile(root);path=root/"profile.json"
            path.write_text(json.dumps(profile),encoding="utf-8")
            self.assertEqual(read_config(path,"main",now=1)["clocks"],profile["clocks"])
            self.assertFalse((root/"state").exists())
    def test_empty_or_missing_primary_and_incompatible_epoch_are_rejected(self):
        from inference_worker.run_continuous import read_config
        with TemporaryDirectory() as temp:
            root=Path(temp);path=root/"profile.json"
            for clocks in ({},{"ref":dict(offset=0,error_bound=0,verified=True,epoch="e",valid_until="2099-01-01T00:00:00+00:00")}):
                profile=self.profile(root);profile["clocks"]=clocks;path.write_text(json.dumps(profile))
                with self.assertRaises(ValueError):read_config(path,"main",now=1)
            profile=self.profile(root);profile["clocks"]["ref"]={**profile["clocks"]["main"],"epoch":"other"}
            path.write_text(json.dumps(profile))
            with self.assertRaisesRegex(ValueError,"shared declared epoch"):read_config(path,"main",now=1)
