import os
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from backend.tests import test_auto_decision_preview as fixtures
from backend.auto_decision.routes import PreviewRequest
from backend.auto_decision.readiness import evaluate_readiness
from backend.db.event_repository import get_connection


class AutomaticReadinessTests(unittest.TestCase):
    setUp=fixtures.AutoDecisionPreviewTests.setUp
    def state(self):
        # dict(row) compares values on both sqlite.Row and PostgreSQL's mapping row.
        with get_connection() as conn:
            return {table:[dict(row) for row in conn.execute('SELECT * FROM '+table+' ORDER BY 1')] for table in
                ('candidate_event','event_review','event_review_history','quarterly_summary',
                 'education_recommendation','event_retention','event_retention_history',
                 'consent_record','false_positive_aggregate','auto_decision_shadow_run','auto_decision_shadow_item')}

    def evaluate(self, **kwargs):
        return evaluate_readiness(PreviewRequest(**self.policy),**kwargs)

    def test_experimental_threshold_never_write_authority_or_env_enabled(self):
        before=self.state()
        with patch.dict(os.environ,{'PPE_AUTO_DECISION_ENABLED':'true','AUTO_CONFIRMATION_ENABLED':'1'}):
            result=self.evaluate()
        self.assertFalse(result['automatic_confirmation_enabled'])
        self.assertFalse(result['persisted'])
        row=result['items'][0]
        self.assertFalse(row['automatic_confirmation_allowed'])
        for reason in ('automatic_confirmation_disabled','validated_decision_policy_unavailable',
                       'full_evidence_decision_validation_unavailable','automatic_review_writer_unavailable',
                       'closed_lifecycle_provenance_required'):
            self.assertIn(reason,row['blocking_reasons'])
        self.assertEqual(before,self.state())
        self.assertNotIn('storage/fake',str(result))
        self.assertNotIn(str(self.video),str(result))
        for extra in ({'enabled':True},{'validated':True}):
            with self.assertRaises(ValueError):PreviewRequest(**{**self.policy,**extra})

    def test_even_perfect_closed_diagnostic_remains_blocked(self):
        diagnostic={'collection':{'phase':'closed'},'blocking_reasons':[],
            'evidence_fingerprint':'a'*64,'evaluated_at':'fixture',
            'threshold_candidate':True,'structural_eligible':True}
        with patch('backend.auto_decision.readiness.inspect',return_value=diagnostic):
            row=self.evaluate(expected_fingerprints={self.ident:'a'*64})['items'][0]
        self.assertFalse(row['automatic_confirmation_allowed'])
        self.assertNotIn('diagnostic_evidence_changed',row['blocking_reasons'])
        self.assertIn('validated_decision_policy_unavailable',row['blocking_reasons'])

    def test_fingerprint_change_is_recomputed_not_trusted(self):
        first=self.evaluate()['items'][0]
        with get_connection() as conn:
            conn.execute('UPDATE candidate_event SET ai_confidence=0.95 WHERE event_id=?',(self.ident,))
        second=self.evaluate(expected_fingerprints={self.ident:first['evidence_fingerprint']})['items'][0]
        self.assertIn('diagnostic_evidence_changed',second['blocking_reasons'])
        self.assertNotEqual(first['evidence_fingerprint'],second['evidence_fingerprint'])
        for expected in ({self.ident:'bad'},{'other':'a'*64}):
            with self.assertRaises(ValueError):self.evaluate(expected_fingerprints=expected)

    def test_review_only_human_review_and_delete_do_not_mutate(self):
        with get_connection() as conn:
            conn.execute('UPDATE processing_review_only_event SET review_only=1 WHERE event_id=?',(self.ident,))
            conn.execute("INSERT INTO event_review(review_id,event_id,reviewer_id,review_result) VALUES('readiness-human',?,'admin02','hold')",(self.ident,))
            conn.execute("UPDATE event_retention SET decision='delete' WHERE event_id=?",(self.ident,))
        before=self.state();row=self.evaluate()['items'][0]
        for reason in ('review_only','already_reviewed_or_not_pending','media_deleted_or_expired'):
            self.assertIn(reason,row['blocking_reasons'])
        self.assertEqual(before,self.state())

    def test_missing_event_is_404_and_does_not_write_partial_results(self):
        before=self.state()
        policy=PreviewRequest(**{**self.policy,'event_ids':[self.ident,'missing']})
        with self.assertRaises(HTTPException) as raised:evaluate_readiness(policy)
        self.assertEqual(raised.exception.status_code,404)
        self.assertEqual(raised.exception.detail,'event_not_found')
        self.assertEqual(before,self.state())

    def test_no_public_enable_route_and_bypassed_model_is_revalidated(self):
        for path in ('/api/auto-decisions/enable','/api/auto-decisions/readiness'):
            self.assertEqual(self.client.post(path,headers=self.admin,json=self.policy).status_code,404)
        invalid=PreviewRequest.model_construct(**{**self.policy,'threshold':-1})
        with self.assertRaises(ValueError):evaluate_readiness(invalid)

    def test_persisted_shadow_with_low_threshold_cannot_authorize_writer(self):
        self.policy['threshold']=0
        saved=self.client.post('/api/auto-decisions/shadow',headers=self.admin,
            json={**self.policy,'request_key':'readiness-fixture'})
        self.assertEqual(saved.status_code,201,saved.text)
        before=self.state()
        result=self.evaluate()
        self.assertFalse(result['items'][0]['automatic_confirmation_allowed'])
        self.assertIn('validated_decision_policy_unavailable',result['items'][0]['blocking_reasons'])
        self.assertEqual(before,self.state())


class SnapshotAdapterContractTests(unittest.TestCase):
    def test_postgres_row_snapshot_compares_values_not_only_column_names(self):
        from backend.db.connection import Row
        before=Row(event_id='same',event_status='pending')
        after=Row(event_id='same',event_status='confirmed')
        self.assertEqual(tuple(before),tuple(after))  # Demonstrates the old false positive.
        self.assertNotEqual(dict(before),dict(after))
        self.assertEqual(dict(before),{'event_id':'same','event_status':'pending'})
