from unittest import TestCase
from unittest.mock import patch
from backend.tests import test_capture_processor_eos_e2e as fixture
from backend.db.event_repository import get_connection
from backend.video import service

class ContextRawRetentionTests(TestCase):
    setUp=fixture.ProcessorEosApiTests.setUp
    upload=fixture.ProcessorEosApiTests.upload

    def row(self):
        with get_connection() as conn:
            return dict(conn.execute('SELECT * FROM video_chunk WHERE stream_id=?',(self.streams['CAM_001'],)).fetchone())

    def test_default_is_original_thirty_seconds(self):
        with patch.dict('os.environ',{},clear=False):
            import os
            old=os.environ.pop('PPE_CONTEXT_RAW_RETENTION_SECONDS',None)
            try:self.upload('CAM_001',0)
            finally:
                if old is not None:os.environ['PPE_CONTEXT_RAW_RETENTION_SECONDS']=old
        row=self.row();self.assertEqual(row['expires_at']-row['received_at'],30)

    def test_explicit_policy_is_absolute_and_reads_never_renew(self):
        with patch.dict('os.environ',{'PPE_CONTEXT_RAW_RETENTION_SECONDS':'120'}):self.upload('CAM_001',0)
        row=self.row();self.assertEqual(row['expires_at']-row['received_at'],120)
        for _ in range(3):
            self.client.get('/api/processing/video/streams/'+self.streams['CAM_001']+'/availability',headers=self.nodes['processor']).raise_for_status()
        with patch.dict('os.environ',{'PPE_CONTEXT_RAW_RETENTION_SECONDS':'30'}):self.assertEqual(self.row()['expires_at'],row['expires_at'])
        with patch.object(service.time,'time',return_value=row['received_at']+31):
            service.cleanup();self.assertEqual(self.row()['chunk_id'],row['chunk_id'])
        with patch.object(service.time,'time',return_value=row['expires_at']):
            service.cleanup()
        with get_connection() as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM video_chunk WHERE chunk_id=?',(row['chunk_id'],)).fetchone())
            self.assertIsNotNone(conn.execute('SELECT 1 FROM capture_chunk_receipt WHERE stream_id=?',(self.streams['CAM_001'],)).fetchone())

    def test_policy_rejects_unbounded_invalid_values(self):
        from fastapi import HTTPException
        for value in ('0','-1','121','3600',' 120','nan','1e2'):
            with patch.dict('os.environ',{'PPE_CONTEXT_RAW_RETENTION_SECONDS':value}):
                with self.assertRaises(HTTPException):service.context_raw_retention_seconds()
        for value in ('30','60','120'):
            with patch.dict('os.environ',{'PPE_CONTEXT_RAW_RETENTION_SECONDS':value}):self.assertEqual(service.context_raw_retention_seconds(),int(value))

    def test_retention_does_not_increase_storage_limits(self):
        self.assertEqual((service.MAX_BYTES,service.CAMERA_BYTES,service.TOTAL_BYTES),(8*1024**2,64*1024**2,256*1024**2))
        with patch.dict('os.environ',{'PPE_CONTEXT_RAW_RETENTION_SECONDS':'120'}):
            with get_connection() as conn:
                self.assertTrue(service._buffer_full(conn,'CAM_001',service.CAMERA_BYTES+1))

    def test_stream_policy_metadata_does_not_relabel_old_chunks(self):
        self.upload('CAM_001',0)
        original=self.row()['expires_at']
        with patch.dict('os.environ',{'PPE_CONTEXT_RAW_RETENTION_SECONDS':'120'}):
            response=self.client.post('/api/video/streams',headers=self.nodes['source'],json={
                'camera_id':'CAM_001','stream_id':self.streams['CAM_001'],'capture_context_id':self.contexts['CAM_001']})
            self.assertEqual(response.status_code,201,response.text)
            self.assertEqual(response.json()['raw_retention_seconds_for_new_uploads'],120)
            self.assertEqual(response.json()['buffer_seconds'],30)
        self.assertEqual(self.row()['expires_at'],original)
