"""Preserved native CLOSE bodies; endpoint precision never fills internal gaps."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4
import os

from backend.tests import test_continuous_events as fixtures
from backend.db.event_repository import get_connection
from backend.processing.capture import Range, _covers
from backend.processing.lifecycle import WIRE_TIME_EPSILON
from backend.auto_decision.evidence import coverage


NATIVE = json.loads((Path(__file__).parent/'fixtures/continuous_close_wire_precision.json').read_text())


class WireCoverageTests(TestCase):
    setUp = fixtures.ContinuousEventTests.setUp
    ts = fixtures.ContinuousEventTests.ts

    def prepare(self, native=NATIVE[0], *, deficit_us=1, start_deficit_us=0, missing_segment=False):
        self.ident = 'EVT_'+uuid4().hex
        self.close_body = native['body']
        self.session = self.close_body['session_id']
        start = native['segments'][0]['capture_start_at']
        self.t = datetime.fromisoformat(start)+timedelta(seconds=2)
        body = {**self.body, 'event_id': self.ident, 'session_id': self.session,
                'ppe_type': 'vest' if native['event_id'] == NATIVE[1]['event_id'] else 'helmet',
                'timestamp_start': self.ts(0), 'timestamp_end': self.ts(3)}
        self.client.post(self.base, headers=self.nodes[0], json=body).raise_for_status()
        for sequence in range(1, 5):
            progress = {**self.close_body, 'sequence': sequence, 'kind': 'progress'}
            for key in ['close_reason', 'complete', 'final_segment_count']:
                progress.pop(key)
            self.client.post(self.base+'/'+self.ident+'/updates', headers=self.nodes[0], json=progress).raise_for_status()
        self.start_at = datetime.fromisoformat(start)+timedelta(microseconds=start_deficit_us)
        self.required_end = datetime.fromisoformat(self.close_body['timestamp_end'])+timedelta(seconds=2)
        self.end_at = self.required_end-timedelta(microseconds=deficit_us)
        segments = native['segments'][:-1] if missing_segment else native['segments']
        for segment in segments:
            payload = {'session_id': self.session, **{k: segment[k] for k in
                       ['sequence', 'capture_start_at', 'capture_end_at']}}
            if segment['sequence'] == 0:
                payload['capture_start_at'] = self.start_at.isoformat()
            if segment['sequence'] == 5:
                payload['capture_end_at'] = self.end_at.isoformat()
            self.client.post(self.base+'/'+self.ident+'/segments', headers=self.nodes[0], json=payload).raise_for_status()

    def close(self, **changes):
        return self.client.post(self.base+'/'+self.ident+'/updates', headers=self.nodes[0],
                                json={**self.close_body, **changes})

    def report(self, **changes):
        body = dict(session_id=self.session, sequence=1, state='complete',
                    captured_ranges=[{'start_at': self.start_at.isoformat(), 'end_at': self.end_at.isoformat()}],
                    missing_ranges=[], earliest_available_at=self.start_at.isoformat(),
                    time_basis='shared_monotonic', clock_offset_seconds=0, clock_uncertainty_seconds=0)
        with patch.dict(os.environ, {'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS': '0'}):
            return self.client.post(self.base+'/'+self.ident+'/cameras/CAM_001/capture-reports',
                                    headers=self.nodes[0], json={**body, **changes})

    def test_both_preserved_native_close_bodies_accept_idempotently_without_rewriting(self):
        for native in NATIVE:
            with self.subTest(event=native['event_id']):
                encoded = json.dumps(native['body'], sort_keys=True, separators=(',', ':')).encode()
                self.assertEqual(hashlib.sha256(encoded).hexdigest(), native['body_sha256'])
                self.prepare(native)
                for _ in range(2):
                    response = self.close()
                    self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.report().status_code, 200)
                with get_connection() as conn:
                    row = conn.execute('SELECT phase,observed_seconds,gap_seconds,gap_count FROM processing_event_lifecycle WHERE event_id=?', (self.ident,)).fetchone()
                    self.assertEqual(row['phase'], 'closed')
                    for key in ['observed_seconds', 'gap_seconds', 'gap_count']:
                        self.assertEqual(row[key], native['body'][key])
                    stored = conn.execute('SELECT capture_end_at FROM processing_event_segment WHERE event_id=? ORDER BY sequence DESC LIMIT 1', (self.ident,)).fetchone()[0]
                    self.assertEqual(datetime.fromisoformat(stored), self.end_at)
                    self.assertIsNone(conn.execute('SELECT 1 FROM event_review WHERE event_id=?', (self.ident,)).fetchone())
                preview = self.client.post('/api/auto-decisions/settings/preview', headers=self.admin, json={
                    'mode': 'shadow', 'decision_method': 'continuous_rules_v1',
                    'rule_criteria': {'minimum_observed_seconds': 3, 'minimum_frame_samples': 16},
                    'model_versions': ['test'], 'ppe_types': ['helmet', 'vest'],
                    'camera_ids': ['CAM_001'], 'event_ids': [self.ident]}).raise_for_status().json()['items'][0]
                self.assertFalse(preview['rule_evaluation']['eligible'])
                self.assertIn('continuous_observation_incomplete', preview['blocking_reasons'])

    def test_one_microsecond_start_edge_is_accepted_but_two_microseconds_is_not(self):
        self.prepare(start_deficit_us=1)
        self.close().raise_for_status()
        self.report().raise_for_status()
        self.prepare(start_deficit_us=2)
        self.assertEqual(self.close().json()['detail'], 'segment_coverage_incomplete')

    def test_actual_producer_payload_is_accepted_without_changing_captured_range(self):
        from types import SimpleNamespace
        from inference_worker.continuous_payloads import capture_payload
        self.prepare()
        self.close().raise_for_status()
        segments=[SimpleNamespace(
            capture_start=datetime.fromisoformat(s['capture_start_at']).timestamp(),
            capture_end=datetime.fromisoformat(s['capture_end_at']).timestamp()) for s in NATIVE[0]['segments']]
        status={'start':self.start_at.timestamp(),'end':self.required_end.timestamp(),
                'cameras':{'CAM_001':{'done':True,'reasons':[],'segments':segments}}}
        body=capture_payload(status,camera_id='CAM_001',clock=SimpleNamespace(
            offset=0,error_bound=0,verified=True),session_id=self.session,sequence=1,
            time_basis='source_utc_corrected',required_end=self.required_end.timestamp())
        self.assertEqual(body['captured_ranges'][-1]['end_at'], self.end_at.isoformat())
        with patch.dict(os.environ, {'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS':'0'}):
            response=self.client.post(self.base+'/'+self.ident+'/cameras/CAM_001/capture-reports',
                                      headers=self.nodes[0],json=body)
        self.assertEqual(response.status_code,200,response.text)

    def test_outer_one_microsecond_overshoot_does_not_allow_inexact_adjacency(self):
        self.prepare(deficit_us=-1, start_deficit_us=-1)
        response = self.client.post(self.base+'/'+self.ident+'/segments', headers=self.nodes[0], json={
            'session_id': self.session, 'sequence': 6,
            'capture_start_at': (self.end_at+WIRE_TIME_EPSILON).isoformat(),
            'capture_end_at': (self.end_at+2*WIRE_TIME_EPSILON).isoformat()})
        # This outer endpoint is two microseconds beyond the permitted wire
        # range and must not enlarge the accepted evidence.
        self.assertEqual(response.status_code, 422, response.text)
        self.close().raise_for_status()
        self.report().raise_for_status()
        self.prepare()
        response = self.client.post(self.base+'/'+self.ident+'/segments', headers=self.nodes[0], json={
            'session_id': self.session, 'sequence': 6,
            'capture_start_at': (self.end_at+WIRE_TIME_EPSILON).isoformat(),
            'capture_end_at': (self.end_at+2*WIRE_TIME_EPSILON).isoformat()})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail'], 'segment_sequence_or_boundary_conflict')

    def test_genuinely_short_postroll_and_missing_manifest_are_rejected(self):
        for deficit in [2, 66667]:
            self.prepare(deficit_us=deficit)
            response = self.close()
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()['detail'], 'segment_coverage_incomplete')
        self.prepare(missing_segment=True)
        self.assertEqual(self.close().json()['detail'], 'segment_manifest_incomplete')

    def test_capture_missing_and_internal_microsecond_gap_stay_incomplete(self):
        self.prepare()
        self.close().raise_for_status()
        middle = self.t+timedelta(seconds=1)
        ranges = [{'start_at': self.start_at.isoformat(), 'end_at': middle.isoformat()},
                  {'start_at': (middle+WIRE_TIME_EPSILON).isoformat(), 'end_at': self.end_at.isoformat()}]
        for change in [dict(captured_ranges=ranges),
                       dict(missing_ranges=[{'start_at': middle.isoformat(),
                                             'end_at': (middle+WIRE_TIME_EPSILON).isoformat(), 'reason': 'unknown'}]),
                       dict(captured_ranges=[{'start_at': self.start_at.isoformat(),
                                               'end_at': (self.required_end-2*WIRE_TIME_EPSILON).isoformat()}])]:
            response = self.report(**change)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()['detail'], 'continuous_capture_incomplete')


class StrictInternalCoverageTests(TestCase):
    def test_default_capture_regression_and_automatic_manifest_union_remain_strict(self):
        a = datetime.fromisoformat('2026-11-15T10:36:30.216333+00:00')
        b = a+timedelta(seconds=10)
        ranges = [Range(start_at=a, end_at=b-WIRE_TIME_EPSILON)]
        self.assertFalse(_covers(ranges, a, b))
        self.assertTrue(_covers(ranges, a, b, endpoint_epsilon=WIRE_TIME_EPSILON))
        result = coverage(a.isoformat(), b.isoformat(), [(a.isoformat(), (b-WIRE_TIME_EPSILON).isoformat())])
        self.assertFalse(result['complete'])
        self.assertEqual(len(result['uncovered_ranges']), 1)
