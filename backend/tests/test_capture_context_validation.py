import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from backend.capture_context.validation import CaptureContext, resolve_capture_context


class CaptureContextValidationTests(unittest.TestCase):
    def setUp(self):
        self.t = datetime(2026, 11, 13, tzinfo=timezone.utc)
        self.context = CaptureContext('ctx', 'cam', 'session', 2, 'old-zone', self.t,
                                      self.t + timedelta(minutes=10))
        self.args = dict(context=self.context, context_id='ctx', camera_id='cam',
                         session_id='session', assignment_version=2,
                         capture_start=self.t + timedelta(minutes=1),
                         capture_end=self.t + timedelta(minutes=2),
                         clock_verified=True, clock_error_seconds=1)

    def test_delayed_registration_does_not_consult_current_zone(self):
        result = resolve_capture_context(**self.args)
        self.assertEqual((result.status, result.zone_name), ('supported', 'old-zone'))
        self.assertEqual(result.assignment_version, 2)

    def test_camera_session_version_are_bound(self):
        for key, value in [('camera_id','other'), ('session_id','new'),
                           ('context_id','forged'), ('assignment_version',3), ('assignment_version',True)]:
            with self.subTest(key=key):
                self.assertEqual(resolve_capture_context(**(self.args | {key:value})).status, 'unknown')

    def test_retirement_crossing_and_exact_boundary_are_unknown(self):
        for seconds in [0, -0.5, 1]:
            result = resolve_capture_context(**(self.args | {
                'capture_end': self.context.valid_until + timedelta(seconds=seconds)}))
            self.assertEqual(result.status, 'unknown')
            self.assertIsNone(result.zone_name)

    def test_untrusted_or_missing_clock_is_unknown(self):
        for change in [{'clock_verified':False}, {'clock_verified':1},
                       {'clock_error_seconds':None}, {'clock_error_seconds':float('nan')},
                       {'clock_error_seconds':-1}, {'clock_error_seconds':True}]:
            self.assertEqual(resolve_capture_context(**(self.args | change)).status, 'unknown')

    def test_invalid_and_naive_timestamps_are_unknown(self):
        for end in [self.t, self.t.replace(tzinfo=None), '2026-11-13']:
            self.assertEqual(resolve_capture_context(**(self.args | {'capture_end':end})).status, 'unknown')
        self.assertEqual(resolve_capture_context(**(self.args | {
            'context':replace(self.context, valid_until=self.t)})).status, 'unknown')

    def test_before_history_and_clock_error_do_not_guess(self):
        self.assertEqual(resolve_capture_context(**(self.args | {
            'capture_start':self.t, 'clock_error_seconds':1})).status, 'unknown')
        self.assertEqual(resolve_capture_context(**(self.args | {'context':None})).status, 'unknown')

    def test_unassigned_context_never_uses_current_assignment(self):
        for zone in [None, '', '  ']:
            self.assertEqual(resolve_capture_context(**(self.args | {
                'context':replace(self.context, zone_name=zone)})).status, 'unknown')

    def test_timezone_equivalence_and_open_interval(self):
        kst = timezone(timedelta(hours=9))
        result = resolve_capture_context(**(self.args | {
            'context':replace(self.context, valid_until=None),
            'capture_start':self.args['capture_start'].astimezone(kst)}))
        self.assertEqual(result.status, 'supported')

    def test_overflow_is_unknown(self):
        self.assertEqual(resolve_capture_context(**(self.args | {'clock_error_seconds':1e300})).status, 'unknown')
