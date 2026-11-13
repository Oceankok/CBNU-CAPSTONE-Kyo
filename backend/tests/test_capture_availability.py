from unittest import TestCase
from unittest.mock import patch
from backend.tests import test_capture_processor_eos_e2e as fixture
from backend.db.event_repository import get_connection
from backend.video import service

class AvailabilityTests(TestCase):
    setUp=fixture.ProcessorEosApiTests.setUp
    upload=fixture.ProcessorEosApiTests.upload
    stop=fixture.ProcessorEosApiTests.stop

    def availability(self, after=-1, headers=None):
        return self.client.get('/api/processing/video/streams/'+self.streams['CAM_001']+'/availability',
                               params={'after_sequence':after},headers=self.nodes['processor'] if headers is None else headers)

    def test_pending_available_final_and_permissions(self):
        self.assertEqual(self.availability().json()['next_chunk_state'],'not_received')
        self.assertEqual(self.availability(after=0).status_code,409)
        self.upload('CAM_001',0)
        state=self.availability().json()
        self.assertEqual((state['next_sequence'],state['next_chunk_state']),(0,'available'))
        self.assertEqual(state['capture_context_id'],self.contexts['CAM_001'])
        self.assertEqual(self.availability(headers=self.nodes['source']).status_code,403)
        self.assertEqual(self.availability(headers=self.admin).status_code,401)
        self.stop('CAM_001',0)
        state=self.availability(after=0).json()
        self.assertTrue(state['capture_stopped']);self.assertEqual(state['final_sequence'],0)
        self.assertEqual(state['next_chunk_state'],'not_received')

    def test_expiry_then_cleanup_is_unavailable_not_eos(self):
        self.upload('CAM_001',0);self.stop('CAM_001',0)
        with get_connection() as conn:
            conn.execute('UPDATE video_chunk SET expires_at=0 WHERE stream_id=?',(self.streams['CAM_001'],))
        self.assertEqual(self.availability().json()['reason'],'raw_expired')
        service.cleanup()
        state=self.availability().json()
        self.assertEqual((state['next_chunk_state'],state['reason']),('unavailable','raw_unavailable'))
        self.assertTrue(state['capture_stopped'])
        self.assertEqual(self.client.get('/api/processing/video/streams',headers=self.nodes['processor']).json()['items'][0]['camera_id'],'CAM_TAIL')

    def test_disk_missing_and_receipt_missing_are_distinct_failures(self):
        self.upload('CAM_001',0)
        with get_connection() as conn:
            key=conn.execute('SELECT chunk_id FROM video_chunk WHERE stream_id=?',(self.streams['CAM_001'],)).fetchone()[0]
        service.chunk_path(key).unlink()
        self.assertEqual(self.availability().json()['reason'],'raw_missing')
        with get_connection() as conn:
            conn.execute('DELETE FROM capture_chunk_receipt WHERE stream_id=?',(self.streams['CAM_001'],))
        self.assertEqual(self.availability().json()['reason'],'receipt_missing')

    def test_reads_do_not_extend_retention_or_limits(self):
        self.upload('CAM_001',0)
        with get_connection() as conn:
            before=dict(conn.execute('SELECT received_at,expires_at FROM video_chunk WHERE stream_id=?',(self.streams['CAM_001'],)).fetchone())
        for _ in range(5):self.assertEqual(self.availability().status_code,200)
        with get_connection() as conn:
            self.assertEqual(before,dict(conn.execute('SELECT received_at,expires_at FROM video_chunk WHERE stream_id=?',(self.streams['CAM_001'],)).fetchone()))
        self.assertAlmostEqual(before['expires_at']-before['received_at'],30)
        self.assertEqual(service.CAMERA_BYTES,64*1024*1024)
        self.assertEqual(service.TOTAL_BYTES,256*1024*1024)

    def test_bound_reader_reports_unavailable_after_closed_discovery_vanishes(self):
        for camera in self.streams:self.upload(camera,0)
        self.assertEqual(len(self.reader.poll()),4)
        self.upload('CAM_001',1);self.stop('CAM_001',1)
        with get_connection() as conn:
            conn.execute('UPDATE video_chunk SET expires_at=0 WHERE stream_id=?',(self.streams['CAM_001'],))
        service.cleanup()
        from inference_worker.continuous_source_reader import SourceUnavailableError
        with self.assertRaises(SourceUnavailableError) as caught:self.reader.poll()
        self.assertEqual(caught.exception.expected_sequence,1)
        self.assertEqual(caught.exception.camera,'CAM_001')
