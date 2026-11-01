from datetime import datetime, timezone
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import cv2
import httpx
import numpy as np

from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login
from backend.work_areas import snapshots


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for module, key, value in (
            ('backend.work_areas.snapshots', 'ROOT', self.root/'snapshots'),
            ('backend.video.service', 'ROOT', self.root/'chunks'),
            ('backend.processing.worker', 'WORK_ROOT', self.root/'worker')):
            scope = patch(module+'.'+key, value)
            scope.start()
            self.addCleanup(scope.stop)
        context = isolated_api()
        self.client = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.admin = login(self.client)
        self.worker_user = login(self.client, 'worker01')
        response = self.client.post('/api/nodes', headers=self.admin, json={'node_id': 'snapshot_gpu', 'name': '스냅샷 처리'})
        response.raise_for_status()
        self.node = {'Authorization': 'Bearer '+response.json()['token']}
        self.client.put('/api/processing/workers/snapshot_gpu', headers=self.admin, json={'enabled': True}).raise_for_status()
        self.client.put('/api/cameras/CAM_001/node', headers=self.admin,
            json={'source_node_id': 'snapshot_gpu', 'output_node_id': 'snapshot_gpu'}).raise_for_status()
        self.prefix = '/api/cameras/CAM_001/snapshots'
        self.frame = np.zeros((64, 64, 3), dtype=np.uint8)
        self.image = cv2.imencode('.jpg', self.frame)[1].tobytes()

    def feed(self):
        stream_id, chunk_id = str(uuid4()), str(uuid4())
        from backend.video.service import ROOT, chunk_path
        ROOT.mkdir(parents=True, exist_ok=True)
        file = chunk_path(chunk_id)
        writer = cv2.VideoWriter(str(file), cv2.VideoWriter_fourcc(*'mp4v'), 10, (64,64))
        for _ in range(5):
            writer.write(self.frame)
        writer.release()
        data = file.read_bytes()
        with get_connection() as conn:
            conn.execute("INSERT INTO video_stream(stream_id,camera_id,node_id,status,created_at,last_seen_at) VALUES(?,'CAM_001','snapshot_gpu','active',?,?)", (stream_id,time.time(),time.time()))
            conn.execute('''INSERT INTO video_chunk(chunk_id,stream_id,sequence,sha256,byte_size,capture_start_at,duration_ms,received_at,expires_at)
                VALUES(?,?,0,?,?,?,500,?,?)''', (chunk_id,stream_id,hashlib.sha256(data).hexdigest(),len(data),datetime.now(timezone.utc).isoformat(),time.time(),time.time()+30))
        return data

    def request(self):
        response = self.client.post(self.prefix, headers=self.admin)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def claim(self):
        response = self.client.post('/api/processing/snapshots/claim', headers=self.node)
        response.raise_for_status()
        job = response.json()['job']
        return job, {**self.node, 'X-Processing-Lease': job['lease_token']}

    def test_request_claim_redacted_result_and_private_access(self):
        raw = self.feed()
        item = self.request()
        self.assertIsNone(item['image_url'])
        self.assertEqual(self.request()['snapshot_id'], item['snapshot_id'])
        job, headers = self.claim()
        self.assertEqual(self.client.get(job['source_url'], headers=headers).content, raw)
        self.assertEqual(self.client.get(job['source_url'], headers=self.admin).status_code, 401)
        base = '/api/processing/snapshots/'+job['job_id']
        self.assertEqual(self.client.get(job['source_url'], headers=self.node).status_code, 403)
        result = self.client.post(base+'/result', headers={**headers,'Content-Type':'image/jpeg'}, content=self.image)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.client.post(base+'/result', headers={**headers,'Content-Type':'image/jpeg'}, content=self.image).status_code, 200)
        url = result.json()['image_url']
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.worker_user).status_code, 403)
        response = self.client.get(url, headers=self.admin)
        self.assertEqual(response.content, self.image)
        self.assertIn('no-store', response.headers['cache-control'])
        self.client.post(base+'/cleanup', headers=self.node).raise_for_status()
        self.assertFalse(snapshots.path(job['job_id'], '.mp4').exists())

    def test_unavailable_and_admin_only(self):
        response = self.client.post(self.prefix, headers=self.admin)
        self.assertEqual(response.json()['detail'], 'camera_frame_unavailable')
        self.assertEqual(self.client.post(self.prefix).status_code, 401)
        self.assertEqual(self.client.post(self.prefix, headers=self.worker_user).status_code, 403)
        self.assertEqual(self.client.post('/api/processing/snapshots/claim', headers=self.worker_user).status_code, 401)

    def test_failure_and_expiry_never_expose_original_and_cleanup_is_idempotent(self):
        self.feed()
        item = self.request()
        job, headers = self.claim()
        base = '/api/processing/snapshots/'+job['job_id']
        self.client.post(base+'/failure', headers=headers, json={'error_code': 'gpu_unavailable'}).raise_for_status()
        status = self.client.get(self.prefix+'/'+item['snapshot_id'], headers=self.admin).json()
        self.assertEqual(status['status'], 'failed')
        self.assertIsNone(status['image_url'])
        self.assertEqual(self.client.get(self.prefix+'/'+item['snapshot_id']+'/image', headers=self.admin).status_code, 409)
        with get_connection() as conn:
            conn.execute('UPDATE camera_snapshot SET expires_at=?', (time.time()-1,))
        self.client.post(base+'/cleanup', headers=self.node).raise_for_status()
        self.client.post(base+'/cleanup', headers=self.node).raise_for_status()
        self.assertEqual(list(snapshots.ROOT.iterdir()), [])

    def test_lease_timeout_blocks_late_result_and_pending_cleanup_blocks_new_work(self):
        self.feed()
        self.request()
        job, headers = self.claim()
        with get_connection() as conn:
            conn.execute('UPDATE camera_snapshot SET lease_until=?', (time.time()-1,))
        snapshots.maintain()
        base = '/api/processing/snapshots/'+job['job_id']
        self.assertEqual(self.client.post(base+'/result', headers={**headers,'Content-Type':'image/jpeg'}, content=self.image).status_code, 409)
        self.assertIsNone(self.client.post('/api/processing/claim', headers=self.node).json()['job'])
        self.client.post(base+'/cleanup', headers=self.node).raise_for_status()
        self.assertEqual(self.client.get(self.prefix+'/'+job['job_id'], headers=self.admin).json()['error_code'], 'snapshot_lease_expired')

    def test_worker_decodes_redacts_publishes_and_erases_local_payloads(self):
        self.feed()
        item = self.request()
        from backend.processing.worker import Worker
        worker = Worker('http://127.0.0.1:8000', self.node['Authorization'].removeprefix('Bearer '))
        worker.client.close()
        def handle(request):
            response = self.client.request(request.method, str(request.url).replace(worker.server, ''),
                                           headers=dict(request.headers), content=request.read())
            return httpx.Response(response.status_code, headers=dict(response.headers), content=response.content)
        worker.client = httpx.Client(base_url=worker.server, headers=self.node, transport=httpx.MockTransport(handle))
        self.addCleanup(worker.client.close)
        def redact(frame, output, mode):
            self.assertEqual(frame.shape, (64,64,3))
            self.assertEqual(mode, 'scrfd')
            output.write_bytes(self.image)
        with patch('backend.work_areas.snapshot_worker.redact_frame', side_effect=redact) as redactor:
            self.assertTrue(worker.run_once())
            redactor.assert_called_once()
        status = self.client.get(self.prefix+'/'+item['snapshot_id'], headers=self.admin).json()
        self.assertEqual(status['status'], 'ready')
        self.assertEqual(list(worker.root.iterdir()), [])
        self.assertFalse(snapshots.path(item['snapshot_id'], '.mp4').exists())

    def test_camera_revocation_and_malformed_result(self):
        self.feed()
        self.request()
        job, headers = self.claim()
        base = '/api/processing/snapshots/'+job['job_id']
        self.assertEqual(self.client.post(base+'/result', headers={**headers,'Content-Type':'image/jpeg'}, content=b'not-a-jpeg').status_code, 422)
        self.client.delete('/api/cameras/CAM_001', headers=self.admin).raise_for_status()
        self.assertEqual(self.client.get(job['source_url'], headers=headers).status_code, 409)
        self.assertEqual(self.client.post(base+'/result', headers={**headers,'Content-Type':'image/jpeg'}, content=self.image).status_code, 409)

    def test_ready_image_expiry_blocks_access_before_file_cleanup(self):
        self.feed()
        self.request()
        job, headers = self.claim()
        base = '/api/processing/snapshots/'+job['job_id']
        result = self.client.post(base+'/result', headers={**headers,'Content-Type':'image/jpeg'}, content=self.image)
        result.raise_for_status()
        with get_connection() as conn:
            conn.execute('UPDATE camera_snapshot SET expires_at=?', (time.time()-1,))
        self.assertEqual(self.client.get(result.json()['image_url'], headers=self.admin).status_code, 409)
        self.client.post(base+'/cleanup', headers=self.node).raise_for_status()
        self.assertEqual(list(snapshots.ROOT.iterdir()), [])

    def test_redaction_failure_erases_worker_payload_without_publishing_raw_frame(self):
        self.feed()
        item = self.request()
        from backend.media.redaction import MediaError
        from backend.processing.worker import Worker
        worker = Worker('http://127.0.0.1:8000', self.node['Authorization'].removeprefix('Bearer '))
        worker.client.close()
        def handle(request):
            response = self.client.request(request.method, str(request.url).replace(worker.server, ''),
                                           headers=dict(request.headers), content=request.read())
            return httpx.Response(response.status_code, headers=dict(response.headers), content=response.content)
        worker.client = httpx.Client(base_url=worker.server, headers=self.node, transport=httpx.MockTransport(handle))
        self.addCleanup(worker.client.close)
        with patch('backend.work_areas.snapshot_worker.redact_frame', side_effect=MediaError('scrfd_model_missing')):
            self.assertTrue(worker.run_once())
        result = self.client.get(self.prefix+'/'+item['snapshot_id'], headers=self.admin).json()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'scrfd_model_missing')
        self.assertIsNone(result['image_url'])
        self.assertEqual(list(worker.root.iterdir()), [])
        self.assertFalse(snapshots.path(item['snapshot_id'], '.jpg').exists())

    def test_revoked_camera_queue_is_failed_without_breaking_worker_claim(self):
        self.feed()
        item = self.request()
        self.client.delete('/api/cameras/CAM_001', headers=self.admin).raise_for_status()
        response = self.client.post('/api/processing/snapshots/claim', headers=self.node)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()['job'])
        result = self.client.get(self.prefix+'/'+item['snapshot_id'], headers=self.admin).json()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'snapshot_source_unavailable')
