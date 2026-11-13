import json,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from inference_worker.run_continuous import read_config,policy_assessments,run

class RunnerBoundaryTests(TestCase):
    def test_opt_in_flag_dispatches_before_legacy_model_or_client(self):
        from inference_worker.run_server import main
        with patch('sys.argv',['run','--camera-id','main','--continuous-config','explicit.json','--submit-review-candidates']), \
             patch('inference_worker.run_continuous.run',return_value='selected') as selected, \
             patch('inference_worker.run_server.create_detector') as detector:
            self.assertEqual(main(),'selected');selected.assert_called_once();detector.assert_not_called()
    def test_missing_submission_and_replay_rejected_before_model(self):
        for args in (SimpleNamespace(submit_events=False,submit_review_candidates=False,replay_buffer=False),
                     SimpleNamespace(submit_events=False,submit_review_candidates=True,replay_buffer=True)):
            with self.assertRaises(ValueError):run(args)
    def test_stale_clock_config_rejected(self):
        cfg=dict(state_directory='/tmp/private',clocks={c:dict(offset=0,error_bound=0,verified=True,
            epoch='e',valid_until='2026-01-01T00:00:00Z') for c in ('main','ref')},
            capture={},mux={},outbox={},delivery={},transport={},lifecycle={},source={},decoder={},
            metadata_steps_per_pump=1,max_lag_seconds=1)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json';path.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError,'calibration'):read_config(path,'main',now=9999999999)
    def test_invalid_person_geometry_cannot_create_violation(self):
        rows=[{'tracking_id':'t','person_bbox':None,'ppe':{'helmet':{'status':'suspected_no_helmet'}}}]
        result=policy_assessments(rows,{},(20,20,3),review_only=True)
        self.assertEqual(result[0]['ppe']['helmet']['status'],'unknown')
