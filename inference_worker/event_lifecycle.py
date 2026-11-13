"""Opt-in continuous-event state machine, independent of detection and HTTP.

Not wired into the live runner. The caller must choose a missing-observation
budget explicitly; this module establishes no operational grace default.
Call update/close_all from one decoder-owner thread; transport workers consume
immutable transition bodies rather than sharing this mutable state machine.
"""
from collections.abc import Mapping
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


class EventLifecycle:
    def __init__(self, camera_id, *, missing_grace_seconds,
                 threshold_seconds=3.0, max_sample_gap_seconds=0.5):
        for name, value, allow_zero in (
            ('missing_grace_seconds', missing_grace_seconds, True),
            ('threshold_seconds', threshold_seconds, False),
            ('max_sample_gap_seconds', max_sample_gap_seconds, False),
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
        self.states = {}
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
            output.append(self._snapshot(key, state, 'close', reason))

    def close_all(self, reason):
        """Explicit stream/policy reset. Never reconnect identities across resets."""
        if not reason:
            raise ValueError('close reason required')
        output = []
        for key in list(self.states):
            self._close(key, reason, output)
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
            if state is not None and state['scope'] != scope:
                self._close(key, 'policy_scope_changed', output)
                state = None
            if status == key[1]:
                if state is not None:
                    self._close(key, 'ppe_present', output)
                continue
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
                if gap and (state['event_id'] is None or delta > self.grace):
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
                }
                self.states[key] = state
            state['last_positive'] = timestamp
            state['gap_open'] = False
            state['samples'] += 1
            if verified:
                state['minimum_confidence'] = min(state['minimum_confidence'], score)
            if state['event_id'] is None:
                if timestamp - state['start'] >= self.threshold:
                    state['event_id'] = 'EVT_' + uuid4().hex
                    output.append(self._snapshot(key, state, 'start'))
            else:
                output.append(self._snapshot(key, state, 'progress'))

        for key in list(self.states):
            observation = observations.get(key)
            if observation and observation['status'] == 'suspected_no_' + key[1]:
                continue
            state = self.states[key]
            state['gap_open'] = True
            if state['event_id'] is None or timestamp - state['last_positive'] > self.grace:
                self._close(key, 'observation_gap', output)
        return output
