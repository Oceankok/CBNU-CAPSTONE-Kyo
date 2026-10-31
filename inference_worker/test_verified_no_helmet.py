"""Direct negative-model evidence through real assessment/tracker/event stages."""
import math
import unittest
from contextlib import redirect_stderr, redirect_stdout
import io
from unittest.mock import patch

from inference_worker.event_validation import validate_candidate
from inference_worker.ppe_rules import assess_helmets
from inference_worker.violation_tracker import ViolationTracker
from inference_worker import test_runner_resilience as fixtures
from inference_worker.test_server_mock import FakeVideoClient
from inference_worker import run_server

POLICY = {'configured': True, 'configuration_issues': [], 'required_ppe': ['helmet']}
PERSON = {'class_name': 'person', 'confidence': 0.99, 'bbox': [0, 0, 100, 200]}
NEGATIVE = {'class_name': 'no_helmet', 'confidence': 0.73, 'bbox': [20, 5, 60, 40]}


def assessed(*detections):
    return [{**row, 'tracking_id': 'track-1'} for row in assess_helmets(list(detections))]


def observe(tracker, rows, start=0, count=13):
    return [candidate for i in range(count) for candidate in tracker.update(rows, start+i*0.25)]


class VerifiedNoHelmetTests(unittest.TestCase):
    def test_absent_helmet_box_never_becomes_verified_score(self):
        rows = assessed(PERSON)
        self.assertIsNone(rows[0]['verified_ppe_confidence'])
        candidates = observe(ViolationTracker(), rows)
        self.assertEqual(validate_candidate(candidates[0], POLICY), (False, 'ppe_confidence_not_verified'))

    def test_direct_negative_score_is_not_person_confidence(self):
        rows = assessed(PERSON, NEGATIVE)
        self.assertEqual(rows[0]['verified_ppe_confidence'], 0.73)
        candidate = observe(ViolationTracker(), rows)[0]
        self.assertEqual(candidate['verified_ppe_confidence'], 0.73)
        self.assertEqual(candidate['frame_sample_count'], 13)
        self.assertEqual(validate_candidate(candidate, POLICY), (True, 'valid'))

    def test_minimum_negative_score_across_observation(self):
        tracker = ViolationTracker()
        for index in range(12):
            score = 0.55 if index == 5 else 0.9
            tracker.update(assessed(PERSON, {**NEGATIVE, 'confidence': score}), index*0.25)
        candidate = tracker.update(assessed(PERSON, NEGATIVE), 3)[0]
        self.assertEqual(candidate['verified_ppe_confidence'], 0.55)

    def test_unrelated_body_prediction_is_not_head_evidence(self):
        rows = assessed(PERSON, {**NEGATIVE, 'bbox': [20, 100, 60, 150]})
        self.assertIsNone(rows[0]['verified_ppe_confidence'])

    def test_overlapping_people_do_not_share_one_negative(self):
        rows = assess_helmets([PERSON, {**PERSON, 'bbox': [10, 0, 110, 200]}, NEGATIVE])
        self.assertTrue(all(row['verified_ppe_confidence'] is None for row in rows))

    def test_positive_negative_conflict_is_unknown(self):
        rows = assessed(PERSON, NEGATIVE, {**NEGATIVE, 'class_name': 'helmet'})
        self.assertEqual(rows[0]['status'], 'unknown')
        self.assertEqual(observe(ViolationTracker(), rows), [])

    def test_invalid_or_low_negative_scores_are_not_evidence(self):
        for score in (True, -1, 0.49, 1.1, math.nan, math.inf, '0.9'):
            with self.subTest(score=score):
                rows = assessed(PERSON, {**NEGATIVE, 'confidence': score})
                self.assertIsNone(rows[0]['verified_ppe_confidence'])
                candidate = {'ppe_type': 'helmet', 'duration_seconds': 3, 'verified_ppe_confidence': score}
                self.assertEqual(validate_candidate(candidate, POLICY), (False, 'invalid_ppe_confidence'))

    def test_old_unverified_time_does_not_count_as_negative_observation(self):
        tracker = ViolationTracker()
        observe(tracker, assessed(PERSON))
        self.assertEqual(observe(tracker, assessed(PERSON, NEGATIVE), start=3.25, count=12), [])
        candidate = tracker.update(assessed(PERSON, NEGATIVE), 6.25)[0]
        self.assertEqual(candidate['start_time'], 3.25)

    def test_one_missing_negative_resets_verified_streak(self):
        tracker = ViolationTracker()
        observe(tracker, assessed(PERSON, NEGATIVE), count=8)
        tracker.update(assessed(PERSON), 2)
        self.assertEqual(observe(tracker, assessed(PERSON, NEGATIVE), start=2.25, count=12), [])
        self.assertEqual(len(tracker.update(assessed(PERSON, NEGATIVE), 5.25)), 1)

    def test_large_frame_gap_cannot_join_negative_observations(self):
        tracker = ViolationTracker()
        observe(tracker, assessed(PERSON, NEGATIVE), count=8)
        self.assertEqual(tracker.update(assessed(PERSON, NEGATIVE), 3.5), [])
        self.assertEqual(tracker.states['track-1']['start'], 3.5)

    def test_brief_negative_loss_after_report_does_not_duplicate_event(self):
        tracker = ViolationTracker()
        self.assertEqual(len(observe(tracker, assessed(PERSON, NEGATIVE))), 1)
        tracker.update(assessed(PERSON), 3.25)
        self.assertEqual(observe(tracker, assessed(PERSON, NEGATIVE), start=3.5), [])

    def test_wearing_helmet_then_new_episode_requires_new_observation(self):
        tracker = ViolationTracker()
        observe(tracker, assessed(PERSON, NEGATIVE))
        tracker.update(assessed(PERSON, {**NEGATIVE, 'class_name': 'helmet'}), 3.25)
        self.assertEqual(len(observe(tracker, assessed(PERSON, NEGATIVE), start=3.5)), 1)

    def test_duplicate_frame_does_not_inflate_sample_count(self):
        tracker = ViolationTracker()
        tracker.update(assessed(PERSON, NEGATIVE), 0)
        tracker.update(assessed(PERSON, NEGATIVE), 0)
        candidate = observe(tracker, assessed(PERSON, NEGATIVE), start=0.25, count=12)[0]
        self.assertEqual(candidate['frame_sample_count'], 13)

    def test_nonfinite_time_cannot_produce_event(self):
        tracker = ViolationTracker()
        self.assertEqual(tracker.update(assessed(PERSON, NEGATIVE), math.nan), [])
        self.assertEqual(tracker.states, {})

    def test_policy_still_blocks_unconfigured_or_nonrequired_helmet(self):
        candidate = observe(ViolationTracker(), assessed(PERSON, NEGATIVE))[0]
        self.assertFalse(validate_candidate(candidate, {**POLICY, 'configured': False})[0])
        self.assertFalse(validate_candidate(candidate, {**POLICY, 'required_ppe': ['vest']})[0])

    def test_runner_uses_real_tracker_and_keeps_startup_preroll(self):
        class NegativeDetector:
            model_version = 'fixture-negative-model'
            supports_no_helmet = True
            def detect(self, frame):
                return [PERSON, NEGATIVE]
        output, delivery = fixtures.RunnerResilienceTests().run_runner(
            FakeVideoClient(9), submit=True, detector=NegativeDetector())
        delivery.assert_called_once()
        event = delivery.call_args.args[1]
        self.assertEqual(event['confidence'], 0.73)
        self.assertEqual(event['model_version'], 'fixture-negative-model')
        self.assertEqual(event['frame_sample_count'], 13)
        self.assertNotIn('[CLIP INCOMPLETE]', output)

    def test_three_class_model_is_rejected_before_api_client_on_submit(self):
        class ThreeClassDetector:
            model_version = 'fixture-three-class'
            supports_no_helmet = False
        with (patch.object(run_server, 'create_detector', return_value=ThreeClassDetector()),
              patch.object(run_server, 'VideoClient') as client,
              patch('sys.argv', ['run_server', '--camera-id', 'CAM_001', '--submit-events']),
              redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error):
            with self.assertRaises(SystemExit) as raised:
                run_server.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('no_helmet', error.getvalue())
        client.assert_not_called()
