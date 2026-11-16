import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
import unittest
from unittest.mock import Mock, patch

import cv2
import httpx
import numpy as np
from pydantic import ValidationError

from backend.media import redaction, retinaface, verification, verification_policy
from backend.media.verification_diagnostics import FailureDiagnostic, VerificationFailure
from backend.processing.routes import Failure
from backend.tests import test_processing as fixture
from backend.db.event_repository import get_connection


class DiagnosticTests(unittest.TestCase):
    def test_contract_rejects_unknown_reason_fields_and_success(self):
        for fields in ({'reason': 'raw exception'}, {'path': 'private'}, {'status': 'passed'},
                       {'frames_checked': -1}, {'passes': 4}, {'frames_checked': '1'}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                FailureDiagnostic.model_validate({'reason': 'detector_runtime_error', **fields})
        with self.assertRaises(ValidationError):
            Failure(error_code='media_processing_failed', verification_failure={
                'reason': 'detector_runtime_error'})
        Failure(error_code='face_verification_failed')  # Old worker contract remains valid.

    def test_diagnostic_never_qualifies_as_passed_verification(self):
        value = FailureDiagnostic(reason='detector_runtime_error').model_dump()
        with self.assertRaisesRegex(redaction.MediaError, 'face_verification_failed'):
            verification_policy.record(value, 1, '0'*64)

    def test_annotation_does_not_replace_privacy_failure(self):
        error = VerificationFailure('detector_runtime_error')
        error.annotate(frames_checked=100001, expected_frames=100002)
        self.assertEqual(error.verification_failure['frames_checked'], 100000)
        error.annotate(stage='unknown', passes=999)
        self.assertEqual(str(error), 'face_verification_failed')
        self.assertEqual(error.verification_failure['stage'], 'detector')

    def test_detector_classifies_runtime_output_and_probability_without_raw_error(self):
        detector = retinaface.RetinaFace.__new__(retinaface.RetinaFace)
        detector.lock, detector.anchors = Lock(), retinaface.priors()
        detector.input_name, detector.session = 'input', Mock()
        count = len(detector.anchors)
        for reason in ('detector_runtime_error', 'detector_output_invalid', 'detector_probability_invalid'):
            with self.subTest(reason=reason):
                detector.session.run.side_effect = None
                if reason == 'detector_runtime_error':
                    detector.session.run.side_effect = RuntimeError('SECRET path/token')
                elif reason == 'detector_output_invalid':
                    detector.session.run.return_value = []
                else:
                    detector.session.run.return_value = [np.zeros((1,count,4), np.float32),
                        np.full((1,count,2), 2, np.float32), np.zeros((1,count,10), np.float32)]
                with self.assertRaises(VerificationFailure) as caught:
                    detector.detect(np.zeros((32,32,3), np.uint8))
                self.assertEqual(caught.exception.verification_failure['reason'], reason)
                self.assertNotIn('SECRET', json.dumps(caught.exception.verification_failure))
                self.assertEqual(str(caught.exception), 'face_verification_failed')

    def test_primary_detection_sets_stage(self):
        primary = redaction.FaceRedactor.__new__(redaction.FaceRedactor)
        primary.detector, primary.verifier = Mock(), Mock()
        primary.detector.detect.return_value = (1, None)
        primary.verifier.detect.side_effect = VerificationFailure('detector_runtime_error')
        primary.metrics, primary.detection_passes = {'detection_seconds': 0}, 0
        with self.assertRaises(VerificationFailure) as caught:
            primary.detect_faces(np.zeros((32,32,3), np.uint8), rotated=False)
        self.assertEqual(caught.exception.verification_failure['stage'], 'primary_detection')

    def test_residual_and_invalid_box_delete_encoded_output(self):
        with TemporaryDirectory() as folder:
            for face, reason, passes in [((8.,8.,8.,8.,.9), 'residual_detection_limit', 3),
                                         ((8.,8.,-1.,8.,.9), 'invalid_repair_box', 1)]:
                with self.subTest(reason=reason):
                    target = Path(folder)/'output.jpg'
                    self.assertTrue(cv2.imwrite(str(target), np.zeros((32,32,3), np.uint8)))
                    detector = Mock()
                    detector.detect.return_value = [face]
                    with self.assertRaises(VerificationFailure) as caught:
                        verification.verify_image(target, detector)
                    diagnostic = caught.exception.verification_failure
                    self.assertEqual((diagnostic['reason'], diagnostic['passes']), (reason, passes))
                    self.assertEqual(diagnostic['stage'], 'encoded_verification')
                    self.assertEqual(diagnostic['frames_checked'], 1)
                    self.assertFalse(target.exists())

    def test_video_excess_frames_delete_output_and_intermediates(self):
        with TemporaryDirectory() as folder:
            target = Path(folder)/'output.mp4'
            target.write_bytes(b'synthetic')
            capture, writer, detector = Mock(), Mock(), Mock()
            capture.isOpened.return_value = writer.isOpened.return_value = True
            capture.get.return_value = 5
            capture.read.side_effect = [(True, np.zeros((32,32,3), np.uint8))]*2
            detector.detect.return_value = []
            with patch.object(verification.cv2, 'VideoCapture', return_value=capture), \
                 patch.object(verification.cv2, 'VideoWriter', return_value=writer):
                with self.assertRaises(VerificationFailure) as caught:
                    verification.verify_video(target, detector, folder, 1)
            self.assertEqual(caught.exception.verification_failure['reason'], 'frame_count_exceeded')
            self.assertEqual(caught.exception.verification_failure['frames_checked'], 2)
            capture.release.assert_called_once()
            writer.release.assert_called_once()
            self.assertEqual(list(Path(folder).iterdir()), [])


class DiagnosticApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ProcessingTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.client, self.admin, self.node = self.fixture.client, self.fixture.admin, self.fixture.node

    def test_failure_persists_only_allowlisted_metadata_without_publication(self):
        media = self.fixture.upload()
        job, headers = self.fixture.claim()
        url = '/api/processing/jobs/'+job['job_id']+'/failure'
        proof = VerificationFailure('detector_runtime_error').annotate(
            stage='encoded_verification', passes=1, frames_checked=2, expected_frames=3).verification_failure
        payload = {'error_code': 'face_verification_failed', 'verification_failure': proof}
        self.assertEqual(self.client.post(url, headers={**headers, 'X-Processing-Lease':'bad'}, json=payload).status_code, 403)
        self.assertEqual(self.client.post(url, headers=headers, json={**payload,
            'verification_failure': {**proof, 'raw_error':'SECRET'}}).status_code, 422)
        response = self.client.post(url, headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        with get_connection() as conn:
            row = conn.execute('SELECT * FROM event_media WHERE media_id=?', (media['media_id'],)).fetchone()
            self.assertEqual(json.loads(row['verification_json']), proof)
            self.assertEqual((row['status'], row['redaction_status'], row['error_code']),
                             ('failed', 'failed', 'face_verification_failed'))
        rows = self.client.get('/api/events/EVT_0001/media', headers=self.admin).json()['items']
        self.assertIsNone(next(item for item in rows if item['media_id']==media['media_id'])['url'])

    def test_worker_forwards_diagnostic_and_cleans_payloads(self):
        from backend.processing.worker import Worker
        self.fixture.upload()
        worker = Worker('http://127.0.0.1:8000', self.node['Authorization'].removeprefix('Bearer '))
        worker.client.close()
        sent = []
        def handle(request):
            if request.url.path.endswith('/failure'):
                sent.append(json.loads(request.read()))
            response = self.client.request(request.method, request.url.raw_path.decode(),
                headers=dict(request.headers), content=request.read())
            return httpx.Response(response.status_code, headers=dict(response.headers), content=response.content)
        worker.client = httpx.Client(base_url=worker.server, headers=self.node, transport=httpx.MockTransport(handle))
        self.addCleanup(worker.client.close)
        for invalid in (False, True):
            if invalid:
                # A second job proves malformed metadata cannot break failure reporting/cleanup.
                self.fixture.upload()
            error = VerificationFailure('detector_runtime_error')
            if invalid:
                error.verification_failure['raw_error'] = 'SECRET'
            with patch('backend.processing.worker.redact_image', side_effect=error):
                self.assertTrue(worker.run_once())
            self.assertEqual(sent[-1]['error_code'], 'face_verification_failed')
            self.assertEqual('verification_failure' in sent[-1], not invalid)
            self.assertNotIn('SECRET', json.dumps(sent[-1]))
            self.assertEqual(list(worker.root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
