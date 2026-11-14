"""Synthetic measured detections only: no weights, camera, or automatic decision."""
from copy import deepcopy
import unittest
from unittest.mock import Mock
from types import SimpleNamespace
import numpy as np

from inference_worker.event_lifecycle import EventLifecycle
from inference_worker.violation_tracker import ViolationTracker
from inference_worker.person_tracker import PersonTracker
from inference_worker.ppe_rules import assess_ppe
from inference_worker.test_event_lifecycle import observation
from inference_worker.test_positive_ppe import PERSON, HELMET, SHAPE


def person(x=30, score=.95, tag="A"):
    return {"person_bbox": [x,20,x+70,220], "person_confidence": score, "tag": tag,
            "ppe": {"helmet": {"status": "suspected_no_helmet", "verified_ppe_confidence": None}}}


class EpisodeStabilityTests(unittest.TestCase):
    def test_long_nonwear_with_flicker_and_missing_frames_is_one(self):
        tracker = ViolationTracker()
        events = []
        for step in range(241):
            rows = [observation()]
            if step in (40, 80, 120, 160):
                rows = []
            elif step in (41, 81, 121, 161):
                rows = [observation(status="helmet")]
            events.extend(tracker.update(rows, step*.25))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["observed_seconds"], 3)
        self.assertTrue(events[0]["review_only"])
        self.assertIsNone(events[0]["verified_ppe_confidence"])

    def test_rewear_then_removal_is_new_event_and_other_ppe_stays_open(self):
        tracker = ViolationTracker()
        events = []
        for step in range(41):
            row = observation()
            row["ppe"]["vest"] = {"status":"suspected_no_vest"}
            if 16 <= step <= 18:
                row["ppe"]["helmet"]["status"] = "helmet"
            events.extend(tracker.update([row], step*.25))
        self.assertEqual([e["ppe_type"] for e in events], ["helmet", "vest", "helmet"])
        self.assertEqual(len({e["event_id"] for e in events}), 3)
        self.assertEqual(events[-1]["start_time"], 4.75)

    def test_short_unknown_pauses_qualification_without_counting_gap(self):
        tracker = ViolationTracker()
        events = []
        for step in range(15):
            events.extend(tracker.update([] if step == 6 else [observation()], step*.25))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["end_time"], 3.5)
        self.assertEqual(events[0]["observed_seconds"], 3)
        self.assertEqual(events[0]["observation_gap_seconds"], .5)

    def test_sparse_absence_cannot_accumulate_unbounded_evidence(self):
        tracker = ViolationTracker()
        events = []
        for step in range(200):
            events.extend(tracker.update([] if step % 3 == 2 else [observation()], step*.25))
        self.assertEqual(events, [])

    def test_departure_and_other_person_never_merge(self):
        tracker = ViolationTracker()
        events = []
        for step in range(41):
            rows = [observation("A")] if step <= 12 else []
            if step >= 20:
                rows.append(observation("B"))
            events.extend(tracker.update(rows, step*.25))
        self.assertEqual([e["tracking_id"] for e in events], ["A","B"])
        self.assertNotEqual(events[0]["event_id"], events[1]["event_id"])

    def test_policy_change_requires_fresh_qualification(self):
        tracker = ViolationTracker()
        events = []
        for step in range(29):
            events.extend(tracker.update([observation(scope="A" if step <= 12 else "B")], step*.25))
        self.assertEqual([e["start_time"] for e in events], [0,3.25])

    def test_visible_unknown_for_twenty_seconds_keeps_id_without_absence_evidence(self):
        tracker=EventLifecycle("CAM",missing_grace_seconds=1,rewear_seconds=.5)
        original=None
        for step in range(13):
            starts=tracker.update([observation()],step*.25)
            if starts:original=starts[0]
        for step in range(13,93):
            self.assertEqual(tracker.update([observation(status="unknown")],step*.25),[])
        resumed=tracker.update([observation()],23.25)[0]
        self.assertEqual(resumed["event_id"],original["event_id"])
        self.assertEqual(resumed["kind"],"progress")
        self.assertAlmostEqual(resumed["observed_seconds"],3)
        self.assertAlmostEqual(resumed["observation_gap_seconds"],20.25)
        self.assertEqual(resumed["observation_gap_count"],1)
        closed=tracker.update([],24.5)[0]
        self.assertEqual(closed["close_reason"],"observation_gap")
        self.assertEqual(closed["end_time"],23.25)

    def test_continuous_close_waits_for_observed_rewear(self):
        tracker = EventLifecycle("CAM", missing_grace_seconds=1, rewear_seconds=.5)
        for step in range(13):
            tracker.update([observation()], step*.25)
        self.assertEqual(tracker.update([observation(status="helmet")],3.25),[])
        self.assertEqual(tracker.update([],3.5),[])
        self.assertEqual(tracker.update([observation(status="helmet")],3.75),[])
        self.assertEqual(tracker.update([observation(status="helmet")],4),[])
        closed=tracker.update([observation(status="helmet")],4.25)
        self.assertEqual(closed[0]["kind"],"close")
        self.assertEqual(closed[0]["end_time"],3)
        self.assertEqual(closed[0]["close_reason"],"ppe_present_after_unknown")


    def test_trailing_unknown_marks_normal_close_for_review_without_fake_gap_seconds(self):
        from inference_worker.continuous_payloads import update_payload
        for reason in ("ppe_present","track_departed","primary_source_ended"):
            with self.subTest(reason=reason):
                tracker=EventLifecycle("CAM",missing_grace_seconds=1,rewear_seconds=.5)
                for step in range(13):
                    tracker.update([observation()],step*.25)
                tracker.update([observation(status="unknown")],3.25)
                if reason=="ppe_present":
                    for timestamp in (3.5,3.75):
                        self.assertEqual(tracker.update([observation(status="helmet")],timestamp),[])
                    closed=tracker.update([observation(status="helmet")],4)[0]
                elif reason=="track_departed":
                    closed=tracker.update([],3.5,departed_tracking_ids=["track-1"])[0]
                else:
                    closed=tracker.close_all(reason)[0]
                self.assertEqual(closed["close_reason"],reason+"_after_unknown")
                self.assertEqual(closed["end_time"],3)
                self.assertEqual(closed["observation_gap_seconds"],0)
                self.assertTrue(closed["trailing_unknown"])
                body=update_payload(closed,session_id="a"*32,sequence=1,
                                    complete=True,final_segment_count=1)
                self.assertEqual(body["close_reason"],reason+"_after_unknown")
                self.assertEqual(body["gap_seconds"],0)

    def test_uninterrupted_rewear_closes_normally(self):
        tracker=EventLifecycle("CAM",missing_grace_seconds=1,rewear_seconds=.5)
        for step in range(13):
            tracker.update([observation()],step*.25)
        for timestamp in (3.25,3.5):
            self.assertEqual(tracker.update([observation(status="helmet")],timestamp),[])
        closed=tracker.update([observation(status="helmet")],3.75)[0]
        self.assertEqual(closed["close_reason"],"ppe_present")
        self.assertFalse(closed["trailing_unknown"])


class ByteTrackTests(unittest.TestCase):
    def test_weak_measurement_retains_id_but_not_ppe_evidence(self):
        tracker=PersonTracker()
        first=tracker.update([person()],0)[0]
        weak=tracker.update([person(32,.3)],.25)[0]
        self.assertEqual(weak["tracking_id"],first["tracking_id"])
        self.assertEqual(weak["ppe"]["helmet"]["status"],"unknown")
        self.assertTrue(weak["tracking_only"])
        self.assertEqual(weak["person_bbox"],[32,20,102,220])
        strong=tracker.update([person(34)],.5)[0]
        self.assertEqual(strong["tracking_id"],first["tracking_id"])
        self.assertEqual(strong["ppe"]["helmet"]["status"],"suspected_no_helmet")

    def test_weak_person_cannot_start_track(self):
        tracker=PersonTracker()
        for step in range(20):
            self.assertEqual(tracker.update([person(score=.3)],step*.25),[])

    def test_short_occlusion_has_no_predicted_ghost_and_recovers_id(self):
        tracker=PersonTracker()
        first=tracker.update([person()],0)[0]
        self.assertEqual(tracker.update([],.25),[])
        recovered=tracker.update([person(32)],.5)[0]
        self.assertEqual(recovered["tracking_id"],first["tracking_id"])

    def test_expired_track_is_not_revived_by_same_location(self):
        tracker=PersonTracker(max_missing_seconds=1)
        first=tracker.update([person()],0)[0]
        tracker.update([],1.25)
        tracker.update([person()],1.5)
        new=tracker.update([person()],1.75)[0]
        self.assertNotEqual(first["tracking_id"],new["tracking_id"])

    def test_ids_are_independent_for_each_camera(self):
        a,b=PersonTracker(),PersonTracker()
        self.assertEqual(a.update([person()],0)[0]["tracking_id"],"track-1")
        self.assertEqual(b.update([person()],0)[0]["tracking_id"],"track-1")
        for step in range(1,5):
            aa=a.update([person(),person(200,tag="B")],step*.25)
            bb=b.update([person(),person(200,tag="B")],step*.25)
        self.assertEqual({r["tracking_id"] for r in aa},{"track-1","track-2"})
        self.assertEqual({r["tracking_id"] for r in bb},{"track-1","track-2"})

    def test_separated_people_keep_distinct_tracks_and_overlap_is_unknown(self):
        tracker=PersonTracker()
        ids={}
        for step in range(41):
            rows=tracker.update([person(30+step*5,tag="A"),person(230-step*5,tag="B")],step*.1)
            self.assertEqual(len({r["tracking_id"] for r in rows}),len(rows))
            for row in rows:
                if step < 16:
                    if row["tag"] in ids:
                        self.assertEqual(row["tracking_id"],ids[row["tag"]])
                    ids[row["tag"]]=row["tracking_id"]
                if row["identity_ambiguous"]:
                    self.assertEqual(row["ppe"]["helmet"]["status"],"unknown")
        self.assertEqual(len(set(ids.values())),2)

    def test_duplicate_timestamp_does_not_consume_second_detection(self):
        tracker=PersonTracker()
        first=tracker.update([person()],0)
        self.assertEqual(tracker.update([],0),first)
        with self.assertRaises(ValueError):
            tracker.update([], -1)


class AssociationTests(unittest.TestCase):
    def test_helmet_above_person_box_is_associated(self):
        helmet={**HELMET,"bbox":[60,8,90,35]}
        row=assess_ppe([PERSON,helmet],SHAPE)[0]
        self.assertEqual(row["ppe"]["helmet"]["status"],"helmet")

    def test_adjacent_people_do_not_share_helmet(self):
        other={**PERSON,"bbox":[155,20,255,220]}
        rows=assess_ppe([PERSON,other,HELMET],SHAPE)
        self.assertEqual([r["ppe"]["helmet"]["status"] for r in rows],
                         ["helmet","suspected_no_helmet"])

    def test_ambiguous_box_does_not_become_absence(self):
        other={**PERSON,"bbox":[35,20,135,220]}
        rows=assess_ppe([PERSON,other,HELMET],SHAPE)
        self.assertEqual([r["ppe"]["helmet"]["status"] for r in rows],["unknown","unknown"])

    def test_weak_person_cannot_steal_ppe_or_occlude_strong_person(self):
        weak={**PERSON,"bbox":[32,20,132,220],"confidence":.2}
        with_helmet=assess_ppe([PERSON,weak,HELMET],SHAPE)
        self.assertEqual(with_helmet[0]["ppe"]["helmet"]["status"],"helmet")
        self.assertEqual(with_helmet[1]["ppe"]["helmet"]["status"],"unknown")
        without=assess_ppe([PERSON,weak],SHAPE)
        self.assertEqual(without[0]["ppe"]["helmet"]["status"],"suspected_no_helmet")
        tracker=PersonTracker()
        tracked=tracker.update([person(),person(32,.2)],0)
        self.assertEqual(tracked[0]["ppe"]["helmet"]["status"],"suspected_no_helmet")
        self.assertFalse(tracked[0]["identity_ambiguous"])

    def test_detector_preserves_weak_person_only(self):
        from inference_worker.ppe_detector import PPEDetector
        detector=PPEDetector.__new__(PPEDetector)
        detector.confidence=.5
        boxes=[SimpleNamespace(cls=np.array(i),conf=np.array(score),
               xyxy=np.array([[1,2,30,50]])) for i,score in ((0,.3),(1,.3),(1,.8))]
        detector.model=Mock(names={0:"person",1:"helmet"})
        detector.model.predict.return_value=[SimpleNamespace(boxes=boxes)]
        result=detector.detect(np.zeros((64,64,3),dtype=np.uint8))
        self.assertEqual([r["class_name"] for r in result],["person","helmet"])
        self.assertEqual(detector.model.predict.call_args.kwargs["conf"],.1)

