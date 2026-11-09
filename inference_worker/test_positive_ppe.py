"""Positive-only PPE matching and independent continuous absence checks."""
import unittest

from inference_worker.ppe_rules import assess_ppe
from inference_worker.person_tracker import PersonTracker
from inference_worker.violation_tracker import ViolationTracker
from inference_worker.policy import ppe_policy_status

PERSON = {'class_name': 'person', 'confidence': .95, 'bbox': [30, 20, 130, 220]}
HELMET = {'class_name': 'helmet', 'confidence': .9, 'bbox': [60, 22, 90, 50]}
VEST = {'class_name': 'vest', 'confidence': .9, 'bbox': [50, 70, 110, 145]}
SHAPE = (300, 400, 3)


class PositivePPEtests(unittest.TestCase):
    def rows(self, *extras):
        return assess_ppe([PERSON, *extras], SHAPE)

    def test_positive_classes_associate_with_one_person(self):
        row = self.rows(HELMET, VEST)[0]
        self.assertEqual(row['ppe']['helmet']['status'], 'helmet')
        self.assertEqual(row['ppe']['vest']['status'], 'vest')
        self.assertIsNone(row['ppe']['vest']['verified_ppe_confidence'])

    def test_independent_missing_types(self):
        for present, missing in ((HELMET, 'vest'), (VEST, 'helmet')):
            tracker, timer = PersonTracker(), ViolationTracker()
            events = []
            for i in range(13):
                events += timer.update(tracker.update(self.rows(present)), i*.25)
            self.assertEqual([x['ppe_type'] for x in events], [missing])
            self.assertIsNone(events[0]['verified_ppe_confidence'])

    def test_simultaneous_missing_same_identity_once(self):
        tracker, timer = PersonTracker(), ViolationTracker()
        events = []
        for i in range(25):
            events += timer.update(tracker.update(self.rows()), i*.25)
        self.assertEqual({x['ppe_type'] for x in events}, {'helmet', 'vest'})
        self.assertEqual(len(events), 2)
        self.assertEqual(len({x['tracking_id'] for x in events}), 1)

    def test_gap_and_missing_person_break_continuity(self):
        for missing in (False, True):
            tracker, timer = PersonTracker(), ViolationTracker()
            for i in range(12):
                self.assertFalse(timer.update(tracker.update(self.rows()), i*.25))
            if missing:
                timer.update([], 2.9)
            self.assertFalse(timer.update(tracker.update(self.rows()), 4))

    def test_unknown_cropped_small_ambiguous_and_unsupported(self):
        for box in ([30, 0, 130, 200], [30, 20, 40, 40], [30, 20, 130, 300]):
            row = assess_ppe([{**PERSON, 'bbox': box}], SHAPE)[0]
            self.assertTrue(all(x['status'] == 'unknown' for x in row['ppe'].values()))
        rows = self.rows({**PERSON, 'bbox': [35, 20, 135, 220]}, HELMET, VEST)
        self.assertTrue(all(s['status'] == 'unknown' for p in rows for s in p['ppe'].values()))
        row = assess_ppe([PERSON], SHAPE, ('helmet',))[0]
        self.assertEqual(row['ppe']['vest']['status'], 'unknown')

    def test_wearing_one_type_resets_only_its_timer(self):
        tracker, timer = PersonTracker(), ViolationTracker()
        events = []
        for i in range(13):
            rows = self.rows(HELMET) if i == 6 else self.rows()
            events += timer.update(tracker.update(rows), i*.25)
        self.assertEqual([x['ppe_type'] for x in events], ['vest'])

    def test_vest_only_policy(self):
        policy = {'configured': True, 'required_ppe': ['vest'], 'detectable_ppe': ['helmet', 'vest']}
        self.assertTrue(ppe_policy_status(policy, 'vest')[0])
        self.assertFalse(ppe_policy_status(policy, 'helmet')[0])


if __name__ == '__main__':
    unittest.main()
