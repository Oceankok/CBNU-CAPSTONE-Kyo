import unittest
from inference_worker.ppe_absence_evidence import summarize_absence
from inference_worker.ppe_rules import assess_ppe

class AbsenceEvidenceTests(unittest.TestCase):
    def rows(self,status='suspected_no_vest'):
        return [dict(camera_id='cam',session_id='session',model_version='model',tracking_id='t',ppe_type='vest',timestamp=i*.5,status=status) for i in range(7)]
    def run_episode(self,**updates):
        return summarize_absence(**{**dict(camera_id='cam',session_id='session',model_version='model',tracking_id='t',ppe_type='vest',start_time=0,end_time=3,observations=self.rows(),max_sample_gap_seconds=.5),**updates})
    def test_absence_is_metric_not_verified_model_confidence(self):
        r=self.run_episode()
        self.assertEqual(r['decision_metric'],1)
        self.assertEqual(r['absence_pair_seconds'],3)
        self.assertIsNone(r['verified_ppe_confidence'])
        self.assertFalse(r['automatic_confirmation_allowed'])
    def test_normal_clothing_and_mixed_are_measured(self):
        self.assertEqual(self.run_episode(observations=self.rows('vest'))['decision_metric'],0)
        rows=self.rows(); rows[:3]=self.rows('vest')[:3]
        self.assertAlmostEqual(self.run_episode(observations=rows)['decision_metric'],4/7)
    def test_unknown_not_discarded_to_make_metric_one(self):
        rows=self.rows();rows[3]['status']='unknown'
        r=self.run_episode(observations=rows)
        self.assertEqual(r['absence_sample_ratio'],1)
        self.assertIsNone(r['decision_metric'])
        self.assertIn('unknown_observations',r['blocking_reasons'])
    def test_gaps_edges_and_short_episode_are_blocked(self):
        for rows in (self.rows()[1:],self.rows()[:-1],self.rows()[::2]):
            self.assertIsNone(self.run_episode(observations=rows)['decision_metric'])
        self.assertIsNone(self.run_episode(end_time=1,observations=self.rows()[:3])['decision_metric'])
    def test_identity_ppe_order_and_invalid_numbers(self):
        for changes in ({'camera_id':'other'},{'session_id':'reset'},{'model_version':'changed'},{'tracking_id':'another'},{'ppe_type':'helmet'},{'timestamp':0},{'timestamp':float('nan')},{'timestamp':10**1000}):
            rows=self.rows();rows[1].update(changes)
            with self.assertRaises(ValueError):self.run_episode(observations=rows)
        for v in (0,True,float('nan'),10**1000):
            with self.assertRaises(ValueError):self.run_episode(max_sample_gap_seconds=v)
    def test_existing_assessment_has_no_negative_confidence(self):
        person={'class_name':'person','confidence':.95,'bbox':[50,30,150,230]}
        row=assess_ppe([person],(300,300,3))[0]
        self.assertEqual(row['ppe']['vest']['status'],'suspected_no_vest')
        self.assertIsNone(row['ppe']['vest']['verified_ppe_confidence'])
        rows=self.rows(row['ppe']['vest']['status'])
        self.assertEqual(self.run_episode(observations=rows)['decision_metric'],1)
    def test_unsupported_ppe_is_rejected(self):
        with self.assertRaises(ValueError):self.run_episode(ppe_type='mask')
