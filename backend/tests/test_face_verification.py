import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from backend.media import redaction, retinaface, verification, verification_policy


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
        self.image = np.repeat(((np.indices((32, 32)).sum(axis=0) % 2) * 255)
                               .astype(np.uint8)[:, :, None], 3, axis=2)
        target = self.image_file()
        detector = Mock()
        detector.detect.side_effect = [[self.face], []]
        result = verification.verify_image(target, detector)
        self.assertEqual(result['repairs'], 1)
        self.assertTrue(80 < int(detector.detect.call_args.args[0][12, 12].max()) < 180)
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

    def test_video_repair_covers_known_faces_even_on_previously_clear_frames(self):
        self.image = np.repeat(((np.indices((32, 32)).sum(axis=0) % 2) * 255)
                               .astype(np.uint8)[:, :, None], 3, axis=2)
        target = self.video_file()
        faces = [(8., 8., 8., 8., .9), (9., 8., 8., 8., .9), (10., 8., 8., 8., .9)]
        seen = []

        def detect(frame):
            index = len(seen)
            seen.append(frame.copy())
            if index < 3:
                return [faces[0]] if index == 0 else []
            x, y, w, h, _ = faces[index % 3]
            # A residual on any frame still blocks acceptance. Each tracked
            # face must already be coarsely pixelated on the first repair.
            value = frame[int(y+h/2), int(x+w/2)].max()
            return [] if 80 < value < 180 else [faces[index % 3]]

        result = verification.verify_video(target, Mock(detect=detect), self.folder, 3,
                                            repair_masks=[(face,) for face in faces])
        self.assertEqual((result['repairs'], len(seen)), (1, 6))
        self.assertTrue(target.exists())

    def test_new_uncovered_residuals_are_not_exempted_by_known_masks(self):
        target = self.video_file()
        detector = Mock()
        detector.detect.side_effect = [[(0., 0., 4., 4., .9)]] * 3 + \
            [[(24., 24., 4., 4., .9)]] * 3 + [[(24., 0., 4., 4., .9)]] * 3
        with self.assertRaisesRegex(redaction.MediaError, 'face_verification_failed'):
            verification.verify_video(target, detector, self.folder, 3,
                                      repair_masks=[(self.face,)] * 3)
        self.assertEqual(detector.detect.call_count, 9)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.folder.glob('verification-*')), [])

    def test_verification_record_accepts_audited_policy_and_legacy_reports(self):
        legacy = dict(model='retinaface-resnet50', status='passed', passes=1,
                      repairs=0, frames=3, sha256='0'*64)
        verification_policy.VerificationReport.model_validate(legacy)
        verification_policy.VerificationReport.model_validate(
            dict(legacy, passes=2, repairs=1, residual_threshold=.8))
        for fields in ({'masked_detections': -1}, {'residual_threshold': 1.1},
                       {'residual_threshold': .49}):
            with self.assertRaises(ValueError):
                verification_policy.VerificationReport.model_validate(dict(legacy, **fields))

    def test_known_masks_do_not_change_a_video_that_passes_initial_check(self):
        target = self.video_file()
        original = target.read_bytes()
        detector = Mock()
        detector.detect.return_value = []
        result = verification.verify_video(target, detector, self.folder, 3,
                                            repair_masks=[(self.face,)] * 3)
        self.assertEqual(result['repairs'], 0)
        self.assertEqual(target.read_bytes(), original)

    def test_misaligned_mask_sequence_fails_closed(self):
        target = self.video_file()
        detector = Mock()
        with self.assertRaisesRegex(redaction.MediaError, 'incomplete_video_decode'):
            verification.verify_video(target, detector, self.folder, 3,
                                      repair_masks=[(self.face,)] * 2)
        detector.detect.assert_not_called()
        self.assertFalse(target.exists())

    def test_render_records_independent_tracked_masks_in_frame_order(self):
        detector = redaction.FaceRedactor.__new__(redaction.FaceRedactor)
        detector.metrics = {'pixelation_seconds': 0}
        detector._verification_masks = []
        moving_face = list(self.face)
        detector.render(self.image, [moving_face])
        moving_face[0] = 20
        detector.render(self.image, [])
        self.assertEqual(detector._verification_masks, [(self.face,), ()])

    def test_repair_preserves_headgear_pixels_outside_face_margin(self):
        frame = np.repeat(((np.indices((64, 64)).sum(axis=0) % 2) * 255)
                          .astype(np.uint8)[:, :, None], 3, axis=2)
        frame[:12] = (20, 180, 240)  # Synthetic helmet-colour band above face.
        repaired = verification.cover(frame, [(20., 28., 16., 16., .9)])
        np.testing.assert_array_equal(repaired[:12], frame[:12])
        self.assertGreater(int(repaired[32:40, 24:32].mean()), 50)
        self.assertLess(float(repaired[32:40, 24:32].std()), float(frame[32:40, 24:32].std()))

    def test_repair_retains_upper_lower_colours_instead_of_opaque_fill(self):
        frame = np.full((64, 64, 3), (30, 50, 70), dtype=np.uint8)
        frame[32:] = (120, 170, 210)
        repaired = verification.cover(frame, [(20., 24., 16., 16., .9)])
        np.testing.assert_array_equal(repaired[24, 28], frame[24, 28])
        np.testing.assert_array_equal(repaired[40, 28], frame[40, 28])
        self.assertFalse(np.array_equal(repaired[24, 28], repaired[40, 28]))

    def test_overlapping_repairs_are_rendered_as_one_region(self):
        faces = [(8., 8., 8., 8., .9), (10., 8., 8., 8., .9)]
        self.assertEqual(len(verification.repair_regions(self.image, faces)), 1)
        faces.append((26., 26., 2., 2., .9))
        self.assertEqual(len(verification.repair_regions(self.image, faces)), 2)

    def test_video_repair_backfills_frames_before_late_detection(self):
        self.image = np.repeat(((np.indices((32, 32)).sum(axis=0) % 2) * 255)
                               .astype(np.uint8)[:, :, None], 3, axis=2)
        source, target = self.video_file(), self.folder/'repair.avi'
        regions = verification.write_video_repair(source, target, 5, (32, 32),
                                                   [[], [], [self.face]], None, [()] * 3)
        capture = cv2.VideoCapture(str(target))
        try:
            ok, first = capture.read()
            self.assertTrue(ok)
            self.assertTrue(80 < int(first[12, 12].max()) < 180)
            self.assertEqual(len(regions), 3)
            self.assertTrue(regions[0])
        finally:
            capture.release()

    def test_residual_threshold_is_separate_from_primary_discovery(self):
        target = self.image_file()
        detector = Mock()
        detector.detect.return_value = [(*self.face[:4], .79)]
        result = verification.verify_image(target, detector)
        self.assertEqual(result['repairs'], 0)
        primary = redaction.FaceRedactor.__new__(redaction.FaceRedactor)
        primary.detector, primary.verifier = Mock(), detector
        primary.detector.detect.return_value = (1, None)
        primary.metrics, primary.detection_passes = {'detection_seconds': 0}, 0
        self.assertEqual(primary.detect_faces(self.image, rotated=False), [(*self.face[:4], .79)])

    def test_invalid_low_confidence_residual_is_not_silently_ignored(self):
        target = self.image_file()
        detector = Mock()
        detector.detect.return_value = [(8., 8., -1., 8., .1)]
        with self.assertRaisesRegex(redaction.MediaError, 'face_verification_failed'):
            verification.verify_image(target, detector)
        self.assertFalse(target.exists())

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
