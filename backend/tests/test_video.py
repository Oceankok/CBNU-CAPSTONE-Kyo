from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from backend.tests.support import isolated_api, login
from backend.db.event_repository import get_connection
from backend.db.migrations import LATEST_VERSION
from backend.db.connection import is_postgres, schema_version
from backend.video import service


class VideoIngestTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        scope = patch('backend.video.service.ROOT',Path(temporary.name))
        scope.start()
        self.addCleanup(scope.stop)
        context = isolated_api()
        self.client = context.__enter__()
        self.addCleanup(context.__exit__,None,None,None)
        self.admin = login(self.client)
        self.nodes = {}
        for ident in ('source','processor','other'):
            response = self.client.post('/api/nodes',headers=self.admin,json={'node_id':ident,'name':ident})
            response.raise_for_status()
            self.nodes[ident] = {'Authorization':'Bearer '+response.json()['token']}
        self.client.put('/api/cameras/CAM_001/node',headers=self.admin,json={'source_node_id':'source','output_node_id':'source'}).raise_for_status()
        self.client.put('/api/processing/workers/processor',headers=self.admin,json={'enabled':True}).raise_for_status()
        self.data = (Path(__file__).resolve().parents[1]/'db/demo_assets/sample.mp4').read_bytes()

    def start(self):
        response = self.client.post('/api/video/streams',headers=self.nodes['source'],json={'camera_id':'CAM_001'})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()['stream_id']

    def upload(self, stream, sequence=0, data=None, headers=None):
        if not hasattr(self,'captured'):
            self.captured = datetime.now(timezone.utc).isoformat()
        values = {**self.nodes['source'],'Content-Type':'video/mp4','X-Capture-Start':self.captured,'X-Duration-Ms':'1000'}
        values.update(headers or {})
        return self.client.post(f'/api/video/streams/{stream}/chunks?sequence={sequence}',headers=values,content=self.data if data is None else data)

    def test_source_and_processor_permissions(self):
        for headers, status in [({},401),(self.admin,401),(self.nodes['other'],403)]:
            response = self.client.post('/api/video/streams',headers=headers,json={'camera_id':'CAM_001'})
            self.assertEqual(response.status_code,status,response.text)
        stream = self.start()
        media = self.upload(stream)
        self.assertEqual(media.status_code,202,media.text)
        key = media.json()['chunk_id']
        path = '/api/processing/video/chunks/'+key
        self.assertEqual(self.client.get(path,headers=self.nodes['source']).status_code,403)
        self.assertEqual(self.client.get(path,headers=self.admin).status_code,401)
        response = self.client.get(path,headers=self.nodes['processor'])
        self.assertEqual(response.content,self.data)
        self.assertIn('no-store',response.headers['cache-control'])
        listed = self.client.get(f'/api/processing/video/streams/{stream}/chunks',headers=self.nodes['processor']).json()['items']
        self.assertEqual(listed[0]['sequence'],0)
        self.assertFalse(any('path' in field for field in listed[0]))

    def test_duplicate_conflict_gaps_and_sequence_after_cleanup(self):
        stream = self.start()
        self.assertEqual(self.upload(stream).status_code,202)
        self.assertTrue(self.upload(stream).json()['duplicate'])
        conflict = self.upload(stream,headers={'X-Capture-Start':datetime.now(timezone.utc).isoformat()})
        self.assertEqual(conflict.status_code,409,conflict.text)
        response = self.upload(stream,sequence=2)
        self.assertEqual(response.json()['sequence_gap'],1)
        with get_connection() as conn:
            conn.execute('UPDATE video_chunk SET expires_at=?',(time.time()-1,))
        service.cleanup()
        self.assertEqual(self.upload(stream,sequence=0).status_code,409)
        self.assertEqual(self.upload(stream,sequence=3).status_code,202)

    def test_session_replacement_and_camera_revocation(self):
        old = self.start()
        new = self.start()
        self.assertEqual(self.upload(old).status_code,409)
        response = self.upload(new)
        self.assertEqual(response.status_code,202,response.text)
        key = response.json()['chunk_id']
        self.client.delete('/api/cameras/CAM_001',headers=self.admin).raise_for_status()
        self.assertEqual(self.upload(new,sequence=1).status_code,409)
        self.assertEqual(self.client.get('/api/processing/video/chunks/'+key,headers=self.nodes['processor']).status_code,409)

    def test_invalid_content_size_metadata_and_capacity_leave_no_payload(self):
        stream = self.start()
        self.assertEqual(self.upload(stream,data=b'bad').status_code,422)
        self.assertEqual(self.upload(stream,headers={'X-Capture-Start':'2026-10-16T00:00:00'}).status_code,422)
        self.assertEqual(self.upload(stream,headers={'Content-Type':'image/jpeg'}).status_code,415)
        with patch.object(service,'MAX_BYTES',8):
            self.assertEqual(self.upload(stream).status_code,413)
        with patch.object(service,'TOTAL_BYTES',8):
            self.assertEqual(self.upload(stream).status_code,429)
        self.assertEqual(list(service.ROOT.glob('*.mp4')),[])
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM video_chunk').fetchone()[0],0)

    def test_expiry_removes_files_and_rejects_reads(self):
        stream = self.start()
        key = self.upload(stream).json()['chunk_id']
        with get_connection() as conn:
            conn.execute('UPDATE video_chunk SET expires_at=?',(time.time()-1,))
            conn.execute('UPDATE video_stream SET last_seen_at=?',(time.time()-40,))
        self.assertEqual(self.client.get('/api/processing/video/chunks/'+key,headers=self.nodes['processor']).status_code,404)
        self.assertEqual(service.cleanup(),1)
        self.assertFalse(service.chunk_path(key).exists())
        self.assertEqual(self.upload(stream,sequence=1).status_code,409)

    def test_v9_upgrade_preserves_accounts(self):
        with get_connection() as conn:
            conn.execute('DROP TABLE video_chunk')
            conn.execute('DROP TABLE video_stream')
            if is_postgres():
                conn.execute('UPDATE app_schema_version SET version=9 WHERE singleton=1')
            else:
                conn.execute('PRAGMA user_version=9')
        if is_postgres():
            from backend.db.postgres import initialize
            initialize()
        else:
            from backend.db.migrations import apply_migrations
            with get_connection() as conn:
                apply_migrations(conn)
        with get_connection() as conn:
            self.assertEqual(schema_version(conn),LATEST_VERSION)
        self.assertEqual(self.client.get('/api/auth/me',headers=self.admin).status_code,200)
