import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from backend.db.event_repository import get_connection
from backend.media.paths import private_source_path
from backend.media.service import _request_delete, delete_event_media
from backend.processing.service import expire_jobs
from backend.tests.support import isolated_api, login


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        processed = root / 'storage/candidate_events/processed'
        work = root / '.private_media'
        source = work / 'originals'
        source.mkdir(parents=True)
        for module, name, value in [
            ('backend.media.paths', 'PROJECT_ROOT', root), ('backend.media.paths', 'STORAGE_ROOT', root / 'storage'),
            ('backend.media.paths', 'SOURCE_ROOT', source), ('backend.media.service', 'SOURCE_ROOT', source),
            ('backend.media.service', 'WORK_ROOT', work), ('backend.media.service', 'PROCESSED_ROOT', processed),
            ('backend.media.routes', 'WORK_ROOT', work), ('backend.processing.routes', 'WORK_ROOT', work),
            ('backend.processing.routes', 'PROCESSED_ROOT', processed), ('backend.processing.worker', 'WORK_ROOT', work)]:
            scope = patch(module + '.' + name, value)
            scope.start()
            self.addCleanup(scope.stop)
        scope = patch.dict(os.environ, {'PPE_PROCESSING_MODE': 'remote'})
        scope.start()
        self.addCleanup(scope.stop)
        context = isolated_api()
        self.client = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.admin = login(self.client)
        response = self.client.post('/api/nodes', headers=self.admin, json={'node_id': 'gpu', 'name': '처리 PC'})
        self.assertEqual(response.status_code, 201, response.text)
        self.node = {'Authorization': 'Bearer ' + response.json()['token']}
        self.client.put('/api/processing/workers/gpu', headers=self.admin, json={'enabled': True}).raise_for_status()
        with get_connection() as conn:
            if not conn.execute("SELECT 1 FROM candidate_event WHERE event_id='EVT_0001'").fetchone():
                raise AssertionError('Missing test event')
        self.image = cv2.imencode('.jpg', np.zeros((64,64,3), dtype=np.uint8))[1].tobytes()
        self.meta = {'faces_detected': 1, 'processed_frames': 1, 'redaction_mode': 'scrfd',
                     'inference_backend': 'test-only', 'processing_seconds': 0.1}

    def upload(self):
        response = self.client.post('/api/events/EVT_0001/media?kind=image', headers=self.admin, content=self.image)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['processing_state'], 'queued')
        self.assertIsNone(response.json()['url'])
        self.assertFalse(response.json()['can_reprocess'])
        return response.json()

    def claim(self):
        response = self.client.post('/api/processing/claim', headers=self.node)
        self.assertEqual(response.status_code, 200, response.text)
        job = response.json()['job']
        return job, {**self.node, 'X-Processing-Lease': job['lease_token']}

    def result(self, job, headers, image=None):
        return self.client.post('/api/processing/jobs/' + job['job_id'] + '/result',
            headers={**headers, 'Content-Type': 'image/jpeg', 'X-Processing-Metadata': json.dumps(self.meta)},
            content=image if image is not None else self.image)

    def test_queue_source_publish_and_idempotent_result(self):
        media = self.upload()
        job, headers = self.claim()
        self.assertEqual(self.client.get(job['source_url'], headers=headers).content, self.image)
        self.assertEqual(self.client.get(job['source_url']).status_code, 401)
        self.assertEqual(self.client.get(job['source_url'], headers=self.admin).status_code, 401)
        result = self.result(job, headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.result(job, headers).status_code, 200)
        other = cv2.imencode('.jpg', np.ones((64,64,3), dtype=np.uint8) * 255)[1].tobytes()
        self.assertEqual(self.result(job, headers, other).status_code, 409)
        self.assertEqual(self.client.post('/api/processing/jobs/' + job['job_id'] + '/cleanup', headers=self.node).status_code, 200)
        rows = self.client.get('/api/events/EVT_0001/media', headers=self.admin).json()['items']
        row = next(row for row in rows if row['media_id'] == media['media_id'])
        self.assertEqual(row['status'], 'ready')
        self.assertIsNotNone(row['url'])

    def test_delete_blocks_late_result_and_waits_for_worker_cleanup(self):
        media = self.upload()
        job, headers = self.claim()
        with get_connection() as conn:
            _request_delete(conn, 'EVT_0001', 'admin02')
        self.assertEqual(delete_event_media('EVT_0001')['pending_media'], 1)
        self.assertEqual(self.client.get(job['source_url'], headers=headers).status_code, 409)
        self.assertEqual(self.result(job, headers).status_code, 409)
        self.assertEqual(self.client.post('/api/processing/jobs/' + job['job_id'] + '/cleanup', headers=self.node).status_code, 200)
        self.assertEqual(delete_event_media('EVT_0001')['pending_media'], 0)
        with get_connection() as conn:
            row = conn.execute('SELECT * FROM event_media WHERE media_id=?', (media['media_id'],)).fetchone()
            self.assertEqual(row['status'], 'deleted')
            self.assertIsNotNone(conn.execute("SELECT deleted_at FROM event_retention WHERE event_id='EVT_0001'").fetchone()[0])

    def test_native_worker_reuses_redaction_and_confirms_cleanup(self):
        import httpx
        from backend.processing.worker import Worker
        self.upload()
        worker = Worker('http://127.0.0.1:8000', self.node['Authorization'].removeprefix('Bearer '))
        worker.client.close()
        def handle(request):
            response = self.client.request(request.method, str(request.url).replace('http://127.0.0.1:8000', ''),
                                           headers=dict(request.headers), content=request.read())
            return httpx.Response(response.status_code, headers=dict(response.headers), content=response.content)
        worker.client = httpx.Client(base_url=worker.server, headers=self.node, transport=httpx.MockTransport(handle))
        self.addCleanup(worker.client.close)
        def redact(source, output, mode):
            output.write_bytes(self.image)
            return self.meta
        with patch('backend.processing.worker.redact_image', side_effect=redact):
            self.assertTrue(worker.run_once())
        self.assertEqual(list(worker.root.iterdir()), [])
        with get_connection() as conn:
            row = conn.execute('SELECT status,local_cleanup_status FROM processing_job').fetchone()
            self.assertEqual(row['status'], 'succeeded')
            self.assertEqual(row['local_cleanup_status'], 'confirmed')

    def test_lease_expiry_retry_rejects_old_generation(self):
        media = self.upload()
        old, headers = self.claim()
        with get_connection() as conn:
            conn.execute('UPDATE processing_job SET lease_until=? WHERE job_id=?', (time.time()-1, old['job_id']))
        self.assertIsNone(self.client.post('/api/processing/claim', headers=self.node).json()['job'])
        expire_jobs()
        retry = self.client.post('/api/media/' + media['media_id'] + '/redact', headers=self.admin)
        self.assertEqual(retry.status_code, 202, retry.text)
        self.assertEqual(self.result(old, headers).status_code, 409)
        self.assertIsNone(self.client.post('/api/processing/claim', headers=self.node).json()['job'])
        self.client.post('/api/processing/jobs/' + old['job_id'] + '/cleanup', headers=self.node).raise_for_status()
        new, _ = self.claim()
        self.assertNotEqual(new['job_id'], old['job_id'])
        self.assertGreater(new['generation'], old['generation'])

    def test_regular_node_cannot_claim_or_access_source(self):
        response = self.client.post('/api/nodes', headers=self.admin, json={'node_id': 'field', 'name': '현장 PC'})
        node = {'Authorization': 'Bearer ' + response.json()['token']}
        self.assertEqual(self.client.post('/api/processing/claim', headers=node).status_code, 403)
        self.upload()
        job, headers = self.claim()
        self.assertEqual(self.client.get(job['source_url'], headers={**headers, 'X-Processing-Lease': 'bad'}).status_code, 403)
        self.assertEqual(self.client.get(job['source_url'], headers=node).status_code, 403)

    def test_missing_source_fails_queued_job_without_dispatch(self):
        media = self.upload()
        with get_connection() as conn:
            row = conn.execute('SELECT source_path FROM event_media WHERE media_id=?', (media['media_id'],)).fetchone()
            private_source_path(row[0]).unlink()
        self.assertIsNone(self.client.post('/api/processing/claim', headers=self.node).json()['job'])
        result = self.client.get('/api/processing/jobs/' + media['job_id'], headers=self.admin).json()
        self.assertEqual(result['status'], 'failed')

    def test_invalid_result_does_not_publish(self):
        self.upload()
        job, headers = self.claim()
        self.assertEqual(self.result(job, headers, b'invalid').status_code, 422)
        with get_connection() as conn:
            row = conn.execute('SELECT status,storage_path FROM event_media').fetchone()
            self.assertEqual(row['status'], 'processing')
            self.assertEqual(row['storage_path'], '')
