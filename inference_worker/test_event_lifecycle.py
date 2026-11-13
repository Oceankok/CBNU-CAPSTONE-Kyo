from unittest import TestCase
from inference_worker.event_lifecycle import EventLifecycle


def observation(track='track-1', status='suspected_no_helmet', scope='roi-A'):
    return {'tracking_id': track, 'person_confidence': .9, 'policy_scope_key': scope,
            'ppe': {'helmet': {'status': status, 'verified_ppe_confidence': None}}}


class LifecycleTests(TestCase):
    def continuous(self, tracker, end, *, start=0, track='track-1'):
        result = []
        for step in range(start, end + 1):
            result.extend(tracker.update([observation(track)], step / 10))
        return result

    def test_requires_explicit_grace_and_rejects_bad_parameters(self):
        with self.assertRaises(TypeError):
            EventLifecycle('CAM')
        for value in (-1, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError):
                EventLifecycle('CAM', missing_grace_seconds=value)

    def test_long_streak_has_one_start_and_one_id(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=.5)
        rows = self.continuous(tracker, 1250)
        self.assertEqual([r['start_time'] for r in rows if r['kind'] == 'start'], [0])
        self.assertEqual(len({r['event_id'] for r in rows}), 1)
        self.assertEqual(rows[-1]['end_time'], 125)
        self.assertEqual(rows[-1]['frame_sample_count'], 1251)
        self.assertEqual(tracker.close_all('shutdown')[0]['end_time'], 125)
        self.assertEqual(tracker.close_all('shutdown'), [])

    def test_short_gap_after_qualification_preserves_id_but_records_gap(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=.5)
        first = self.continuous(tracker, 30)[0]
        self.assertEqual(tracker.update([], 3.1), [])
        continued = tracker.update([observation()], 3.2)[0]
        self.assertEqual(continued['event_id'], first['event_id'])
        self.assertEqual(continued['kind'], 'progress')
        self.assertEqual(continued['observation_gap_count'], 1)
        self.assertAlmostEqual(continued['observation_gap_seconds'], .2)
        self.assertAlmostEqual(continued['observed_seconds'], 3)

    def test_prequalification_gap_does_not_count_as_three_seconds(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=5)
        self.assertEqual(self.continuous(tracker, 29), [])
        self.assertEqual(tracker.update([], 3), [])
        self.assertEqual(tracker.update([observation()], 3.1), [])
        rows = self.continuous(tracker, 62, start=32)
        starts = [r for r in rows if r['kind'] == 'start']
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]['start_time'], 3.1)

    def test_grace_boundary_and_reentry(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=.5)
        original = self.continuous(tracker, 30)[0]
        self.assertEqual(tracker.update([], 3.5), [])
        closed = tracker.update([], 3.51)[0]
        self.assertEqual(closed['kind'], 'close')
        self.assertEqual(closed['end_time'], 3)
        self.assertEqual(closed['event_id'], original['event_id'])
        rows = self.continuous(tracker, 70, start=40)
        self.assertNotEqual(rows[0]['event_id'], original['event_id'])

    def test_different_track_not_merged_even_with_same_scope(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=5)
        original = self.continuous(tracker, 30)[0]
        rows = self.continuous(tracker, 70, start=40, track='track-2')
        starts = [r for r in rows if r['kind'] == 'start']
        self.assertEqual(len(starts), 1)
        self.assertNotEqual(starts[0]['event_id'], original['event_id'])
        self.assertEqual(starts[0]['tracking_id'], 'track-2')

    def test_explicit_departure_and_positive_ppe_end_immediately(self):
        for reason in ('departure', 'compliant'):
            tracker = EventLifecycle('CAM', missing_grace_seconds=5)
            self.continuous(tracker, 30)
            if reason == 'departure':
                result = tracker.update([], 3.1, departed_tracking_ids=['track-1'])
            else:
                result = tracker.update([observation(status='helmet')], 3.1)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['kind'], 'close')
            self.assertEqual(result[0]['end_time'], 3)

    def test_scope_change_and_unknown_do_not_transfer_qualification(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=5)
        self.continuous(tracker, 30)
        result = tracker.update([observation(scope='roi-B')], 3.1)
        self.assertEqual(result[0]['close_reason'], 'policy_scope_changed')
        self.assertEqual(len(result), 1)
        self.assertEqual(tracker.update([observation(status='unknown', scope='roi-B')], 3.2), [])
        self.assertFalse(tracker.states)

    def test_grace_values_are_explicit_test_parameters_not_operational_defaults(self):
        for grace in (.5, 1, 5):
            tracker = EventLifecycle('CAM', missing_grace_seconds=grace)
            self.continuous(tracker, 30)
            self.assertEqual(tracker.update([], 3 + grace), [])
            self.assertEqual(tracker.update([], 3 + grace + .01)[0]['kind'], 'close')

    def test_duplicate_time_does_not_add_sample_and_reverse_time_rejected(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=.5)
        self.continuous(tracker, 30)
        self.assertEqual(tracker.update([observation()], 3), [])
        with self.assertRaises(ValueError):
            tracker.update([observation()], 2)

    def test_ppe_tracks_are_independent_and_do_not_share_ids(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=.5)
        person = observation()
        person['ppe']['vest'] = {'status': 'suspected_no_vest', 'verified_ppe_confidence': None}
        starts = []
        for index in range(31):
            starts += tracker.update([person], index / 10)
        self.assertEqual({r['ppe_type'] for r in starts}, {'helmet', 'vest'})
        self.assertEqual(len({r['event_id'] for r in starts}), 2)
        person['ppe']['helmet']['status'] = 'helmet'
        result = tracker.update([person], 3.1)
        self.assertEqual([(r['ppe_type'], r['kind']) for r in result],
                         [('helmet', 'close'), ('vest', 'progress')])

    def test_unknown_gap_recovery_and_timeout(self):
        for recover in (True, False):
            tracker = EventLifecycle('CAM', missing_grace_seconds=.5)
            original = self.continuous(tracker, 30)[0]
            self.assertEqual(tracker.update([observation(status='unknown')], 3.1), [])
            result = tracker.update([observation(status=('suspected_no_helmet' if recover else 'unknown'))],
                                    3.2 if recover else 3.6)
            self.assertEqual(result[0]['event_id'], original['event_id'])
            self.assertEqual(result[0]['kind'], 'progress' if recover else 'close')

    def test_sample_gap_requires_requalification_before_start(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=5)
        self.continuous(tracker, 29)
        self.assertEqual(tracker.update([observation()], 4), [])
        result = self.continuous(tracker, 70, start=41)
        self.assertEqual(result[0]['start_time'], 4)

    def test_evidence_mode_change_closes_old_event_without_inventing_confidence(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=.5)
        self.continuous(tracker, 30)
        person = observation()
        person['ppe']['helmet']['verified_ppe_confidence'] = .8
        rows = tracker.update([person], 3.1)
        self.assertEqual(rows[0]['close_reason'], 'evidence_mode_changed')
        self.assertTrue(rows[0]['review_only'])
        self.assertIsNone(rows[0]['verified_ppe_confidence'])
        self.assertIsNone(tracker.states[('track-1', 'helmet')]['event_id'])

    def test_duplicate_observation_is_rejected_without_state_mutation(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=.5)
        with self.assertRaises(ValueError):
            tracker.update([observation(), observation()], 0)
        self.assertIsNone(tracker.last_timestamp)
        self.assertEqual(tracker.states, {})

    def test_explicit_reset_can_start_new_stream_clock(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=.5)
        original = self.continuous(tracker, 30)[0]
        self.assertEqual(tracker.close_all('stream_changed')[0]['event_id'], original['event_id'])
        replacement = self.continuous(tracker, 30)[0]
        self.assertNotEqual(original['event_id'], replacement['event_id'])

    def test_malformed_later_row_cannot_consume_start_transition(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=.5)
        self.continuous(tracker, 29)
        malformed = observation(track='track-2')
        del malformed['ppe']['helmet']['status']
        with self.assertRaises(ValueError):
            tracker.update([observation(), malformed], 3)
        self.assertEqual(tracker.last_timestamp, 2.9)
        self.assertIsNone(tracker.states[('track-1', 'helmet')]['event_id'])
        retry = tracker.update([observation()], 3)
        self.assertEqual(retry[0]['kind'], 'start')

    def test_mutable_scope_is_snapshotted_not_aliased(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=.5)
        scope = ['v1', ['roi-A']]
        person = observation(scope=scope)
        for index in range(31):
            tracker.update([person], index / 10)
        scope[1][0] = 'roi-B'
        result = tracker.update([person], 3.1)
        self.assertEqual(result[0]['close_reason'], 'policy_scope_changed')
        self.assertEqual(result[0]['policy_scope_key'], ('v1', ('roi-A',)))

    def test_invalid_departure_frame_does_not_advance_clock(self):
        tracker = EventLifecycle('CAM', missing_grace_seconds=.5)
        self.continuous(tracker, 29)
        with self.assertRaises(ValueError):
            tracker.update([observation()], 3, departed_tracking_ids=[['bad']])
        self.assertEqual(tracker.last_timestamp, 2.9)
        self.assertEqual(tracker.update([observation()], 3)[0]['kind'], 'start')
