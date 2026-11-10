import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from backend.media import redaction, retinaface, verification


class FaceVerificationTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(TemporaryDirectory()))
        self.image = np.full((32, 32, 3), 255, dtype=np.uint8)
        self.face = (8., 8., 8., 8., .9)

    def image_file(self):
        target = self.folder / 'output.jpg'
        self.assertTrue(cv2.imwrite(str(target), self.image))
        return target

    def video_file(self):
        target = self.folder / 'output.mp4'
        writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*'mp4v'), 5, (32, 32))
        self.assertTrue(writer.isOpened())
        for _ in range(3):
            writer.write(self.image)
        writer.release()
        return target

    def test_image_repairs_encoded_output_and_checks_it_again(self):
        target = self.image_file()
        detector = Mock()
        detector.detect.side_effect = [[self.face], []]
        result = verification.verify_image(target, detector)
        self.assertEqual(result['repairs'], 1)
        self.assertLess(int(detector.detect.call_args.args[0][12, 12].max()), 30)
        self.assertTrue(target.exists())

    def test_repeated_detection_is_bounded_and_never_leaves_publishable_output(self):
        target = self.image_file()
        detector = Mock()
        detector.detect.return_value = [self.face]
        with self.assertRaisesRegex(redaction.MediaError, 'face_verification_failed'):
            verification.verify_image(target, detector)
        self.assertEqual(detector.detect.call_count, 3)
        self.assertFalse(target.exists())

    def test_detector_failure_removes_output_without_fallback(self):
        target = self.image_file()
        detector = Mock()
        detector.detect.side_effect = redaction.MediaError('face_verification_failed')
        with self.assertRaises(redaction.MediaError):
            verification.verify_image(target, detector)
        self.assertFalse(target.exists())

    def test_video_scans_every_encoded_frame_and_rechecks_after_repair(self):
        target = self.video_file()
        detector = Mock()
        detector.detect.side_effect = [[self.face], [], [], [], [], []]
        result = verification.verify_video(target, detector, self.folder, 3)
        self.assertEqual(result['repairs'], 1)
        self.assertEqual(detector.detect.call_count, 6)
        self.assertTrue(target.exists())
        self.assertEqual(list(self.folder.glob('verification-*')), [])

    def test_truncated_or_unexpected_frame_count_blocks_output_and_cleans_work(self):
        target = self.video_file()
        detector = Mock()
        detector.detect.return_value = []
        with self.assertRaisesRegex(redaction.MediaError, 'incomplete_video_decode'):
            verification.verify_video(target, detector, self.folder, 4)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.folder.glob('verification-*')), [])

    def test_video_detector_error_cleans_partial_intermediate(self):
        target = self.video_file()
        detector = Mock()
        detector.detect.side_effect = redaction.MediaError('face_verification_failed')
        with self.assertRaises(redaction.MediaError):
            verification.verify_video(target, detector, self.folder, 3)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.folder.glob('verification-*')), [])

    def test_explicit_enable_requires_model_and_valid_setting(self):
        with patch.dict(os.environ, {'PPE_MEDIA_FACE_VERIFIER': 'off'}):
            self.assertIsNone(retinaface.configured_detector())
        for setting, message in [('retinaface', 'face_verifier_model_invalid'), ('typo', 'face_verifier_configuration_invalid')]:
            with self.subTest(setting=setting), patch.dict(os.environ, {
                    'PPE_MEDIA_FACE_VERIFIER': setting, 'PPE_RETINAFACE_MODEL_PATH': str(self.folder/'missing.onnx')}):
                with self.assertRaisesRegex(redaction.MediaError, message):
                    retinaface.configured_detector()

    def test_checksum_mismatch_does_not_create_session(self):
        path = self.folder/'model.onnx'
        path.write_bytes(b'synthetic model bytes')
        path.with_suffix('.manifest.json').write_text(json.dumps({'model': retinaface.MODEL, 'sha256': '0'*64}))
        with patch.dict(os.environ, {'PPE_MEDIA_FACE_VERIFIER': 'retinaface', 'PPE_RETINAFACE_MODEL_PATH': str(path)}), \
                patch.object(retinaface, 'RetinaFace') as factory:
            with self.assertRaisesRegex(redaction.MediaError, 'face_verifier_model_invalid'):
                retinaface.configured_detector()
            factory.assert_not_called()

    def test_raw_output_decode_maps_box_from_letterbox_and_rejects_logits(self):
        detector = retinaface.RetinaFace.__new__(retinaface.RetinaFace)
        detector.lock, detector.anchors = Lock(), retinaface.priors()
        detector.input_name, detector.session = 'input', Mock()
        count = len(detector.anchors)
        loc, conf, landmarks = np.zeros((1,count,4), np.float32), np.zeros((1,count,2), np.float32), np.zeros((1,count,10), np.float32)
        conf[:,:,0] = 1
        # Anchor center at (100,100) with side 16 in the 640 input.
        index = (12*80+12)*2
        conf[0,index] = [.1,.9]
        detector.session.run.return_value = [landmarks, loc, conf]
        faces = detector.detect(np.zeros((320,640,3), np.uint8))
        self.assertEqual(len(faces), 1)
        self.assertAlmostEqual(faces[0][0], 92, places=3)
        self.assertAlmostEqual(faces[0][1], 92, places=3)
        conf[0,index] = [-1,2]
        with self.assertRaisesRegex(redaction.MediaError, 'face_verification_failed'):
            detector.detect(self.image)

    def test_primary_detection_unions_secondary_faces(self):
        primary = redaction.FaceRedactor.__new__(redaction.FaceRedactor)
        primary.detector, primary.verifier = Mock(), Mock()
        primary.detector.detect.return_value = (1, None)
        primary.verifier.detect.return_value = [self.face]
        primary.metrics, primary.detection_passes = {'detection_seconds': 0}, 0
        self.assertEqual(primary.detect_faces(self.image, rotated=False), [self.face])

    def test_image_entry_point_runs_final_verification_before_return(self):
        source, target = self.image_file(), self.folder/'result.jpg'
        detector = Mock()
        detector.redact.return_value = (self.image, 0)
        with patch.object(redaction, 'FaceRedactor', return_value=detector), \
                patch.object(verification, 'verify_image', side_effect=redaction.MediaError('face_verification_failed')), \
                patch.object(redaction, '_details') as details:
            with self.assertRaises(redaction.MediaError):
                redaction.redact_image(source, target)
            details.assert_not_called()


if __name__ == '__main__':
    unittest.main()
