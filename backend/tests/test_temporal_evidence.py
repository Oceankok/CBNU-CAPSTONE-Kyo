from datetime import datetime,timedelta,timezone
import unittest
from backend.auto_decision.temporal_evidence import summarize_episode
from inference_worker.event_lifecycle import EventLifecycle

class TemporalEvidenceTests(unittest.TestCase):
    def ts(self,n):return (datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(seconds=n)).isoformat()
    def samples(self):return [dict(timestamp=self.ts(i/2),status='violation',verified_ppe_confidence=.9) for i in range(7)]
    def run_episode(self,**updates):
        return summarize_episode(**{**dict(event_id='evt',model_version='fixture',ppe_type='helmet',start_at=self.ts(0),end_at=self.ts(3),samples=self.samples(),max_sample_gap_seconds=.5),**updates})
    def test_matches_producer_minimum_for_complete_verified_episode(self):
        samples=self.samples();samples[-1]['verified_ppe_confidence']=.6
        result=self.run_episode(samples=samples)
        producer=EventLifecycle('CAM',missing_grace_seconds=1,max_sample_gap_seconds=.5)
        transitions=[]
        for i,sample in enumerate(samples):
            transitions+=producer.update([{'tracking_id':'t','ppe_type':'helmet','status':'suspected_no_helmet','verified_ppe_confidence':sample['verified_ppe_confidence']}],i/2)
        self.assertEqual(result['experimental_score'],transitions[-1]['verified_ppe_confidence'])
        self.assertEqual(result['verified_pair_coverage_seconds'],3)
        self.assertFalse(result['automatic_confirmation_allowed'])
    def test_present_unknown_unverified_never_dropped(self):
        for status,score in [('present',None),('unknown',None),('violation',None)]:
            samples=self.samples();samples[3].update(status=status,verified_ppe_confidence=score)
            result=self.run_episode(samples=samples)
            self.assertIsNone(result['experimental_score'])
            self.assertLess(result['verified_pair_coverage_seconds'],3)
            self.assertTrue(result['blocking_reasons'])
    def test_missing_edges_and_large_gap_exclude(self):
        for samples in (self.samples()[1:],self.samples()[:-1],self.samples()[::2]):
            result=self.run_episode(samples=samples)
            self.assertIsNone(result['experimental_score'])
            self.assertTrue(result['excluded_ranges'])
    def test_unsupported_ppe_does_not_gain_verified_status(self):
        result=self.run_episode(ppe_type='vest')
        self.assertIsNone(result['experimental_score'])
        self.assertIn('producer_verified_ppe_not_supported',result['blocking_reasons'])
    def test_gap_threshold_required_and_bad_values_rejected(self):
        for value in (0,-1,True,float('nan'),float('inf'),'0.5',10**1000):
            with self.assertRaises(ValueError):self.run_episode(max_sample_gap_seconds=value)
        with self.assertRaises(TypeError):summarize_episode()
    def test_duplicate_time_bad_score_and_nonviolation_score_rejected(self):
        for mutation in ({'timestamp':self.ts(0)}, {'verified_ppe_confidence':True},
                         {'verified_ppe_confidence':float('nan')},{'verified_ppe_confidence':10**1000},{'verified_ppe_confidence':.2},
                         {'status':'unknown','verified_ppe_confidence':.9}):
            samples=self.samples();samples[1].update(mutation)
            with self.assertRaises(ValueError):self.run_episode(samples=samples)
    def test_single_sample_is_not_whole_event(self):
        result=self.run_episode(samples=self.samples()[:1])
        self.assertIsNone(result['experimental_score'])
        self.assertEqual(result['verified_pair_coverage_seconds'],0)
    def test_late_weak_sample_changes_full_event_score(self):
        samples=self.samples();samples[-1]['verified_ppe_confidence']=.5
        self.assertEqual(self.run_episode(samples=samples)['experimental_score'],.5)

    def test_short_episode_is_not_candidate_even_with_complete_samples(self):
        result=self.run_episode(end_at=self.ts(1),samples=self.samples()[:3])
        self.assertIsNone(result['experimental_score'])
        self.assertIn('below_existing_three_second_event_gate',result['blocking_reasons'])
