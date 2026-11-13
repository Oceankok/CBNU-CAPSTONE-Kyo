import unittest
from backend.auto_decision.evidence import coverage
from backend.tests import test_continuous_events as fixtures
from backend.db.event_repository import get_connection


class CoverageTests(unittest.TestCase):
    def test_union_clips_preroll_postroll_and_overlap(self):
        result=coverage('2026-01-01T00:00:00Z','2026-01-01T00:00:10Z',[
            ('2025-12-31T23:59:58Z','2026-01-01T00:00:06Z'),
            ('2026-01-01T00:00:04Z','2026-01-01T00:00:12Z')])
        self.assertEqual(result['covered_seconds'],10)
        self.assertTrue(result['complete'])

    def test_gaps_and_timezone_equivalence(self):
        result=coverage('2026-01-01T09:00:00+09:00','2026-01-01T09:00:10+09:00',[
            ('2026-01-01T00:00:02Z','2026-01-01T00:00:04Z'),
            ('2026-01-01T00:00:06Z','2026-01-01T00:00:08Z')])
        self.assertEqual(result['covered_seconds'],4)
        self.assertEqual(len(result['uncovered_ranges']),3)
        self.assertFalse(result['complete'])

    def test_invalid_intervals_fail_not_silently_covered(self):
        for intervals in ([('2026-01-01T00:00:05Z','2026-01-01T00:00:04Z')],
                          [('2026-01-01T00:00:00','2026-01-01T00:00:01Z')]):
            with self.assertRaises(ValueError):
                coverage('2026-01-01T00:00:00Z','2026-01-01T00:00:10Z',intervals)


class WholeEvidenceTests(unittest.TestCase):
    setUp=fixtures.ContinuousEventTests.setUp
    ts=fixtures.ContinuousEventTests.ts
    start=fixtures.ContinuousEventTests.start
    progress=fixtures.ContinuousEventTests.progress
    segment=fixtures.ContinuousEventTests.segment

    def diagnostic(self):
        r=self.client.post('/api/auto-decisions/preview',headers=self.admin,json={
            'event_ids':[self.ident], 'threshold':0.9, 'model_versions':['test'],
            'ppe_types':['helmet'],'policy_label':'experiment'})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()['items'][0]

    def test_full_event_summary_does_not_turn_manifest_into_decision(self):
        self.start().raise_for_status()
        self.progress().raise_for_status()
        self.segment(end=12).raise_for_status()
        self.progress(sequence=2,kind='close',close_reason='ppe_present',complete=True,final_segment_count=1).raise_for_status()
        item=self.diagnostic(); summary=item['whole_event_evidence']
        self.assertEqual(summary['phase'],'closed')
        self.assertEqual(summary['observed_seconds'],10)
        self.assertEqual(len(summary['cameras']),2)
        self.assertTrue(all(c['manifest_coverage']['complete'] for c in summary['cameras']))
        self.assertFalse(item['threshold_candidate']) # No files, no classifier confidence.
        self.assertFalse(item['automatic_confirmation_allowed'])
        self.assertNotIn('storage_path',str(summary))
        self.assertNotIn(self.session,str(summary))

    def test_gap_change_is_fingerprinted_and_shadow_preserved(self):
        self.start().raise_for_status()
        first=self.diagnostic()
        with get_connection() as conn:
            conn.execute('UPDATE processing_event_lifecycle SET observed_seconds=2,gap_seconds=1,gap_count=1 WHERE event_id=?',(self.ident,))
        second=self.diagnostic()
        self.assertNotEqual(first['evidence_fingerprint'],second['evidence_fingerprint'])
        self.assertEqual(second['whole_event_evidence']['gap_seconds'],1)
        body={'event_ids':[self.ident],'threshold':0.9,'model_versions':['test'],
              'ppe_types':['helmet'],'policy_label':'experiment','request_key':'whole-evidence'}
        stored=self.client.post('/api/auto-decisions/shadow',headers=self.admin,json=body)
        self.assertEqual(stored.status_code,201,stored.text)
        self.assertEqual(stored.json()['items'][0]['whole_event_evidence'],second['whole_event_evidence'])

    def test_invalid_observation_not_clamped_and_reference_missing_visible(self):
        self.start().raise_for_status()
        with get_connection() as conn:
            conn.execute('UPDATE processing_event_lifecycle SET observed_seconds=100 WHERE event_id=?',(self.ident,))
        summary=self.diagnostic()['whole_event_evidence']
        self.assertFalse(summary['observation_metadata_valid'])
        self.assertIsNone(summary['observed_seconds'])
        self.assertTrue(all(not c['manifest_coverage']['complete'] for c in summary['cameras']))

    def test_unaccounted_span_is_visible_not_assumed_observed(self):
        self.start().raise_for_status()
        self.progress().raise_for_status()
        with get_connection() as conn:
            conn.execute('UPDATE processing_event_lifecycle SET observed_seconds=3,gap_seconds=0,gap_count=0 WHERE event_id=?',(self.ident,))
        summary=self.diagnostic()['whole_event_evidence']
        self.assertTrue(summary['observation_metadata_valid'])
        self.assertEqual(summary['event_span_seconds'],10)
        self.assertEqual(summary['unaccounted_seconds'],7)
