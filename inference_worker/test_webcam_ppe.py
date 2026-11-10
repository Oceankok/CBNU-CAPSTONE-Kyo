"""Exercise the actual webcam entrypoint without a camera or model runtime."""
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from src import realtime_helmet_monitor as monitor


class WebcamTests(unittest.TestCase):
    def test_both_absences_display_and_submit_only_when_enabled(self):
        for submit in (False, True):
            with self.subTest(submit=submit):
                frame = np.zeros((300, 400, 3), dtype=np.uint8)
                capture = Mock()
                capture.isOpened.return_value = True
                capture.read.side_effect = [(True, frame.copy()) for _ in range(14)] + [(False, None)]
                detector = SimpleNamespace(
                    supported_ppe={'helmet', 'vest'},
                    model=SimpleNamespace(names={0: 'helmet', 1: 'vest', 2: 'person'}),
                    detect=lambda frame: [{'class_name': 'person', 'confidence': .9,
                                           'bbox': [30, 20, 130, 220]}],
                )
                module = SimpleNamespace(PPEDetector=Mock(return_value=detector), DEFAULT_MODEL=Path('best.pt'))
                args = ['realtime_helmet_monitor'] + (['--submit-review-candidates'] if submit else [])
                output = io.StringIO()
                with (patch.dict(sys.modules, {'inference_worker.ppe_detector': module}),
                      patch.object(sys, 'argv', args),
                      patch.object(monitor.cv2, 'VideoCapture', return_value=capture),
                      patch.object(monitor.cv2, 'imshow') as display,
                      patch.object(monitor.cv2, 'waitKey', return_value=-1),
                      patch.object(monitor.cv2, 'destroyAllWindows') as close,
                      patch.object(monitor.time, 'monotonic', side_effect=[i*.25 for i in range(14)]),
                      patch.object(monitor, 'save_candidate') as save,
                      redirect_stdout(output)):
                    monitor.main()
                self.assertEqual(save.call_count, 2 if submit else 0)
                self.assertEqual(output.getvalue().count('[SUSPECTED]'), 2)
                self.assertEqual(display.call_count, 14)
                # At the threshold, the person rectangle becomes red.
                self.assertEqual(display.call_args.args[1][20, 30].tolist(), [0, 0, 255])
                capture.release.assert_called_once()
                close.assert_called_once()
                if submit:
                    self.assertEqual({c.args[2]['ppe_type'] for c in save.call_args_list}, {'helmet', 'vest'})


if __name__ == '__main__':
    unittest.main()
