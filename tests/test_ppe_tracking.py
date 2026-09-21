import unittest

from src.ppe_tracking import (
    Detection,
    IoUTrackFallback,
    PersonPPEAssociator,
    ViolationTracker,
)


def detection(label, box, confidence=0.9, track_id=None):
    return Detection(label, confidence, box, track_id)


class PersonPPEAssociatorTests(unittest.TestCase):
    def test_helmets_are_matched_to_the_correct_workers(self):
        observations = PersonPPEAssociator().associate(
            [
                detection("person", (0, 0, 100, 300), track_id=10),
                detection("person", (120, 0, 220, 300), track_id=11),
                detection("helmet", (30, 5, 70, 45)),
                detection("no_helmet", (150, 5, 190, 45)),
            ]
        )

        by_track = {item.track_id: item for item in observations}
        self.assertEqual(by_track[10].status, "helmet")
        self.assertEqual(by_track[11].status, "no_helmet")

    def test_one_helmet_cannot_satisfy_two_overlapping_people(self):
        observations = PersonPPEAssociator().associate(
            [
                detection("person", (0, 0, 100, 300), track_id=10),
                detection("person", (55, 0, 155, 300), track_id=11),
                detection("helmet", (65, 5, 95, 45)),
            ]
        )

        self.assertEqual(
            sorted(item.status for item in observations),
            ["helmet", "no_helmet_inferred"],
        )

    def test_ppe_on_lower_body_is_not_treated_as_a_helmet(self):
        observation = PersonPPEAssociator().associate(
            [
                detection("person", (0, 0, 100, 300), track_id=10),
                detection("helmet", (30, 220, 70, 270)),
            ]
        )[0]
        self.assertEqual(observation.status, "no_helmet_inferred")

    def test_missing_helmet_can_remain_unknown_for_four_class_model(self):
        observation = PersonPPEAssociator(infer_missing_helmet=False).associate(
            [detection("person", (0, 0, 100, 300), track_id=10)]
        )[0]
        self.assertEqual(observation.status, "unknown")


class ViolationTrackerTests(unittest.TestCase):
    def setUp(self):
        associator = PersonPPEAssociator()
        self.violating = associator.associate(
            [detection("person", (0, 0, 100, 300), track_id=10)]
        )[0]
        self.compliant = associator.associate(
            [
                detection("person", (0, 0, 100, 300), track_id=11),
                detection("helmet", (30, 5, 70, 45)),
            ]
        )[0]

    def test_each_worker_has_independent_temporal_state(self):
        tracker = ViolationTracker(
            duration_sec=2.0,
            cooldown_sec=10.0,
            min_samples=3,
            min_violation_ratio=0.6,
        )
        self.assertEqual(tracker.update([self.violating, self.compliant], 0.0), [])
        self.assertEqual(tracker.update([self.violating, self.compliant], 1.0), [])
        events = tracker.update([self.violating, self.compliant], 2.0)

        self.assertEqual([event.track_id for event in events], [10])
        self.assertEqual(events[0].sample_count, 3)

    def test_track_cooldown_prevents_duplicate_event(self):
        tracker = ViolationTracker(
            duration_sec=1.0,
            cooldown_sec=5.0,
            min_samples=2,
        )
        tracker.update([self.violating], 0.0)
        self.assertEqual(len(tracker.update([self.violating], 1.0)), 1)
        self.assertEqual(tracker.update([self.violating], 2.0), [])

    def test_compliant_current_frame_does_not_emit_stale_violation(self):
        tracker = ViolationTracker(
            duration_sec=1.0,
            cooldown_sec=5.0,
            min_samples=2,
            min_violation_ratio=0.5,
        )
        tracker.update([self.violating], 0.0)
        same_track_compliant = PersonPPEAssociator().associate(
            [
                detection("person", (0, 0, 100, 300), track_id=10),
                detection("helmet", (30, 5, 70, 45)),
            ]
        )[0]
        self.assertEqual(tracker.update([same_track_compliant], 1.0), [])


class IoUTrackFallbackTests(unittest.TestCase):
    def test_id_survives_small_motion(self):
        tracker = IoUTrackFallback()
        first = tracker.update([(0, 0, 100, 300)])
        second = tracker.update([(5, 0, 105, 300)])
        self.assertEqual(first[0], second[0])


if __name__ == "__main__":
    unittest.main()
