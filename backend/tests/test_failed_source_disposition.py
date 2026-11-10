import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.db.event_repository import get_connection
from backend.media.paths import private_source_path
from backend.tests import test_verified_source_policy as fixtures
from backend.tests.support import login


class FailedSourceDispositionTests(unittest.TestCase):
    setUp = fixtures.VerifiedSourcePolicyTests.setUp
    upload = fixtures.VerifiedSourcePolicyTests.upload
    claim = fixtures.VerifiedSourcePolicyTests.claim
    media = fixtures.VerifiedSourcePolicyTests.media

    def failed(self, clean=True):
        media = self.upload()
        job, headers = self.claim()
        self.client.post('/api/processing/jobs/'+job['job_id']+'/failure', headers=headers,
                         json={'error_code': 'face_verification_failed'}).raise_for_status()
        if clean:
            self.client.post('/api/processing/jobs/'+job['job_id']+'/cleanup', headers=self.node).raise_for_status()
        row = self.media(media['media_id'])
        self.endpoint = '/api/media/'+row['media_id']+'/discard-failed-source'
        self.body = {'expected_generation': row['processing_generation'], 'reason': '관리자 파기 선택'}
        return row

    def test_discard_preserves_failure_event_and_audit_without_preview(self):
        row = self.failed()
        source = private_source_path(row['source_path'])
        sentinel = source.parent/'keep-other-source.png'
        sentinel.write_bytes(b'other synthetic source')
        response = self.client.post(self.endpoint, headers=self.admin, json=self.body)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertIsNone(result['url'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['face_verification']['actor'], 'admin02')
        self.assertEqual(result['face_verification']['available_actions'], [])
        self.assertIsNotNone(result['source_deleted_at'])
        self.assertFalse(source.exists())
        self.assertTrue(sentinel.exists())
        self.assertEqual(self.client.post(self.endpoint, headers=self.admin, json=self.body).status_code, 200)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT event_status FROM candidate_event WHERE event_id=?', (row['event_id'],)).fetchone()[0], 'pending')
        self.assertEqual(json.loads(self.media(row['media_id'])['verification_json'])['reason'], self.body['reason'])

    def test_unconfirmed_worker_cleanup_blocks_discard(self):
        row = self.failed(clean=False)
        self.assertEqual(self.client.post(self.endpoint, headers=self.admin, json=self.body).status_code, 409)
        self.assertTrue(private_source_path(row['source_path']).exists())

    def test_permissions_generation_and_reason_validation(self):
        row = self.failed()
        self.assertEqual(self.client.post(self.endpoint, json=self.body).status_code, 401)
        worker = login(self.client, 'worker01')
        self.assertEqual(self.client.post(self.endpoint, headers=worker, json=self.body).status_code, 403)
        wrong = {**self.body, 'expected_generation': self.body['expected_generation']+1}
        self.assertEqual(self.client.post(self.endpoint, headers=self.admin, json=wrong).status_code, 409)
        self.assertEqual(self.client.post(self.endpoint, headers=self.admin, json={**self.body,'reason':' '}).status_code, 400)
        self.assertTrue(private_source_path(row['source_path']).exists())

    def test_file_deletion_failure_is_retryable_and_keeps_audit_unchanged(self):
        row = self.failed()
        source = private_source_path(row['source_path'])
        original = Path.unlink

        def locked(path, *args, **kwargs):
            if path == source:
                raise PermissionError('synthetic lock')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'unlink', autospec=True, side_effect=locked):
            self.assertEqual(self.client.post(self.endpoint, headers=self.admin, json=self.body).status_code, 409)
        self.assertTrue(source.exists())
        self.assertIsNone(self.media(row['media_id'])['source_deleted_at'])
        self.assertEqual(self.client.post(self.endpoint, headers=self.admin, json=self.body).status_code, 200)

    def test_running_job_cannot_be_discarded_as_failed(self):
        media = self.upload()
        self.claim()
        row = self.media(media['media_id'])
        endpoint = '/api/media/'+row['media_id']+'/discard-failed-source'
        body = {'expected_generation': row['processing_generation'], 'reason':'test'}
        self.assertEqual(self.client.post(endpoint, headers=self.admin, json=body).status_code, 409)
        self.assertTrue(private_source_path(row['source_path']).exists())
