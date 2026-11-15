"""Offline legacy review episodes across resolved ROI requirements."""
from copy import deepcopy
from unittest import TestCase

from inference_worker.violation_tracker import ViolationTracker
from inference_worker.event_lifecycle import EventLifecycle
from inference_worker.work_area_policy import resolve_person_policy


POLICY = dict(configured=True, scope='work_area', policy_version='v1', configuration_issues=[],
    zone_required_ppe=['helmet', 'vest'], event_supported_ppe=['helmet', 'vest'],
    areas=[dict(area_id='A', points=[[0, 0], [.5, 0], [.5, 1], [0, 1]], equipment_required_ppe=['helmet'])])


def person(x=20, required=None, version='v1', statuses=None, track='track-1'):
    policy = deepcopy(POLICY)
    if required is not None:
        policy['zone_required_ppe'] = required
        policy['areas'] = []
    policy['policy_version'] = version
    effective = resolve_person_policy(policy, [x, 10, x+20, 90], 100, 100)
    return dict(tracking_id=track, person_confidence=.9,
        policy_scope_key=(version, effective['scope'], tuple(effective['area_ids']), tuple(sorted(effective['required_ppe']))),
        ppe={p: dict(status=(statuses or {}).get(p, 'suspected_no_'+p), verified_ppe_confidence=None)
             for p in ('helmet', 'vest')})


class LegacyRequiredEpisodeTests(TestCase):
    def tracker(self):
        return ViolationTracker(preserve_required_episodes=True)

    def qualified(self, tracker):
        starts = []
        for i in range(13):
            starts += tracker.update([person()], i*.25)
        self.assertEqual(len(starts), 2)
        return {r['ppe_type']: r['event_id'] for r in starts}

    def test_same_required_ppe_membership_changes_do_not_reopen_qualified_events(self):
        tracker = self.tracker()
        ids = self.qualified(tracker)
        original = {k: deepcopy(v['scope']) for k, v in tracker.lifecycle.states.items()}
        later = []
        for i in range(13, 70):
            later += tracker.update([person(x=60 if (i // 16) % 2 else 20)], i*.25)
        self.assertEqual(later, [])
        self.assertEqual({k[1]: v['event_id'] for k, v in tracker.lifecycle.states.items()}, ids)
        self.assertEqual({k: v['scope'] for k, v in tracker.lifecycle.states.items()}, original)

    def test_prequalification_membership_change_requires_fresh_three_seconds(self):
        tracker = self.tracker()
        for i in range(12):
            self.assertEqual(tracker.update([person()], i*.25), [])
        events = []
        for i in range(12, 25):
            events += tracker.update([person(x=60)], i*.25)
        self.assertEqual([e['start_time'] for e in events], [3., 3.])
        self.assertEqual([e['end_time'] for e in events], [6., 6.])

    def test_ppe_off_then_on_is_new_but_other_ppe_stays_same(self):
        tracker = self.tracker()
        ids = self.qualified(tracker)
        off = tracker.update_transitions([person(required=['helmet'])], 3.25)
        self.assertCountEqual([(e['ppe_type'], e['kind']) for e in off], [('vest', 'close'), ('helmet', 'progress')])
        events = []
        for i in range(14, 27):
            events += tracker.update([person()], i*.25)
        self.assertEqual([e['ppe_type'] for e in events], ['vest'])
        self.assertNotEqual(events[0]['event_id'], ids['vest'])
        self.assertEqual(tracker.lifecycle.states[('track-1','helmet')]['event_id'], ids['helmet'])

    def test_all_ppe_off_and_unknown_are_distinct(self):
        for required, expected_count in (([], 2), (None, 0)):
            with self.subTest(required=required):
                tracker = self.tracker()
                self.qualified(tracker)
                row = person(required=required)
                if required is None:
                    row['policy_scope_key'] = None
                    for state in row['ppe'].values(): state['status'] = 'unknown'
                for i in range(13, 30):
                    tracker.update([row], i*.25)
                events = []
                for i in range(30, 43):
                    events += tracker.update([person()], i*.25)
                self.assertEqual(len(events), expected_count)

    def test_real_policy_version_change_closes_both(self):
        tracker = self.tracker()
        self.qualified(tracker)
        rows = tracker.update_transitions([person(version='v2')], 3.25)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(e['close_reason'] == 'policy_scope_changed' for e in rows))

    def test_rewear_departure_and_new_person_remain_new_incidents(self):
        for ending in ('rewear', 'departure', 'newperson'):
            with self.subTest(ending=ending):
                tracker = self.tracker()
                ids = self.qualified(tracker)
                if ending == 'rewear':
                    for t in (3.25,3.5,3.75):
                        tracker.update([person(x=60, statuses={'helmet':'helmet','vest':'vest'})], t)
                else:
                    tracker.update([],4.25)
                events = []
                for i in range(20,33):
                    events += tracker.update([person(x=60, track='other' if ending=='newperson' else 'track-1')], i*.25)
                self.assertEqual(len(events), 2)
                self.assertTrue(all(e['event_id'] != ids[e['ppe_type']] for e in events))

    def test_evidence_mode_change_is_not_hidden(self):
        tracker = self.tracker()
        self.qualified(tracker)
        row = person(x=60)
        for ppe in row['ppe'].values(): ppe['verified_ppe_confidence'] = .9
        rows = tracker.update_transitions([row], 3.25)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(e['kind']=='close' for e in rows))

    def test_continuous_and_default_adapter_stay_strict(self):
        for tracker in (ViolationTracker(), EventLifecycle('CAM', missing_grace_seconds=1)):
            for i in range(13): tracker.update([person()], i*.25)
            lifecycle = tracker.lifecycle if isinstance(tracker, ViolationTracker) else tracker
            lifecycle_rows = (tracker.update_transitions([person(x=60)],3.25)
                              if isinstance(tracker,ViolationTracker) else tracker.update([person(x=60)],3.25))
            self.assertEqual(len(lifecycle_rows), 2)
            self.assertTrue(all(r['close_reason']=='policy_scope_changed' for r in lifecycle_rows))

    def run_review_memberships(self, policy):
        from inference_worker.test_server_mock import FakeVideoClient
        from inference_worker.test_roi_runner_end_to_end import ReviewDetector
        from inference_worker.test_runner_resilience import RunnerResilienceTests
        client = FakeVideoClient(18)
        client.get_ppe_policy = lambda camera: deepcopy(policy)

        class MovingMeasurement(ReviewDetector):
            def detect(self, frame):
                rows = super().detect(frame)
                phase = (self.frames_seen-1) % 48
                x = (4 if phase < 16 else 4+(phase-16)*4.75 if phase < 24
                     else 42 if phase < 40 else 42-(phase-40)*4.75)
                if rows:
                    rows[0]['bbox'] = [x,2,x+20,60]
                return rows

        output, _ = RunnerResilienceTests().run_runner(client, review=True,
            detector=MovingMeasurement(warmup_frames=0))
        return client, output

    def test_real_review_runner_submits_once_per_ppe_across_membership_changes(self):
        client, output = self.run_review_memberships(POLICY)
        self.assertEqual(len(client.submitted_events), 2)
        self.assertEqual({e['ppe_type'] for e in client.submitted_events}, {'helmet','vest'})
        self.assertTrue(all(e['review_only'] and e['confidence'] is None for e in client.submitted_events))
        self.assertNotIn('policy_scope_changed', output)

    def test_real_review_runner_known_off_does_not_hold_like_unknown(self):
        policy = deepcopy(POLICY)
        policy['zone_required_ppe'] = []
        policy['areas'][0]['equipment_required_ppe'] = ['helmet','vest']
        client, output = self.run_review_memberships(policy)
        self.assertEqual(len(client.submitted_events), 4)
        self.assertEqual(len({e['tracking_id'] for e in client.submitted_events}), 1)
        self.assertIn('policy_scope_changed', output)
