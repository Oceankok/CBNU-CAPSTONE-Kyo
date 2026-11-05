import unittest

from inference_worker.policy import helmet_policy_status
from inference_worker.violation_tracker import ViolationTracker
from inference_worker.work_area_policy import resolve_person_policy


class ROIIntegrationTests(unittest.TestCase):
    def test_area_change_resets_observation(self):
        tracker = ViolationTracker(threshold_seconds=3.0)

        def person(area):
            return {
                "tracking_id": "track-1",
                "status": "suspected_no_helmet",
                "person_confidence": 0.9,
                "verified_ppe_confidence": None,
                "policy_scope_key": ("policy-v1", (area,)),
            }

        for second in range(3):
            self.assertEqual(tracker.update([person("A")], float(second)), [])

        for second in range(3, 6):
            self.assertEqual(tracker.update([person("B")], float(second)), [])

        events = tracker.update([person("B")], 6.0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["duration_seconds"], 3.0)
        self.assertEqual(tracker.update([person("B")], 7.0), [])

    def test_helmet_not_required_outside_roi(self):
        policy = {
            "configured": True,
            "scope": "work_area",
            "policy_version": "test-v1",
            "configuration_issues": [],
            "zone_required_ppe": [],
            "event_supported_ppe": ["helmet"],
            "areas": [{
                "area_id": "ROI-A",
                "points": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]],
                "equipment_required_ppe": ["helmet"],
            }],
        }

        inside = resolve_person_policy(policy, [10, 10, 30, 80], 100, 100)
        outside = resolve_person_policy(policy, [70, 10, 90, 80], 100, 100)

        self.assertTrue(helmet_policy_status(inside)[0])
        self.assertFalse(helmet_policy_status(outside)[0])

        tracker = ViolationTracker(threshold_seconds=3.0)
        for second in range(5):
            self.assertEqual(tracker.update([], float(second)), [])
        self.assertEqual(tracker.states, {})

    def test_invalid_roi_policy_blocks_evaluation(self):
        policy = {
            "configured": True,
            "scope": "work_area",
            "policy_version": "test-v1",
            "configuration_issues": ["invalid_area"],
            "zone_required_ppe": ["helmet"],
            "event_supported_ppe": ["helmet"],
            "areas": [],
        }

        tracker = ViolationTracker(threshold_seconds=3.0)

        for second in range(5):
            with self.assertRaises(ValueError):
                resolve_person_policy(
                    policy, [10, 10, 30, 80], 100, 100
                )
            self.assertEqual(tracker.update([], float(second)), [])

        self.assertEqual(tracker.states, {})


if __name__ == "__main__":
    unittest.main()
