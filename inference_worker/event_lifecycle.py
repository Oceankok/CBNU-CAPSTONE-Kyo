"""Shared episode state machine, independent of detection and HTTP.

Shared by candidate-only and continuous runners. The caller chooses observation
budgets; only observed absence qualifies.
Call update/close_all from one decoder-owner thread; transport workers consume
immutable transition bodies rather than sharing this mutable state machine.
"""
from collections.abc import Mapping
import hashlib
import json
from math import isfinite
from uuid import uuid4

from inference_worker.event_validation import valid_ppe_confidence


def _scope_value(value):
    """Copy a supported policy identity into immutable, safely comparable values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    if isinstance(value, (tuple, list)):
        return tuple(_scope_value(item) for item in value)
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return tuple(sorted((key, _scope_value(item)) for key, item in value.items()))
    raise ValueError('policy_scope_key')


def _scope_change(previous, current, timestamp):
    """Fixed-size local diagnostic; no raw scope values enter the new fields."""
    def digest(value):
        return hashlib.sha256(json.dumps(value, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    result = dict(observed_at=timestamp, previous_scope_sha256=digest(previous),
                  next_scope_sha256=digest(current), previous_scope_resolved=previous is not None,
                  next_scope_resolved=current is not None)
    if all(isinstance(value, tuple) and len(value) == 4 and value[1] in ('person', 'camera')
           for value in (previous, current)):
        for name, index in (('policy_version_changed', 0), ('scope_basis_changed', 1),
                            ('area_membership_changed', 2), ('required_ppe_changed', 3)):
            result[name] = previous[index] != current[index]
    return result


class EventLifecycle:
    def __init__(self, camera_id, *, missing_grace_seconds,
                 threshold_seconds=3.0, max_sample_gap_seconds=0.5,
                 rewear_seconds=0.0, qualification_grace_seconds=0.0,
                 qualification_window_seconds=6.0):
        for name, value, allow_zero in (
            ('missing_grace_seconds', missing_grace_seconds, True),
            ('threshold_seconds', threshold_seconds, False),
            ('max_sample_gap_seconds', max_sample_gap_seconds, False),
            ('rewear_seconds', rewear_seconds, True),
            ('qualification_grace_seconds', qualification_grace_seconds, True),
            ('qualification_window_seconds', qualification_window_seconds, False),
        ):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not isfinite(value) or value < 0 or (not allow_zero and value == 0)):
                raise ValueError(name)
        if not isinstance(camera_id, str) or not camera_id.strip():
            raise ValueError('camera_id')
        self.camera_id = camera_id
        self.grace = missing_grace_seconds
        self.threshold = threshold_seconds
        self.sample_gap = max_sample_gap_seconds
        if qualification_window_seconds < threshold_seconds:
            raise ValueError('qualification_window_seconds')
        self.rewear = rewear_seconds
        self.qualification_grace = qualification_grace_seconds
        self.qualification_window = qualification_window_seconds
        self.states = {}
        self.scoped_tracks = {}  # Last presence only; independent of qualification state.
        self.last_timestamp = None

    def _snapshot(self, key, state, kind, reason=None):
        result = {
            'kind': kind, 'event_id': state['event_id'],
            'camera_id': self.camera_id, 'tracking_id': key[0], 'ppe_type': key[1],
            'policy_scope_key': state['scope'],
            'start_time': state['start'], 'end_time': state['last_positive'],
            'duration_seconds': state['last_positive'] - state['start'],
            'frame_sample_count': state['samples'],
            'verified_ppe_confidence': state['minimum_confidence'],
            'review_only': not state['verified'],
            'person_confidence': state.get('person_confidence'),
            'trailing_unknown': state['unresolved_gap'],
            # Span is not claimed to be continuously observed across a gap.
            'observed_seconds': state['observed_seconds'],
            'observation_gap_count': state['gap_count'],
            'observation_gap_seconds': state['gap_seconds'],
        }
        if reason is not None:
            result['close_reason'] = reason
        return result

    def _close(self, key, reason, output):
        state = self.states.pop(key)
        if state['event_id'] is not None:
            if state['unresolved_gap'] and reason in ('ppe_present', 'track_departed', 'primary_source_ended'):
                reason += '_after_unknown'
            output.append(self._snapshot(key, state, 'close', reason))

    def close_all(self, reason):
        """Explicit stream/policy reset. Never reconnect identities across resets."""
        if not reason:
            raise ValueError('close reason required')
        output = []
        for key in list(self.states):
            self._close(key, reason, output)
        self.scoped_tracks.clear()
        self.last_timestamp = None
        return output

    def update(self, assessments, timestamp, *, departed_tracking_ids=()):
        if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                or not isfinite(timestamp)):
            raise ValueError('timestamp')
        if self.last_timestamp is not None and timestamp < self.last_timestamp:
            raise ValueError('timestamp order')
        if timestamp == self.last_timestamp:
            return []  # Decoder retries must not create an extra sample or event.

        # Validate the entire frame before mutating even one track. Otherwise a
        # malformed later row could consume a start transition without returning it.
        if isinstance(departed_tracking_ids, (str, bytes)):
            raise ValueError('departed_tracking_ids')
        departed_rows = tuple(departed_tracking_ids)
        if any(not isinstance(track, str) or not track for track in departed_rows):
            raise ValueError('departed_tracking_ids')
        departed = set(departed_rows)
        observations = {}
        present_tracks, known_scoped_tracks = set(), set()
        for person in assessments:
            if not isinstance(person, Mapping):
                raise ValueError('person assessment')
            tracking_id = person.get('tracking_id')
            if not isinstance(tracking_id, str) or not tracking_id:
                raise ValueError('tracking_id')
            ppes = person.get('ppe')
            if ppes is not None and not isinstance(ppes, Mapping):
                raise ValueError('ppe assessments')
            scope = _scope_value(person.get('policy_scope_key'))
            present_tracks.add(tracking_id)
            if scope is not None:
                known_scoped_tracks.add(tracking_id)
            rows = ppes.items() if ppes is not None else [(person.get('ppe_type', 'helmet'), person)]
            for ppe, assessment in rows:
                if not isinstance(ppe, str) or not ppe or not isinstance(assessment, Mapping):
                    raise ValueError('ppe assessment')
                status = assessment.get('status')
                if status not in (ppe, 'suspected_no_' + ppe, 'unknown'):
                    raise ValueError('ppe status')
                key = (tracking_id, ppe)
                if key in observations:
                    raise ValueError('duplicate track/PPE observation')
                observations[key] = {**person, **assessment, 'policy_scope_key': scope}

        # Remember scoped mode for a currently visible track even when its
        # unqualified PPE state expires. Never-scoped legacy tracks stay valid.
        self.scoped_tracks = {track: seen for track, seen in self.scoped_tracks.items()
                              if track not in departed and
                              (track in present_tracks or timestamp - seen <= self.grace)}
        for track in (known_scoped_tracks | (present_tracks & self.scoped_tracks.keys())) - departed:
            self.scoped_tracks[track] = timestamp
        for key, observation in observations.items():
            if key[0] in self.scoped_tracks and observation['policy_scope_key'] is None:
                observation['status'] = 'unknown'

        self.last_timestamp = timestamp
        output = []
        for key in list(self.states):
            if key[0] in departed:
                self._close(key, 'track_departed', output)

        for key, observation in observations.items():
            if key[0] in departed:
                continue
            status = observation['status']
            scope = observation.get('policy_scope_key')
            state = self.states.get(key)
            if state is not None and state['scope'] is not None and scope is None:
                # Unresolved position/policy is an unknown observation, not a
                # known membership change or evidence of absence/rewear.
                status = observation['status'] = 'unknown'
            elif state is not None and state['scope'] != scope:
                self._close(key, 'policy_scope_changed', output)
                if state['event_id'] is not None:
                    output[-1]['policy_scope_change'] = _scope_change(state['scope'], scope, timestamp)
                state = None
            if state is not None and state['event_id'] is not None:
                person_delta = timestamp - state['last_seen']
                if person_delta > self.sample_gap:
                    state['unresolved_gap'] = True
                if ((state['person_gap_open'] or person_delta > self.sample_gap)
                        and person_delta > self.grace):
                    self._close(key, 'observation_gap', output)
                    state = None
                else:
                    state['last_seen'] = timestamp
                    state['person_gap_open'] = False
            if status == key[1]:
                if state is not None:
                    if state['event_id'] is None:
                        self._close(key, 'ppe_present', output)
                    else:
                        if (state['present_last'] is None
                                or timestamp - state['present_last'] > self.sample_gap):
                            state['present_since'] = timestamp
                        state['present_last'] = timestamp
                        state['gap_open'] = True
                        if timestamp - state['present_since'] + 1e-6 >= self.rewear:
                            self._close(key, 'ppe_present', output)
                continue
            if state is not None:
                state['present_since'] = state['present_last'] = None
            if status != 'suspected_no_' + key[1]:
                continue  # Unknown is handled as a missing observation below.

            score = observation.get('verified_ppe_confidence')
            verified = valid_ppe_confidence(score)
            if state is not None and state['verified'] != verified:
                self._close(key, 'evidence_mode_changed', output)
                state = None
            if state is not None:
                delta = timestamp - state['last_positive']
                gap = state['gap_open'] or delta > self.sample_gap
                budget = self.grace if state['event_id'] is not None else self.qualification_grace
                expired = (state['event_id'] is None
                           and timestamp - state['start'] > self.qualification_window)
                if expired or (state['event_id'] is None and gap and delta > budget):
                    self._close(key, 'observation_gap', output)
                    state = None
                elif gap:
                    state['gap_count'] += 1
                    state['gap_seconds'] += delta
                else:
                    state['observed_seconds'] += delta

            if state is None:
                state = {
                    'start': timestamp, 'last_positive': timestamp,
                    'scope': scope, 'verified': verified,
                    'minimum_confidence': score if verified else None,
                    'event_id': None, 'samples': 0, 'gap_open': False,
                    'observed_seconds': 0.0, 'gap_count': 0, 'gap_seconds': 0.0,
                    'present_since': None, 'present_last': None,
                    'last_seen': timestamp, 'person_gap_open': False, 'unresolved_gap': False,
                    'person_confidence': observation.get('person_confidence'),
                }
                self.states[key] = state
            state['unresolved_gap'] = False
            state['last_seen'] = timestamp
            state['last_positive'] = timestamp
            state['gap_open'] = False
            state['samples'] += 1
            state['person_confidence'] = observation.get('person_confidence')
            if verified:
                state['minimum_confidence'] = min(state['minimum_confidence'], score)
            if state['event_id'] is None:
                if state['observed_seconds'] + 1e-6 >= self.threshold:
                    state['event_id'] = 'EVT_' + uuid4().hex
                    output.append(self._snapshot(key, state, 'start'))
            else:
                output.append(self._snapshot(key, state, 'progress'))

        for key in list(self.states):
            observation = observations.get(key)
            if observation and observation['status'] == 'suspected_no_' + key[1]:
                continue
            state = self.states[key]
            if observation and observation['status'] == key[1]:
                continue
            state['gap_open'] = True
            state['unresolved_gap'] = True
            state['present_since'] = state['present_last'] = None
            if state['event_id'] is not None:
                # A visible tracked person with unknown PPE is still the same
                # episode. Only missing person observations expire identity.
                if observation:
                    continue
                state['person_gap_open'] = True
                if timestamp - state['last_seen'] > self.grace:
                    self._close(key, 'observation_gap', output)
                continue
            budget = self.qualification_grace
            if (timestamp - state['last_positive'] > budget
                    or (state['event_id'] is None
                        and timestamp - state['start'] > self.qualification_window)):
                self._close(key, 'observation_gap', output)
        return output
