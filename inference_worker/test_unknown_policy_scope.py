"""Synthetic policy-resolution/lifecycle cases, not attribution of a live split."""
from copy import deepcopy
from unittest import TestCase

from inference_worker.event_lifecycle import EventLifecycle
from inference_worker.run_continuous import policy_assessments


POLICY = dict(configured=True, scope='work_area', policy_version='v1', configuration_issues=[],
    zone_required_ppe=['helmet', 'vest'], event_supported_ppe=['helmet', 'vest'],
    areas=[dict(area_id='roi-A', points=[[0, 0], [.5, 0], [.5, 1], [0, 1]],
                equipment_required_ppe=['helmet'])])
OUTSIDE = (400, 100, 500, 200)


def person(*, track='track-1', bbox=OUTSIDE, status='absent', policy=POLICY):
    row = dict(tracking_id=track, person_confidence=.9, person_bbox=bbox,
               ppe={ppe: dict(status=ppe if status == 'present' else 'suspected_no_' + ppe,
                              verified_ppe_confidence=None) for ppe in ('helmet', 'vest')})
    return policy_assessments([row], policy, (360, 640, 3), review_only=True)[0]


class UnknownPolicyScopeTests(TestCase):
    def make(self):
        return EventLifecycle('CAM', missing_grace_seconds=1, rewear_seconds=.5,
                              qualification_grace_seconds=.5)

    def feed(self, lifecycle, start=0, end=30, **changes):
        output = []
        for step in range(start, end + 1):
            output += lifecycle.update([person(**changes)], step / 10)
        return output

    def qualified(self):
        lifecycle = self.make()
        starts = self.feed(lifecycle)
        self.assertEqual(len(starts), 2)
        return lifecycle, {row['ppe_type']: row['event_id'] for row in starts}

    def test_one_invalid_position_preserves_ids_and_excludes_unknown_samples(self):
        lifecycle, ids = self.qualified()
        unresolved = person(bbox=(-1, 100, 500, 200))
        self.assertNotIn('policy_scope_key', unresolved)
        self.assertTrue(all(row['status'] == 'unknown' for row in unresolved['ppe'].values()))
        self.assertEqual(lifecycle.update([unresolved], 3.1), [])
        rows = lifecycle.update([person()], 3.2)
        self.assertEqual({row['ppe_type']: row['event_id'] for row in rows}, ids)
        for row in rows:
            self.assertEqual(row['kind'], 'progress')
            self.assertEqual(row['frame_sample_count'], 32)
            self.assertAlmostEqual(row['observed_seconds'], 3)
            self.assertEqual(row['observation_gap_count'], 1)
            self.assertAlmostEqual(row['observation_gap_seconds'], .2)
            self.assertEqual(row['policy_scope_key'], person()['policy_scope_key'])

    def test_visible_long_unknown_scope_retains_episode_until_person_missing(self):
        lifecycle, ids = self.qualified()
        for step in range(31, 81):
            self.assertEqual(lifecycle.update([person(bbox=None)], step / 10), [])
        rows = lifecycle.update([person()], 8.1)
        self.assertEqual({row['ppe_type']: row['event_id'] for row in rows}, ids)
        self.assertTrue(all(row['frame_sample_count'] == 32 for row in rows))
        self.assertTrue(all(abs(row['observed_seconds'] - 3) < 1e-6 for row in rows))
        self.assertEqual(lifecycle.update([], 8.2), [])
        closes = lifecycle.update([], 9.2)
        self.assertEqual({row['event_id'] for row in closes}, set(ids.values()))
        self.assertTrue(all(row['close_reason'] == 'observation_gap' for row in closes))

    def test_missing_scope_cannot_count_absence_or_confirm_rewear(self):
        for status in ('absent', 'present'):
            with self.subTest(status=status):
                lifecycle, ids = self.qualified()
                unresolved = person(status=status)
                unresolved.pop('policy_scope_key')
                self.assertEqual(lifecycle.update([unresolved], 3.1), [])
                rows = lifecycle.update([person()], 3.2)
                self.assertEqual({row['ppe_type']: row['event_id'] for row in rows}, ids)
                self.assertTrue(all(row['frame_sample_count'] == 32 for row in rows))
                self.assertTrue(all(abs(row['observed_seconds'] - 3) < 1e-6 for row in rows))

    def test_short_unknown_before_qualification_uses_existing_grace(self):
        lifecycle = self.make()
        self.assertEqual(self.feed(lifecycle, end=20), [])
        for step in range(21, 25):
            self.assertEqual(lifecycle.update([person(bbox=None)], step / 10), [])
        starts = self.feed(lifecycle, start=25, end=35)
        self.assertEqual(len(starts), 2)
        self.assertTrue(all(row['start_time'] == 0 for row in starts))
        self.assertTrue(all(abs(row['observed_seconds'] - 3) < 1e-6 for row in starts))

    def test_long_unknown_before_qualification_restarts_observation_window(self):
        lifecycle = self.make()
        self.assertEqual(self.feed(lifecycle, end=20), [])
        for step in range(21, 27):
            self.assertEqual(lifecycle.update([person(bbox=None)], step / 10), [])
        self.assertEqual(lifecycle.states, {})
        starts = self.feed(lifecycle, start=27, end=57)
        self.assertEqual(len(starts), 2)
        self.assertTrue(all(row['start_time'] == 2.7 for row in starts))

    def test_known_area_version_and_required_ppe_changes_still_close(self):
        revised = deepcopy(POLICY)
        revised['policy_version'] = 'v2'
        ppe_policy = deepcopy(POLICY)
        ppe_policy['zone_required_ppe'] = ['helmet']
        for changes in ({'bbox': (100, 100, 200, 200)}, {'policy': revised}, {'policy': ppe_policy}):
            with self.subTest(changes=changes):
                lifecycle, ids = self.qualified()
                self.assertEqual(lifecycle.update([person(bbox=None)], 3.1), [])
                closes = lifecycle.update([person(**changes)], 3.2)
                self.assertEqual({row['event_id'] for row in closes}, set(ids.values()))
                self.assertTrue(all(row['close_reason'] == 'policy_scope_changed' for row in closes))
                self.assertTrue(all(row['policy_scope_key'] == person()['policy_scope_key'] for row in closes))

    def test_rewear_after_unknown_still_closes_and_new_absence_gets_new_ids(self):
        lifecycle, ids = self.qualified()
        self.assertEqual(lifecycle.update([person(bbox=None)], 3.1), [])
        self.assertEqual(lifecycle.update([person(status='present')], 3.2), [])
        closes = lifecycle.update([person(status='present')], 3.7)
        self.assertEqual({row['event_id'] for row in closes}, set(ids.values()))
        self.assertTrue(all(row['close_reason'] == 'ppe_present_after_unknown' for row in closes))
        starts = self.feed(lifecycle, start=38, end=68)
        self.assertEqual(len(starts), 2)
        self.assertTrue(set(ids.values()).isdisjoint(row['event_id'] for row in starts))

    def test_other_person_keeps_separate_qualification_and_ids(self):
        lifecycle, first_ids = self.qualified()
        starts = []
        for step in range(31, 62):
            starts += lifecycle.update([person(bbox=None), person(track='track-2')], step / 10)
        self.assertEqual(len(starts), 2)
        self.assertTrue(all(row['tracking_id'] == 'track-2' for row in starts))
        self.assertTrue(set(first_ids.values()).isdisjoint(row['event_id'] for row in starts))
        self.assertEqual({state['event_id'] for (track, _), state in lifecycle.states.items()
                          if track == 'track-1'}, set(first_ids.values()))

    def test_legacy_unscoped_absence_remains_supported(self):
        lifecycle = self.make()
        starts = []
        for step in range(31):
            row = person()
            row.pop('policy_scope_key')
            starts += lifecycle.update([row], step / 10)
        self.assertEqual(len(starts), 2)
        self.assertTrue(all(row['policy_scope_key'] is None for row in starts))

    def test_visible_unresolved_scope_cannot_become_legacy_after_qualification_expires(self):
        lifecycle = self.make()
        self.assertEqual(self.feed(lifecycle, end=20), [])
        for step in range(21, 61):
            unresolved = person()
            unresolved.pop('policy_scope_key')  # Contradictory direct-caller absence.
            self.assertEqual(lifecycle.update([unresolved], step / 10), [])
        self.assertEqual(lifecycle.states, {})
        starts = self.feed(lifecycle, start=61, end=91)
        self.assertEqual(len(starts), 2)
        self.assertTrue(all(row['start_time'] == 6.1 for row in starts))
        self.assertTrue(all(row['policy_scope_key'] == person()['policy_scope_key'] for row in starts))

    def test_scoped_mode_expires_with_existing_missing_person_grace(self):
        lifecycle = self.make()
        self.feed(lifecycle, end=20)
        unresolved = person()
        unresolved.pop('policy_scope_key')
        lifecycle.update([unresolved], 2.1)
        self.assertEqual(lifecycle.update([], 3.1), [])
        self.assertIn('track-1', lifecycle.scoped_tracks)
        self.assertEqual(lifecycle.update([], 3.11), [])
        self.assertEqual(lifecycle.scoped_tracks, {})

    def test_departure_and_explicit_reset_clear_scoped_mode(self):
        for reset in ('departure', 'reset'):
            with self.subTest(reset=reset):
                lifecycle = self.make()
                self.feed(lifecycle, end=20)
                if reset == 'departure':
                    lifecycle.update([], 2.1, departed_tracking_ids=['track-1'])
                else:
                    lifecycle.close_all('stream_changed')
                self.assertEqual(lifecycle.scoped_tracks, {})

    def test_scoped_mode_keeps_one_entry_per_live_track_not_frame_history(self):
        lifecycle = self.make()
        for index in range(50):
            lifecycle.update([person(track=f'track-{index}', status='present')], index * 2)
            self.assertEqual(len(lifecycle.scoped_tracks), 1)
        lifecycle.update([], 99.01)
        self.assertEqual(lifecycle.scoped_tracks, {})

    def test_malformed_later_row_cannot_mutate_scoped_presence(self):
        lifecycle = self.make()
        self.feed(lifecycle, end=20)
        before = dict(lifecycle.scoped_tracks)
        malformed = person(track='track-2')
        malformed['ppe']['helmet']['status'] = 'invalid'
        with self.assertRaises(ValueError):
            lifecycle.update([person(), malformed], 2.1)
        self.assertEqual(lifecycle.scoped_tracks, before)
        self.assertEqual(lifecycle.last_timestamp, 2)
