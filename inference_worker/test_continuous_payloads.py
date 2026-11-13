from dataclasses import dataclass
from unittest import TestCase
from inference_worker.event_lifecycle import EventLifecycle
from inference_worker.continuous_payloads import start_payload, update_payload, segment_payload


class PayloadTests(TestCase):
    def setUp(self):
        self.session = 'a' * 32
        self.lifecycle = EventLifecycle('CAM_1', missing_grace_seconds=.5)
        self.person = {'tracking_id': 'track-1', 'policy_scope_key': ('v1', ('roi-A',)),
                       'ppe': {'helmet': {'status': 'suspected_no_helmet'}}}
        for n in range(31):
            rows = self.lifecycle.update([self.person], n / 10)
        self.start = rows[0]

    def test_start_keeps_id_and_no_fabricated_score(self):
        body = start_payload(self.start, session_id=self.session, model_version='test-model')
        self.assertEqual(body['event_id'], self.start['event_id'])
        self.assertEqual(body['timestamp_end'], '1970-01-01T00:00:03.000000+00:00')
        self.assertTrue(body['review_only'])
        self.assertIsNone(body['confidence'])
        self.assertEqual(len(body['policy_key']), 64)
        self.assertEqual(body, start_payload(self.start, session_id=self.session, model_version='test-model'))

    def test_update_sequence_is_explicit_and_close_needs_manifest(self):
        progress = self.lifecycle.update([self.person], 3.1)[0]
        self.assertEqual(update_payload(progress, session_id=self.session, sequence=1)['kind'], 'progress')
        close = self.lifecycle.close_all('shutdown')[0]
        with self.assertRaises(ValueError):
            update_payload(close, session_id=self.session, sequence=2)
        body = update_payload(close, session_id=self.session, sequence=2,
                              complete=False, final_segment_count=0)
        self.assertFalse(body['complete'])
        self.assertEqual(body['close_reason'], 'shutdown')

    def test_segment_uses_event_sequence_and_exclusive_interval(self):
        @dataclass
        class Segment:
            capture_start: float = 10
            capture_end: float = 70
            sequence: int = 100
        body = segment_payload(Segment(), session_id=self.session, event_sequence=0)
        self.assertEqual(body['sequence'], 0)
        self.assertEqual(body['capture_end_at'], '1970-01-01T00:01:10.000000+00:00')
        with self.assertRaises(ValueError):
            segment_payload(Segment(capture_end=70.1), session_id=self.session, event_sequence=0)

    def test_invalid_session_and_implicit_sequences_rejected(self):
        with self.assertRaises(ValueError):
            start_payload(self.start, session_id='not-a-session', model_version='test')
        progress = self.lifecycle.update([self.person], 3.1)[0]
        for sequence in (0, True, 1.5):
            with self.assertRaises(ValueError):
                update_payload(progress, session_id=self.session, sequence=sequence)

    def test_verified_progress_carries_full_interval_minimum_confidence(self):
        lifecycle = EventLifecycle('CAM_1', missing_grace_seconds=.5)
        person = {'tracking_id': 'track-1', 'status': 'suspected_no_helmet',
                  'verified_ppe_confidence': .9}
        for n in range(31):
            lifecycle.update([person], n / 10)
        person['verified_ppe_confidence'] = .7
        progress = lifecycle.update([person], 3.1)[0]
        self.assertEqual(update_payload(progress, session_id=self.session, sequence=1)['confidence'], .7)
        person['verified_ppe_confidence'] = .95
        progress = lifecycle.update([person], 3.2)[0]
        self.assertEqual(update_payload(progress, session_id=self.session, sequence=2)['confidence'], .7)


class CapturePayloadTests(TestCase):
    def make(self, *, done=True, reasons=(), end=7, segments=((0,7),), verified=True):
        from types import SimpleNamespace
        from inference_worker.continuous_payloads import capture_payload
        status={'start':0,'end':end,'cameras':{'cam':{'done':done,'reasons':list(reasons),
            'segments':[SimpleNamespace(capture_start=a,capture_end=b) for a,b in segments]}}}
        return capture_payload(status,camera_id='cam',clock=SimpleNamespace(
            offset=0,error_bound=0,verified=verified),session_id='a'*32,sequence=1,
            time_basis='shared_monotonic',required_end=7)

    def test_complete_and_collecting_are_distinct(self):
        self.assertEqual(self.make()['state'],'complete')
        self.assertEqual(self.make(done=False,end=None)['state'],'collecting')

    def test_missing_evidence_is_not_silently_shortened(self):
        body=self.make(reasons=['source_gap'],segments=((0,3),))
        self.assertEqual(body['state'],'incomplete')
        self.assertEqual(len(body['missing_ranges']),1)
        self.assertTrue(body['missing_ranges'][0]['start_at'].endswith('03.000000+00:00'))

    def test_clock_unverified_cannot_report_complete(self):
        self.assertEqual(self.make(verified=False)['state'],'incomplete')

    def test_provisional_extra_and_false_complete_rejected(self):
        for segments in (((0,8),),((0,3),(4,7)),()):
            with self.assertRaises(ValueError):self.make(segments=segments)

    def test_previous_missing_intervals_cannot_be_erased_by_complete(self):
        from types import SimpleNamespace
        from inference_worker.continuous_payloads import capture_payload
        status={'start':0,'end':7,'cameras':{'cam':{'done':True,'reasons':[],
            'segments':[SimpleNamespace(capture_start=0,capture_end=7)]}}}
        with self.assertRaisesRegex(ValueError,'recovery policy'):
            capture_payload(status,camera_id='cam',clock=SimpleNamespace(
                offset=0,error_bound=0,verified=True),session_id='a'*32,sequence=2,
                time_basis='shared_monotonic',required_end=7,
                previous_missing_ranges=[{'start_at':'1970-01-01T00:00:03Z',
                    'end_at':'1970-01-01T00:00:07Z','reason':'source_gap'}])

    def test_epoch_float_rounding_matches_wire_microsecond_precision(self):
        from types import SimpleNamespace
        from inference_worker.continuous_payloads import capture_payload
        start=1794503407.426237;end=1794503415.326237
        status={'start':start,'end':end,'cameras':{'cam':{'done':True,'reasons':[],
            'segments':[SimpleNamespace(capture_start=start,capture_end=1794503415.3262372)]}}}
        body=capture_payload(status,camera_id='cam',clock=SimpleNamespace(offset=0,error_bound=0,verified=True),
            session_id='a'*32,sequence=1,time_basis='source_utc_corrected',required_end=end)
        self.assertEqual(body['state'],'complete')
        status['cameras']['cam']['segments'][0].capture_end=end+.000002
        with self.assertRaises(ValueError):capture_payload(status,camera_id='cam',
            clock=SimpleNamespace(offset=0,error_bound=0,verified=True),session_id='a'*32,
            sequence=1,time_basis='source_utc_corrected',required_end=end)
