import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from inference_worker.scenario_collector import collect_scenario

PERSON = {'class_name': 'person', 'confidence': .9, 'bbox': [50,30,150,230]}
VEST = {'class_name': 'vest', 'confidence': .9, 'bbox': [65,85,130,150]}

class ScenarioCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'observations.jsonl'
        self.detector = Mock(model_version='ppe-known', supported_ppe={'helmet','vest'})
        self.frame = Mock(shape=(300,300,3))
    def run_scenario(self, detections, **kwargs):
        self.detector.detect.side_effect = detections
        return collect_scenario(((i*.5,self.frame) for i in range(len(detections))), self.detector,
            self.path, scenario_id='solo01', camera_id='camera01',
            labels={'helmet':'ambiguous','vest':'worn'}, **kwargs)
    def records(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]
    def test_normal_absent_unknown_and_empty_all_retained(self):
        self.run_scenario([[PERSON,VEST], [PERSON], [{**PERSON,'bbox':[0,0,150,230]}], []])
        rows = self.records()
        self.assertEqual(len(rows),6)
        frames=rows[1:-1]
        self.assertEqual([r['index'] for r in frames],list(range(4)))
        self.assertEqual([r['people'][0]['ppe']['vest'] for r in frames[:3]],
                         ['vest','suspected_no_vest','unknown'])
        self.assertEqual(frames[3]['people'],[])
        self.assertTrue(rows[-1]['completed'])
    def test_no_media_geometry_confidence_or_path_saved(self):
        self.run_scenario([[PERSON],[PERSON]])
        text=self.path.read_text()
        for forbidden in ('bbox','person_confidence','0.9',str(self.path),'image_data'):
            self.assertNotIn(forbidden,text)
        self.assertFalse(self.records()[-1]['automatic_confirmation_allowed'])
    def test_never_overwrite_existing_output(self):
        self.path.write_text('keep')
        with self.assertRaises(FileExistsError): self.run_scenario([[],[]])
        self.assertEqual(self.path.read_text(),'keep')
    def test_detector_failure_preserves_incomplete_footer_without_error_text(self):
        with self.assertRaises(RuntimeError): self.run_scenario([[],RuntimeError('secret-path')])
        self.assertFalse(self.records()[-1]['completed'])
        self.assertNotIn('secret-path',self.path.read_text())
    def test_limit_marks_incomplete(self):
        with self.assertRaises(ValueError):self.run_scenario([[],[],[]],max_frames=2)
        self.assertFalse(self.records()[-1]['completed'])
        self.assertEqual(self.records()[-1]['frame_count'],2)
    def test_invalid_labels_identifiers_fail_before_output(self):
        with self.assertRaises(ValueError):
            collect_scenario([],self.detector,self.path,scenario_id='../bad',camera_id='cam',
                             labels={'helmet':'worn','vest':'worn'})
        self.assertFalse(self.path.exists())
    def test_time_must_start_zero_and_increase(self):
        for times in ([1,2],[0,0],[0,float('nan')]):
            with self.subTest(times=times):
                if self.path.exists():self.path.unlink()
                self.detector.detect.side_effect=[[],[]]
                with self.assertRaises(ValueError):
                    collect_scenario([(t,self.frame) for t in times],self.detector,self.path,
                        scenario_id='a',camera_id='b',labels={'helmet':'worn','vest':'worn'})
                self.assertFalse(self.records()[-1]['completed'])
    def test_no_people_is_valid_scenario_not_fake_event(self):
        self.run_scenario([[],[],[]])
        self.assertEqual(self.records()[-1]['empty_frame_count'],3)
        self.assertEqual(self.records()[-1]['distinct_track_count'],0)
        self.assertNotIn('event_id',self.path.read_text())

class ScenarioCLITests(unittest.TestCase):
    def test_decoder_failure_cannot_report_complete_and_capture_released(self):
        from unittest.mock import patch
        import types
        from inference_worker.collect_scenario import main
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); video=root/'clip.mp4'; model=root/'best.pt'; out=root/'o.jsonl'
            video.write_bytes(b'test');model.write_bytes(b'test')
            frame=Mock(shape=(300,300,3))
            cap=Mock();cap.isOpened.return_value=True
            cap.get.side_effect=lambda p: 2 if p==1 else 4
            cap.read.side_effect=[(True,frame),(True,frame),(False,None)]
            cv=types.SimpleNamespace(VideoCapture=Mock(return_value=cap),CAP_PROP_FPS=1,CAP_PROP_FRAME_COUNT=2)
            detector=Mock(model_version='model',supported_ppe={'helmet','vest'})
            detector.detect.return_value=[]
            dm=types.SimpleNamespace(PPEDetector=Mock(return_value=detector))
            argv=['collect','--video',str(video),'--model',str(model),'--output',str(out),
                '--scenario-id','solo','--camera-id','cam','--helmet-label','worn',
                '--vest-label','worn','--assume-constant-fps']
            with patch('sys.argv',argv), patch.dict('sys.modules',{'cv2':cv,'inference_worker.ppe_detector':dm}):
                with self.assertRaisesRegex(ValueError,'frame count'):
                    main()
            cap.release.assert_called_once()
            self.assertFalse(json.loads(out.read_text().splitlines()[-1])['completed'])
    def test_complete_local_video_success(self):
        from unittest.mock import patch
        import types
        from inference_worker.collect_scenario import main
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);video=root/'clip.mp4';model=root/'best.pt';out=root/'o.jsonl'
            video.write_bytes(b'test');model.write_bytes(b'test')
            frame=Mock(shape=(300,300,3));cap=Mock();cap.isOpened.return_value=True
            cap.get.return_value=2;cap.read.side_effect=[(True,frame),(True,frame),(False,None)]
            cv=types.SimpleNamespace(VideoCapture=Mock(return_value=cap),CAP_PROP_FPS=1,CAP_PROP_FRAME_COUNT=2)
            detector=Mock(model_version='model',supported_ppe={'helmet','vest'});detector.detect.return_value=[]
            argv=['collect','--video',str(video),'--model',str(model),'--output',str(out),
                '--scenario-id','solo','--camera-id','cam','--helmet-label','worn','--vest-label','worn','--assume-constant-fps']
            with patch('sys.argv',argv), patch.dict('sys.modules',{'cv2':cv,'inference_worker.ppe_detector':types.SimpleNamespace(PPEDetector=Mock(return_value=detector))}):
                main()
            cap.release.assert_called_once()
            self.assertTrue(json.loads(out.read_text().splitlines()[-1])['completed'])
