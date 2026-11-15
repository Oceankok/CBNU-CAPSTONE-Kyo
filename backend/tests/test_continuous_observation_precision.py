"""One-microsecond producer/wire precision; never round or invent observations."""
from datetime import datetime,timezone
from unittest import TestCase
from uuid import uuid4

from pydantic import ValidationError

from backend.processing.lifecycle import Start
from backend.tests import test_continuous_events as fixtures
from backend.db.event_repository import get_connection
from inference_worker.event_lifecycle import EventLifecycle
from inference_worker.continuous_payloads import start_payload


def payload(**changes):
    return {**dict(event_id='EVT_'+uuid4().hex,camera_id='CAM_001',tracking_id='precision-track',
        ppe_type='vest',timestamp_start='2026-11-15T10:10:41.579000+00:00',
        timestamp_end='2026-11-15T10:10:45.445667+00:00',confidence=None,
        frame_sample_count=52,model_version='precision-fixture',review_only=True,
        session_id=uuid4().hex,policy_key='precision',observed_seconds=2.9999992847442627,
        gap_seconds=0.8666677474975586,gap_count=6),**changes}


class ObservationPrecisionValidationTests(TestCase):
    def test_exact_failed_native_values_are_accepted_without_changing_measurements(self):
        body=payload();parsed=Start.model_validate(body)
        self.assertEqual(parsed.observed_seconds,body['observed_seconds'])
        self.assertEqual(parsed.gap_seconds,body['gap_seconds'])
        self.assertIsNone(parsed.confidence);self.assertTrue(parsed.review_only)

    def test_real_producer_near_threshold_roundtrip_respects_microsecond_wire(self):
        epoch=datetime(2026,11,15,10,0,tzinfo=timezone.utc).timestamp()
        engine=EventLifecycle('CAM_001',missing_grace_seconds=1)
        observation=[{'tracking_id':'real-producer-fixture','policy_scope_key':'precision',
                      'ppe_type':'vest','status':'suspected_no_vest','verified_ppe_confidence':None}]
        transitions=[]
        for i in range(16):
            instant=epoch+i*.2-(.0000007152557373 if i==15 else 0)
            transitions.extend(engine.update(observation,instant))
        start,=transitions
        self.assertLess(start['observed_seconds'],3)
        self.assertGreaterEqual(start['observed_seconds']+1e-6,3)
        body=start_payload(start,session_id=uuid4().hex,model_version='precision-fixture')
        parsed=Start.model_validate(body)
        self.assertEqual(parsed.observed_seconds,start['observed_seconds'])

    def test_genuinely_short_observation_and_span_are_rejected(self):
        for change in ({'observed_seconds':2.9},{'observed_seconds':2.999998},
                       {'timestamp_end':'2026-11-15T10:10:44.578998+00:00',
                        'observed_seconds':2.999998,'gap_seconds':0,'gap_count':0}):
            with self.subTest(change=change),self.assertRaises(ValidationError):
                Start.model_validate(payload(**change))

    def test_inconsistent_and_nonfinite_observation_is_still_rejected(self):
        for change in ({'observed_seconds':3.1,'timestamp_end':'2026-11-15T10:10:44.579000+00:00',
                        'gap_seconds':0,'gap_count':0},
                       {'observed_seconds':3.000002,'timestamp_end':'2026-11-15T10:10:44.579000+00:00',
                        'gap_seconds':0,'gap_count':0},
                       {'observed_seconds':float('nan')},{'gap_seconds':-1},
                       {'tracking_id':None},{'confidence':.9}):
            with self.subTest(change=change),self.assertRaises(ValidationError):
                Start.model_validate(payload(**change))


class ObservationPrecisionApiTests(TestCase):
    setUp=fixtures.ContinuousEventTests.setUp
    ts=fixtures.ContinuousEventTests.ts

    def test_native_boundary_accepts_once_preserves_values_and_rule_exclusion(self):
        body=payload();response=self.client.post(self.base,headers=self.nodes[0],json=body)
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(self.client.post(self.base,headers=self.nodes[0],json=body).status_code,200)
        with get_connection() as conn:
            stored=conn.execute('SELECT observed_seconds,gap_seconds,gap_count FROM processing_event_lifecycle WHERE event_id=?',(body['event_id'],)).fetchone()
            self.assertEqual(stored['observed_seconds'],body['observed_seconds'])
            self.assertEqual(stored['gap_count'],6)
            self.assertEqual(conn.execute('SELECT count(*) FROM candidate_event WHERE event_id=?',(body['event_id'],)).fetchone()[0],1)
            self.assertIsNone(conn.execute('SELECT 1 FROM event_review WHERE event_id=?',(body['event_id'],)).fetchone())
        preview=self.client.post('/api/auto-decisions/settings/preview',headers=self.admin,json={
            'mode':'shadow','decision_method':'continuous_rules_v1',
            'rule_criteria':{'minimum_observed_seconds':3.0,'minimum_frame_samples':16},
            'model_versions':['precision-fixture'],'ppe_types':['vest'],'camera_ids':['CAM_001'],
            'event_ids':[body['event_id']]}).raise_for_status().json()['items'][0]
        self.assertFalse(preview['rule_evaluation']['eligible'])
        self.assertIn('continuous_observation_incomplete',preview['blocking_reasons'])
        self.assertIn('minimum_observed_seconds_not_met',preview['blocking_reasons'])

    def test_short_and_inconsistent_api_requests_create_no_event(self):
        for change in ({'observed_seconds':2.999998},
                       {'observed_seconds':3.1,'gap_seconds':0,
                        'timestamp_end':'2026-11-15T10:10:44.579000+00:00'}):
            body=payload(**change)
            response=self.client.post(self.base,headers=self.nodes[0],json=body)
            self.assertEqual(response.status_code,422,response.text)
            with get_connection() as conn:
                self.assertIsNone(conn.execute('SELECT 1 FROM candidate_event WHERE event_id=?',(body['event_id'],)).fetchone())
