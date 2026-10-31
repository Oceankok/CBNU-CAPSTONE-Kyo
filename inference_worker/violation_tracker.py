
"""Track consecutive suspected PPE violations over time."""

from math import isfinite

from inference_worker.event_validation import valid_ppe_confidence


class ViolationTracker:
    def __init__(self, threshold_seconds=3.0):
        self.threshold_seconds = threshold_seconds
        self.states = {}

    def update(self, assessments, timestamp):
        """Return newly qualified violation candidates."""
        candidates = []
        active_ids = set()

        for person in assessments:
            track_id = person["tracking_id"]
            active_ids.add(track_id)

            if person["status"] != "suspected_no_helmet":
                self.states.pop(track_id, None)
                continue

            state = self.states.get(track_id)
            score = person.get("verified_ppe_confidence")
            verified = valid_ppe_confidence(score)
            if not isfinite(timestamp):
                self.states.pop(track_id, None)
                continue

            if (state is None or state["verified"] != verified
                    or timestamp < state["last"]
                    or (verified and timestamp - state["last"] > 0.5)):
                alerted = bool(state and state["verified_reported"])
                state = {"start": timestamp, "last": timestamp, "reported": alerted,
                         "verified_reported": alerted, "verified": verified,
                         "minimum_confidence": score if verified else None, "samples": 0}
                self.states[track_id] = state
            if state["samples"] and timestamp == state["last"]:
                continue
            state["last"] = timestamp
            state["samples"] += 1
            if verified:
                state["minimum_confidence"] = min(state["minimum_confidence"], score)

            elapsed = timestamp - state["start"]

            if elapsed >= self.threshold_seconds and not state["reported"]:
                state["reported"] = True
                if verified:
                    state["verified_reported"] = True

                candidates.append({
                    "tracking_id": track_id,
                    "ppe_type": "helmet",
                    "start_time": state["start"],
                    "end_time": timestamp,
                    "duration_seconds": elapsed,
                    "person_confidence": person["person_confidence"],
                    "verified_ppe_confidence": state["minimum_confidence"],
                    "frame_sample_count": state["samples"],
                })

        # A missing person breaks the observed violation streak.
        for track_id in list(self.states):
            if track_id not in active_ids:
                del self.states[track_id]

        return candidates
