import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from inference_worker.scenario_collector import collect_scenario
from inference_worker.summarize_scenario import summarize_records

class SummarizeScenarioTests(unittest.TestCase):
    def records(self, missing=False, empty=False):
        person={'class_name':'person','confidence':.9,'bbox':[50,30,150,230]}
        detector=Mock(model_version='model',supported_ppe={'helmet','vest'})
        detector.detect.side_effect=[[] if empty or (missing and i==3) else [person] for i in range(7)]
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'o.jsonl'
            collect_scenario(((i*.5,Mock(shape=(300,300,3))) for i in range(7)),detector,output,
                scenario_id='solo',camera_id='cam',labels={'helmet':'ambiguous','vest':'not_worn'})
            return [json.loads(line) for line in output.read_text().splitlines()]
    def test_collector_to_summary_keeps_diagnostic_only(self):
        result=summarize_records(self.records(),max_sample_gap_seconds=.5)
        self.assertFalse(result['automatic_confirmation_allowed'])
        self.assertEqual(len(result['track_ppe_diagnostics']),2)
        self.assertEqual(result['track_ppe_diagnostics'][1]['decision_metric'],1)
        self.assertIsNone(result['track_ppe_diagnostics'][1]['verified_ppe_confidence'])
    def test_missing_track_becomes_unknown_not_filtered(self):
        result=summarize_records(self.records(missing=True),max_sample_gap_seconds=.5)
        for report in result['track_ppe_diagnostics']:
            self.assertEqual(report['counts']['unknown'],1)
            self.assertIsNone(report['decision_metric'])
    def test_empty_scenario_not_fabricated_event_or_correct_negative(self):
        result=summarize_records(self.records(empty=True),max_sample_gap_seconds=.5)
        self.assertEqual(result['empty_frame_count'],7)
        self.assertEqual(result['track_ppe_diagnostics'],[])
        self.assertNotIn('correct',result)
    def test_incomplete_or_tampered_footer_rejected(self):
        rows=self.records()
        for field,value in [('completed',False),('frame_count',99),('last_frame_time',1),('distinct_track_count',0),('empty_frame_count',1)]:
            modified=copy.deepcopy(rows);modified[-1][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                summarize_records(modified,max_sample_gap_seconds=.5)
        with self.assertRaises(ValueError):summarize_records(rows[:-1],max_sample_gap_seconds=.5)
    def test_time_order_and_duplicate_track_rejected(self):
        rows=self.records()
        for change in ('time','index','track'):
            modified=copy.deepcopy(rows)
            if change=='time':modified[2]['timestamp']=0
            elif change=='index':modified[2]['index']=5
            else:modified[2]['people']*=2
            with self.subTest(change=change),self.assertRaises(ValueError):
                summarize_records(modified,max_sample_gap_seconds=.5)
    def test_labels_not_attached_to_tracks(self):
        result=summarize_records(self.records(),max_sample_gap_seconds=.5)
        self.assertEqual(result['scenario_labels_unverified']['vest'],'not_worn')
        for report in result['track_ppe_diagnostics']:
            self.assertNotIn('label',report)
    def test_gap_not_inferred(self):
        with self.assertRaises(ValueError):summarize_records(self.records(),max_sample_gap_seconds=0)
        reports=summarize_records(self.records(),max_sample_gap_seconds=.1)['track_ppe_diagnostics']
        for report in reports:self.assertIn('sample_gaps',report['blocking_reasons'])

    def test_expansion_limit_rejects_before_synthesis(self):
        from unittest.mock import patch
        rows=self.records()
        with patch('inference_worker.summarize_scenario.MAX_SYNTHETIC_OBSERVATIONS',10):
            with self.assertRaisesRegex(ValueError,'frame-track'):
                summarize_records(rows,max_sample_gap_seconds=.5)
