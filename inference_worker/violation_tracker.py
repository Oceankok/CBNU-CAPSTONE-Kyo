"""Candidate-only adapter over the shared event lifecycle.

Defaults are initial engineering settings, not calibrated operating claims.
Legacy HTTP events retain the immutable qualification window; extending their
server/media interval remains the separate continuous-event protocol.
"""
from math import isfinite

from inference_worker.event_lifecycle import EventLifecycle, _scope_value
from inference_worker.event_validation import valid_ppe_confidence


class ViolationTracker:
    def __init__(self, threshold_seconds=3.0, *, missing_grace_seconds=1.0,
                 rewear_seconds=0.5, qualification_grace_seconds=0.5,
                 max_sample_gap_seconds=0.5, qualification_window_seconds=6.0,
                 camera_id="local", on_close=None, preserve_required_episodes=False):
        self.lifecycle = EventLifecycle(
            camera_id, threshold_seconds=threshold_seconds,
            missing_grace_seconds=missing_grace_seconds,
            rewear_seconds=rewear_seconds,
            qualification_grace_seconds=qualification_grace_seconds,
            max_sample_gap_seconds=max_sample_gap_seconds,
            qualification_window_seconds=qualification_window_seconds)
        self._flat = set()
        self._on_close = on_close
        self._preserve_required_episodes = preserve_required_episodes

    @staticmethod
    def _resolved_scope(scope):
        return (isinstance(scope, tuple) and len(scope) == 4
                and isinstance(scope[0], str) and bool(scope[0])
                and scope[1] in ('person', 'camera')
                and isinstance(scope[2], tuple) and isinstance(scope[3], tuple)
                and all(isinstance(p, str) for p in scope[3]))

    def _episode_scope(self, scope, old, ppe, assessment):
        """Keep a qualified review incident anchored while its PPE stays required.

        Only legacy review opts in. Actual resolved OFF scopes still reach the
        lifecycle and close the incident; unresolved None remains unknown there.
        This anchor is not a claim about the person's current ROI membership.
        """
        scope = _scope_value(scope)
        if self._resolved_scope(scope) and ppe not in scope[3]:
            assessment['status'] = 'unknown'
        if (old and old['event_id'] and self._resolved_scope(scope)
                and self._resolved_scope(old['scope'])
                and scope[:2] == old['scope'][:2]
                and ppe in scope[3] and ppe in old['scope'][3]
                and old['verified'] == valid_ppe_confidence(assessment.get('verified_ppe_confidence'))):
            return old['scope']
        return scope

    @property
    def states(self):
        return {(key[0] if key in self._flat else key): state
                for key, state in self.lifecycle.states.items()}

    def update(self, assessments, timestamp):
        transitions = self.update_transitions(assessments, timestamp)
        if self._on_close is not None:
            for row in transitions:
                if row['kind'] == 'close':
                    self._on_close(row, timestamp)
        return [row for row in transitions if row['kind'] == 'start']

    def update_transitions(self, assessments, timestamp):
        if not isfinite(timestamp):
            self.lifecycle.close_all("invalid_timestamp")
            self._flat.clear()
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
                if self._preserve_required_episodes:
                    # Each PPE can have a different incident anchor: changing
                    # vest requirements must not close a still-required helmet.
                    rows.append({**row, 'ppe': {ppe: state}, 'policy_scope_key':
                        self._episode_scope(person.get('policy_scope_key'), old, ppe, state)})
            if not self._preserve_required_episodes or not ppes:
                rows.append(row)
        transitions = self.lifecycle.update(rows, timestamp)
        self._flat.intersection_update(self.lifecycle.states)
        return transitions
