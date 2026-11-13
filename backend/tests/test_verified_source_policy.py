import hashlib
import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.db.event_repository import get_connection
from backend.tests import test_processing as fixtures
from backend.media.paths import private_source_path
from backend.media.verification_policy import purge_verified_source


class VerifiedSourcePolicyTests(unittest.TestCase):
    setUp = fixtures.ProcessingTests.setUp
    upload = fixtures.ProcessingTests.upload
    claim = fixtures.ProcessingTests.claim
    result = fixtures.ProcessingTests.result

    def enable(self):
        self.enterContext(patch.dict(os.environ, {'PPE_MEDIA_DELETE_VERIFIED_SOURCE': 'true'}))

    def add_report(self, **overrides):
        self.meta['verification'] = {'model': 'retinaface-resnet50', 'status': 'passed',
            'passes': 2, 'repairs': 1, 'frames': 1,
            'sha256': hashlib.sha256(self.image).hexdigest(), **overrides}

    def media(self, media_id):
        with get_connection() as conn:
            return dict(conn.execute('SELECT * FROM event_media WHERE media_id=?', (media_id,)).fetchone())

    def test_verified_source_deleted_only_after_worker_cleanup_and_audit_preserved(self):
        self.enable()
        media = self.upload()
        job, headers = self.claim()
        source = private_source_path(self.media(media['media_id'])['source_path'])
        self.assertLessEqual(self.media(media['media_id'])['source_expires_at'] - time.time(), 3600)
        self.add_report()
        response = self.result(job, headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(purge_verified_source(media['media_id']))
        self.assertTrue(source.exists())
        self.client.post('/api/processing/jobs/'+job['job_id']+'/cleanup', headers=self.node).raise_for_status()
        row = self.media(media['media_id'])
        self.assertFalse(source.exists())
        self.assertEqual(row['source_path'], '')
        self.assertIsNotNone(row['source_deleted_at'])
        self.assertEqual(row['status'], 'ready')
        self.assertEqual(json.loads(row['verification_json'])['repairs'], 1)
        self.assertFalse(purge_verified_source(media['media_id']))
        # The event, verified output and result retry survive source deletion.
        self.assertEqual(self.result(job, headers).status_code, 200)
        public = self.client.get('/api/events/EVT_0001/media', headers=self.admin).json()['items']
        record = next(x for x in public if x['media_id'] == media['media_id'])
        self.assertIsNotNone(record['url'])
        self.assertFalse(record['can_reprocess'])
        self.assertEqual(record['face_verification']['status'], 'passed')

    def test_missing_or_mismatched_proof_blocks_publication_when_required(self):
        self.enable()
        media = self.upload()
        job, headers = self.claim()
        for values in (None, {'sha256': '0'*64}, {'frames': 2}, {'passes': 3, 'repairs': 0}):
            with self.subTest(values=values):
                self.meta.pop('verification', None)
                if values is not None:
                    self.add_report(**values)
                self.assertEqual(self.result(job, headers).status_code, 422)
                self.assertNotEqual(self.media(media['media_id'])['status'], 'ready')
        self.assertTrue(private_source_path(self.media(media['media_id'])['source_path']).exists())

    def test_deletion_failure_is_retryable_without_losing_verified_output(self):
        self.enable()
        media = self.upload()
        job, headers = self.claim()
        self.add_report()
        self.result(job, headers).raise_for_status()
        source = private_source_path(self.media(media['media_id'])['source_path'])
        original = type(source).unlink

        def blocked(path, *args, **kwargs):
            if path == source:
                raise PermissionError('synthetic lock')
            return original(path, *args, **kwargs)

        with patch.object(type(source), 'unlink', autospec=True, side_effect=blocked):
            response = self.client.post('/api/processing/jobs/'+job['job_id']+'/cleanup', headers=self.node)
            self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(source.exists())
        self.assertEqual(self.media(media['media_id'])['status'], 'ready')
        self.assertTrue(purge_verified_source(media['media_id']))
        self.assertFalse(source.exists())

    def test_repeated_verification_failure_has_actions_but_no_preview_or_admin_raw_access(self):
        self.enable()
        media = self.upload()
        job, headers = self.claim()
        response = self.client.post('/api/processing/jobs/'+job['job_id']+'/failure', headers=headers,
                                    json={'error_code': 'face_verification_failed'})
        self.assertEqual(response.status_code, 200, response.text)
        self.client.post('/api/processing/jobs/'+job['job_id']+'/cleanup', headers=self.node).raise_for_status()
        public = self.client.get('/api/events/EVT_0001/media', headers=self.admin).json()['items']
        item = next(x for x in public if x['media_id'] == media['media_id'])
        self.assertIsNone(item['url'])
        self.assertEqual(item['face_verification']['available_actions'], ['retry', 'discard_source'])
        self.assertEqual(self.client.get(job['source_url'], headers=self.admin).status_code, 401)
        self.assertTrue(private_source_path(self.media(media['media_id'])['source_path']).exists())

    def test_legacy_unverified_worker_does_not_trigger_early_deletion_when_policy_off(self):
        self.enterContext(patch.dict(os.environ, {'PPE_MEDIA_DELETE_VERIFIED_SOURCE': 'false',
                                                'PPE_MEDIA_REQUIRE_FACE_VERIFICATION': 'false'}))
        media = self.upload()
        job, headers = self.claim()
        self.result(job, headers).raise_for_status()
        self.client.post('/api/processing/jobs/'+job['job_id']+'/cleanup', headers=self.node).raise_for_status()
        self.assertTrue(private_source_path(self.media(media['media_id'])['source_path']).exists())

    def test_v19_upgrade_preserves_media_and_initializes_no_false_verification(self):
        from backend.db.connection import schema_version
        from backend.db.migrations import apply_migrations, LATEST_VERSION
        from backend.db.postgres import initialize
        media = self.upload()
        with get_connection() as conn:
            conn.execute('ALTER TABLE event_media DROP COLUMN verification_json')
            conn.execute('ALTER TABLE event_media DROP COLUMN source_deleted_at')
            postgres = getattr(conn, 'dialect', 'sqlite') == 'postgresql'
            conn.execute('UPDATE app_schema_version SET version=19 WHERE singleton=1' if postgres else 'PRAGMA user_version=19')
        if postgres:
            initialize()
        else:
            with get_connection() as conn:
                apply_migrations(conn)
        with get_connection() as conn:
            self.assertEqual(schema_version(conn), LATEST_VERSION)
        row = self.media(media['media_id'])
        self.assertEqual(row['verification_json'], '')
        self.assertIsNone(row['source_deleted_at'])
        self.assertTrue(private_source_path(row['source_path']).exists())

    def test_local_processing_uses_same_verification_and_source_policy(self):
        from backend.media.service import ingest_file
        self.enable()
        self.add_report()
        source = self.enterContext(TemporaryDirectory())
        path = Path(source)/'input.jpg'
        path.write_bytes(self.image)

        def redact(original, output, mode):
            output.write_bytes(self.image)
            return {**self.meta, 'profile': {'verification': self.meta['verification']}}

        with patch('backend.media.service.redact_image', side_effect=redact):
            row = ingest_file('EVT_0001', 'CAM_001', 'image', 'thumbnail', path)
        self.assertEqual(row['status'], 'ready')
        self.assertEqual(row['face_verification']['status'], 'passed')
        self.assertEqual(self.media(row['media_id'])['source_path'], '')
        self.assertIsNotNone(row['source_deleted_at'])

    def test_native_worker_sends_report_and_acknowledges_source_cleanup(self):
        self.enable()
        self.add_report()
        self.meta['profile'] = {'verification': self.meta['verification']}
        fixtures.ProcessingTests.test_native_worker_reuses_redaction_and_confirms_cleanup(self)
        with get_connection() as conn:
            row = conn.execute('SELECT * FROM event_media').fetchone()
        self.assertEqual(json.loads(row['verification_json'])['status'], 'passed')
        self.assertEqual(row['source_path'], '')
        self.assertIsNotNone(row['source_deleted_at'])
