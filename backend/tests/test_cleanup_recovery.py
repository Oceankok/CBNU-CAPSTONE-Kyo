"""Claim response loss recovery without GPU, provider calls or production data."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from backend.db.event_repository import get_connection
from backend.media.service import register_media
from backend.processing.worker import Worker
from backend.tests.support import isolated_api, login


class CleanupRecoveryTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch('backend.processing.worker.WORK_ROOT', root))
        self.sources = root/'sources'
        self.sources.mkdir()
        self.enterContext(patch('backend.media.paths.SOURCE_ROOT', self.sources))
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        response = self.client.post('/api/nodes', headers=self.admin,
                                    json={'node_id':'recovery', 'name':'Recovery test'})
        self.token = response.json()['token']
        self.node = {'Authorization':'Bearer '+self.token}
        self.client.put('/api/processing/workers/recovery', headers=self.admin,
                        json={'enabled':True}).raise_for_status()
        self.worker = Worker('https://isolated.invalid', self.token)
        self.worker.client.close()
        def handle(request):
            response = self.client.request(request.method, request.url.raw_path.decode(),
                headers=dict(request.headers), content=request.read())
            return httpx.Response(response.status_code, content=response.content)
        self.worker.client = httpx.Client(base_url=self.worker.server, headers=self.node,
                                         transport=httpx.MockTransport(handle))
        self.addCleanup(self.worker.client.close)

    def assigned(self, kind, expired=True):
        # Persist exactly the committed assignment that a worker failed to receive.
        ident = str(uuid4())
        until = time.time() + (-10 if expired else 300)
        if kind == 'media':
            media = register_media('EVT_0001','CAM_001','image','reference')
            with get_connection() as conn:
                conn.execute('UPDATE event_media SET processing_generation=1 WHERE media_id=?',(media,))
                conn.execute("INSERT INTO processing_job(job_id,media_id,generation,status,redaction_mode,kind,worker_node_id,lease_until,local_cleanup_status,created_at,updated_at) VALUES(?,?,1,'running','scrfd','image','recovery',?,'pending',?,?)",
                             (ident,media,until,time.time(),time.time()))
        elif kind == 'snapshot':
            with get_connection() as conn:
                conn.execute("INSERT INTO camera_snapshot(snapshot_id,camera_id,source_node_id,capture_start_at,status,worker_node_id,lease_until,expires_at,created_at,local_cleanup_status) VALUES(?,'CAM_001','recovery','2026-11-06T00:00:00+00:00','running','recovery',?,?,?,'pending')",
                             (ident,until,until,time.time()))
        else:
            with get_connection() as conn:
                conn.execute("INSERT INTO host_ai_job(job_id,kind,provider,input_json,status,deadline,worker_node_id,cleanup_pending,created_at) VALUES(?,'education','codex_chatgpt','{}','running',?,'recovery',1,?)",
                             (ident,until,time.time()))
        return ident

    def test_lost_claim_response_recovers_all_kinds_without_journal(self):
        ids = {kind:self.assigned(kind) for kind in ('media','snapshot','ai')}
        self.assertEqual(list(self.worker.root.iterdir()), [])
        response = self.worker.client.post('/api/processing/cleanup-recovery')
        response.raise_for_status()
        self.assertEqual({r['job_id'] for r in response.json()['items']},set(ids.values()))
        self.worker.recover_cleanup()
        self.worker.recover_cleanup()  # Lost cleanup acknowledgement is harmless.
        self.assertEqual(self.worker.client.post('/api/processing/cleanup-recovery').json()['items'], [])
        self.assertEqual(list(self.worker.root.iterdir()), [])
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT local_cleanup_status FROM processing_job WHERE job_id=?',(ids['media'],)).fetchone()[0],'confirmed')
            self.assertEqual(conn.execute('SELECT cleanup_pending FROM host_ai_job WHERE job_id=?',(ids['ai'],)).fetchone()[0],0)
        self.assertEqual(self.client.post('/api/processing/claim',headers=self.node).status_code,200)

    def test_active_claims_not_exposed_or_acknowledged(self):
        for kind in ('media','snapshot','ai'):
            self.assigned(kind,expired=False)
        self.worker.recover_cleanup()
        self.assertEqual(self.worker.client.post('/api/processing/cleanup-recovery').json()['items'], [])
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM processing_job WHERE local_cleanup_status='pending'").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM camera_snapshot WHERE local_cleanup_status='pending'").fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM host_ai_job WHERE cleanup_pending=1').fetchone()[0],1)

    def test_owned_payload_erased_even_without_journal(self):
        ident = self.assigned('media')
        folder = self.worker.root/ident
        folder.mkdir()
        (folder/'source.jpg').write_bytes(b'private test payload')
        self.worker.recover_cleanup()
        self.assertFalse(folder.exists())

    def test_auth_owner_scope_and_revoked_processing_capability(self):
        ident = self.assigned('media')
        path = '/api/processing/cleanup-recovery'
        self.assertEqual(self.client.post(path).status_code,401)
        self.assertEqual(self.client.post(path,headers=self.admin).status_code,401)
        other = self.client.post('/api/nodes',headers=self.admin,json={'node_id':'other','name':'Other'}).json()
        headers = {'Authorization':'Bearer '+other['token']}
        self.assertEqual(self.client.post(path,headers=headers).json()['items'],[])
        self.assertEqual(self.client.post('/api/processing/jobs/'+ident+'/cleanup',headers=headers).status_code,403)
        self.client.put('/api/processing/workers/recovery',headers=self.admin,json={'enabled':False}).raise_for_status()
        self.worker.recover_cleanup()
        self.assertEqual(self.client.post(path,headers=self.node).json()['items'],[])

    def test_offline_startup_still_erases_journaled_payloads(self):
        ident = str(uuid4())
        folder = self.worker.root/ident
        folder.mkdir()
        (folder/'journal.json').write_text(json.dumps({'job_id':ident,'server':self.worker.server}))
        (folder/'source.jpg').write_bytes(b'private test payload')
        with patch.object(self.worker.client,'post',side_effect=httpx.ConnectError('offline')):
            self.worker.recover_cleanup()
        self.assertFalse((folder/'source.jpg').exists())
        self.assertTrue((folder/'journal.json').exists())

    def queued_media(self):
        from backend.processing.service import enqueue
        media = register_media('EVT_0001','CAM_001','image','reference')
        name = str(uuid4())+'.jpg'
        (self.sources/name).write_bytes(b'private fixture; never decoded')
        with get_connection() as conn:
            conn.execute('UPDATE event_media SET source_path=?,source_expires_at=? WHERE media_id=?',
                         (name,time.time()+600,media))
        return enqueue(media)

    def test_committed_claim_response_loss_allows_next_assignment_after_recovery(self):
        first = self.queued_media()
        original_post = self.worker.client.post
        def lose_claim_response(url, **kwargs):
            result = original_post(url, **kwargs)
            if url == '/api/processing/claim':
                result.raise_for_status()
                self.assertEqual(result.json()['job']['job_id'],first)
                raise httpx.ReadTimeout('claim committed; response lost')
            return result
        with patch.object(self.worker.client,'post',side_effect=lose_claim_response):
            with self.assertRaises(httpx.ReadTimeout):
                self.worker.run_once()
        self.assertFalse((self.worker.root/first).exists())
        next_job = self.queued_media()
        with get_connection() as conn:
            conn.execute('UPDATE processing_job SET lease_until=? WHERE job_id=?',(time.time()-1,first))
        self.worker.recover_cleanup()
        response = original_post('/api/processing/claim')
        response.raise_for_status()
        self.assertEqual(response.json()['job']['job_id'],next_job)
