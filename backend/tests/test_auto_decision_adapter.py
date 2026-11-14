"""No real model, network, or production database access."""
from unittest import TestCase
from backend.auto_decision.adapter import DiagnosticOnlyAdapter


class AdapterTests(TestCase):
    def test_never_issues_proof_even_with_forged_claims(self):
        adapter = DiagnosticOnlyAdapter()
        claims = {'observed_confidence': 1, 'calibrated_violation_probability': 1,
                  'model_artifact_attested': True, 'validation_reference': 'approved',
                  'whole_event_evidence': {'cameras': [{'manifest_coverage': {'complete': True}}]}}
        self.assertIsNone(adapter.validate(None, 'event', claims, claims))
        self.assertEqual(adapter.policy_blockers(claims), list(adapter.BLOCKERS))
        result = adapter.diagnose(claims)
        self.assertEqual(result['observed_detector_confidence'], 1)
        self.assertIsNone(result['calibrated_violation_probability'])
        self.assertFalse(result['automatic_confirmation_allowed'])

    def test_malformed_cameras_do_not_raise_or_validate(self):
        adapter = DiagnosticOnlyAdapter()
        for cameras in (None, 1, True, 'x', {}, [], [1], [None], [{}], [{'manifest_coverage': 1}]):
            with self.subTest(cameras=cameras):
                result = adapter.diagnose({'whole_event_evidence': {'cameras': cameras}})
                self.assertFalse(result['camera_manifest_structure_valid'])
                self.assertFalse(result['automatic_confirmation_allowed'])
        for value in (None, [], 1, 'x', {'whole_event_evidence': 1}):
            self.assertFalse(adapter.diagnose(value)['automatic_confirmation_allowed'])

    def test_invalid_scores_not_echoed_as_confidence(self):
        for score in (True, False, float('nan'), float('inf'), -1, 2, 10**1000, -(10**1000), '0.99', None):
            with self.subTest(score=score):
                result = DiagnosticOnlyAdapter().diagnose({'observed_confidence': score})
                self.assertIsNone(result['observed_detector_confidence'])
                self.assertIsNone(result['calibrated_violation_probability'])
