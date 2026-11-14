"""Candidate-only adapter over the shared event lifecycle.

Defaults are initial engineering settings, not calibrated operating claims.
Legacy HTTP events retain the immutable qualification window; extending their
server/media interval remains the separate continuous-event protocol.
"""
from math import isfinite

from inference_worker.event_lifecycle import EventLifecycle
from inference_worker.event_validation import valid_ppe_confidence


class ViolationTracker:
    def __init__(self, threshold_seconds=3.0, *, missing_grace_seconds=1.0,
                 rewear_seconds=0.5, qualification_grace_seconds=0.5,
                 max_sample_gap_seconds=0.5, qualification_window_seconds=6.0,
                 camera_id="local"):
        self.lifecycle = EventLifecycle(
            camera_id, threshold_seconds=threshold_seconds,
            missing_grace_seconds=missing_grace_seconds,
            rewear_seconds=rewear_seconds,
            qualification_grace_seconds=qualification_grace_seconds,
            max_sample_gap_seconds=max_sample_gap_seconds,
            qualification_window_seconds=qualification_window_seconds)
        self._flat = set()

    @property
    def states(self):
        return {(key[0] if key in self._flat else key): state
                for key, state in self.lifecycle.states.items()}

    def update(self, assessments, timestamp):
        if not isfinite(timestamp):
            self.lifecycle.close_all("invalid_timestamp")
            return []
        rows = []
        for person in assessments:
            row = {**person, "tracking_id": str(person["tracking_id"])}
            ppes = person.get("ppe")
            if ppes is None:
                ppe = person.get("ppe_type", "helmet")
                self._flat.add((row["tracking_id"], ppe))
                ppes = {ppe: person}
            row["ppe"] = {}
            for ppe, assessment in ppes.items():
                state = dict(assessment)
                old = self.lifecycle.states.get((row["tracking_id"], ppe))
                if (old and old["event_id"] and old["verified"]
                        and state["status"] == "suspected_no_" + ppe
                        and not valid_ppe_confidence(state.get("verified_ppe_confidence"))):
                    state["status"] = "unknown"
                row["ppe"][ppe] = state
            rows.append(row)
        return [row for row in self.lifecycle.update(rows, timestamp)
                if row["kind"] == "start"]
