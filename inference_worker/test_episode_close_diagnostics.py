"""Offline close diagnostics: no camera, weights, API, or policy changes."""
import io
import json
import unittest
from contextlib import redirect_stdout

from inference_worker.test_event_lifecycle import observation
from inference_worker.violation_tracker import ViolationTracker
from inference_worker import run_server


class EpisodeCloseDiagnosticsTests(unittest.TestCase):
    def tracker(self):
        return ViolationTracker(camera_id="CAM", on_close=run_server._log_episode_close)

    def qualify(self, tracker):
        events = []
        for step in range(13):
            events.extend(tracker.update([observation(scope="private-scope")], step * .25))
        self.assertEqual(len(events), 1)
        return events[0]

    def test_visible_unknown_is_silent_and_departure_logs_once(self):
        output = io.StringIO()
        with redirect_stdout(output):
            tracker = self.tracker()
            event = self.qualify(tracker)
            for step in range(13, 93):
                self.assertEqual(tracker.update([observation(status="unknown", scope="private-scope")], step * .25), [])
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(tracker.update([], 24.5), [])
            tracker.update([], 25)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("[EPISODE CLOSE] "))
        record = json.loads(lines[0].removeprefix("[EPISODE CLOSE] "))
        self.assertEqual(record, dict(camera_id="CAM", tracking_id="track-1", ppe_type="helmet",
            event_id=event["event_id"], reason="observation_gap", start_time=0., end_time=3., observed_at=24.5))
        self.assertNotIn("private-scope", lines[0])

    def test_rewear_closes_then_removal_creates_distinct_event(self):
        output = io.StringIO()
        with redirect_stdout(output):
            tracker = self.tracker()
            first = self.qualify(tracker)
            for timestamp in (3.25, 3.5, 3.75):
                self.assertEqual(tracker.update([observation(status="helmet", scope="private-scope")], timestamp), [])
            events = []
            for step in range(16, 29):
                events.extend(tracker.update([observation(scope="private-scope")], step * .25))
        self.assertEqual(len(events), 1)
        self.assertNotEqual(first["event_id"], events[0]["event_id"])
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(output.getvalue().split("] ", 1)[1])["reason"], "ppe_present")

    def test_default_adapter_does_not_require_a_diagnostic_sink(self):
        tracker = ViolationTracker()
        self.qualify(tracker)
        self.assertEqual(tracker.update([], 4.25), [])

    def test_scope_change_reason_is_visible_without_scope_contents(self):
        output = io.StringIO()
        with redirect_stdout(output):
            tracker = self.tracker()
            self.qualify(tracker)
            self.assertEqual(tracker.update([observation(scope="another-private-scope")], 3.25), [])
        line = output.getvalue()
        self.assertEqual(len(line.splitlines()), 1)
        self.assertEqual(json.loads(line.split("] ", 1)[1])["reason"], "policy_scope_changed")
        self.assertNotIn("private-scope", line)


if __name__ == "__main__":
    unittest.main()
