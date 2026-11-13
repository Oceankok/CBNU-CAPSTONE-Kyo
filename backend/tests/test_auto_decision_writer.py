"""Writer mechanics with explicit synthetic validator, not model accuracy evidence."""
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch
from backend.tests import test_auto_decision_preview as baseline
from backend.auto_decision.routes import PreviewRequest, inspect
from backend.auto_decision import writer
from backend.db.event_repository import get_connection, update_event_review, insert_event_review

class WriterTests(TestCase):
    def setUp(self):
        baseline.AutoDecisionPreviewTests.setUp(self)
        self.enterContext(patch('backend.media.service.owned_path',return_value=self.video))
    state=baseline.AutoDecisionPreviewTests.state

    def runtime(self):
        policy=PreviewRequest(**self.policy)
        return writer.Runtime(policy,lambda conn,e,p,d:writer.ValidationProof(d['evidence_fingerprint'],'synthetic-test-only'),True)

    def eligible(self):
        # Closed whole-event/score validation is mocked explicitly; no live adapter exists.
        with get_connection() as conn:d=inspect(conn,self.ident,PreviewRequest(**self.policy))
        d['collection']={'phase':'closed'}
        d['whole_event_evidence']={'available':True,'observation_metadata_valid':True,'gap_seconds':0,'unaccounted_seconds':0,'cameras':[{'manifest_coverage':{'complete':True}}]}
        return d

    def test_default_and_unwired_validator_never_read_or_write(self):
        before=self.state()
        with patch.object(writer,'inspect',side_effect=AssertionError('must not read')):
            self.assertFalse(writer.attempt(self.ident)['applied'])
            self.assertFalse(writer.attempt(self.ident,runtime=replace(self.runtime(),enabled=False))['applied'])
        self.assertEqual(before,self.state())
        self.assertFalse(writer.attempt(self.ident,runtime=replace(self.runtime(),validator=None))['applied'])

    def test_legacy_or_incomplete_cannot_be_approved_by_adapter(self):
        # Actual diagnostic has no closed continuous provenance.
        before=self.state()
        self.assertFalse(writer.attempt(self.ident,runtime=self.runtime())['applied'])
        self.assertEqual(before,self.state())
        for field,value in [('gap_seconds',1),('unaccounted_seconds',1),('cameras',[])]:
            d=self.eligible();d['whole_event_evidence'][field]=value
            with patch.object(writer,'inspect',return_value=d):
                self.assertFalse(writer.attempt(self.ident,runtime=self.runtime())['applied'])

    def test_bad_proof_and_stale_fingerprint_never_write(self):
        for proof in (None,{},writer.ValidationProof('0'*64,'synthetic')):
            with patch.object(writer,'inspect',return_value=self.eligible()):
                result=writer.attempt(self.ident,runtime=replace(self.runtime(),validator=lambda *a:proof))
                self.assertFalse(result['applied'])
        with get_connection() as conn:self.assertIsNone(conn.execute('SELECT 1 FROM event_review WHERE event_id=?',(self.ident,)).fetchone())

    def test_atomic_write_human_override_and_replay(self):
        before=self.state()['event_retention']
        with patch.object(writer,'inspect',return_value=self.eligible()):
            result=writer.attempt(self.ident,runtime=self.runtime());self.assertTrue(result['applied'])
            self.assertFalse(writer.attempt(self.ident,runtime=self.runtime())['applied'])
        with get_connection() as conn:
            review=dict(conn.execute('SELECT * FROM event_review WHERE event_id=?',(self.ident,)).fetchone())
        self.assertIsNone(review['reviewer_id'])
        self.assertEqual(review['second_review_needed'],1)
        self.assertIn('synthetic-test-only',review['review_comment'])
        self.assertEqual(before,self.state()['event_retention'])
        review.update(reviewer_id='admin01',review_result='false_positive',confirmed_violation=0,second_review_needed=0,review_reason_code='other',review_comment='human correction')
        update_event_review(review)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT event_status FROM candidate_event WHERE event_id=?',(self.ident,)).fetchone()[0],'false_positive')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_review_history WHERE event_id=?',(self.ident,)).fetchone()[0],1)
        self.assertFalse(writer.attempt(self.ident,runtime=self.runtime())['applied'])
        with self.assertRaisesRegex(ValueError,'Review already exists'):insert_event_review(review)

    def test_insert_failure_rolls_back_status_and_retention(self):
        # SQLite trigger deliberately fails review insertion after status UPDATE.
        from backend.db.connection import is_postgres
        if is_postgres():self.skipTest('SQLite failure-injection trigger')
        with get_connection() as conn:
            conn.execute("CREATE TRIGGER fail_auto BEFORE INSERT ON event_review BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END")
        before=self.state()
        with patch.object(writer,'inspect',return_value=self.eligible()):
            with self.assertRaises(Exception):writer.attempt(self.ident,runtime=self.runtime())
        self.assertEqual(before,self.state())

    def test_concurrent_attempts_create_only_one_review(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        ready=Barrier(2);runtime=self.runtime();diagnostic=self.eligible()
        def run():
            ready.wait(timeout=5)
            return writer.attempt(self.ident,runtime=runtime)
        with patch.object(writer,'inspect',return_value=diagnostic),ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(run) for _ in range(2)]
            results=[f.result(timeout=10) for f in futures]
        self.assertEqual(sum(r['applied'] for r in results),1)
        with get_connection() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_review WHERE event_id=?',(self.ident,)).fetchone()[0],1)

    def test_human_review_is_never_overwritten_even_with_stale_diagnostic(self):
        d=self.eligible()
        with get_connection() as conn:
            conn.execute("INSERT INTO event_review(review_id,event_id,reviewer_id,review_result) VALUES('human-first',?,'admin01','hold')",(self.ident,))
        with patch.object(writer,'inspect',return_value=d):
            self.assertFalse(writer.attempt(self.ident,runtime=self.runtime())['applied'])
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT reviewer_id FROM event_review WHERE event_id=?',(self.ident,)).fetchone()[0],'admin01')
