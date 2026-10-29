
"""Track consecutive suspected PPE violations over time."""


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

            if state is None:
                state = {"start": timestamp, "reported": False}
                self.states[track_id] = state

            elapsed = timestamp - state["start"]

            if elapsed >= self.threshold_seconds and not state["reported"]:
                state["reported"] = True

                candidates.append({
                    "tracking_id": track_id,
                    "ppe_type": "helmet",
                    "start_time": state["start"],
                    "end_time": timestamp,
                    "duration_seconds": elapsed,
                    "confidence": person["person_confidence"],
                })

        # A missing person breaks the observed violation streak.
        for track_id in list(self.states):
            if track_id not in active_ids:
                del self.states[track_id]

        return candidates
