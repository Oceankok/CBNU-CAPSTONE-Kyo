"""Existing retain deadlines apply before maintenance, including private workers."""
import time
import unittest
from unittest.mock import patch
from backend.tests import test_processing as fixtures
from backend.db.event_repository import get_connection


class ProcessingRetentionExpiryTests(unittest.TestCase):
    setUp=fixtures.ProcessingTests.setUp
    upload=fixtures.ProcessingTests.upload
    claim=fixtures.ProcessingTests.claim
    result=fixtures.ProcessingTests.result

    def expire(self,until=None):
        until=time.time()-1 if until is None else until
        with get_connection() as conn:
            conn.execute("UPDATE event_retention SET decision='retain',retain_until=? WHERE event_id='EVT_0001'",(until,))

    def test_expired_queued_job_not_claimed_without_maintenance(self):
        media=self.upload();self.expire()
        result=self.client.post('/api/processing/claim',headers=self.node)
        self.assertIsNone(result.json()['job'])
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT status FROM processing_job WHERE media_id=?',(media['media_id'],)).fetchone()[0],'cancelled')
            self.assertEqual(conn.execute("SELECT decision FROM event_retention WHERE event_id='EVT_0001'").fetchone()[0],'retain')
            self.assertEqual(conn.execute('SELECT status FROM event_media WHERE media_id=?',(media['media_id'],)).fetchone()[0],'failed')
        self.expire(time.time()+300)
        self.assertEqual(self.client.post('/api/media/'+media['media_id']+'/redact',headers=self.admin).status_code,202)

    def test_exact_deadline_blocks_source_renew_result_but_allows_owner_cleanup(self):
        media=self.upload();job,headers=self.claim();deadline=time.time()+1;self.expire(deadline)
        with patch('backend.media.retention_access.time.time',return_value=deadline):
            response=self.client.get(job['source_url'],headers=headers)
            self.assertEqual(response.status_code,409)
            self.assertNotEqual(response.content,self.image)
            self.assertEqual(self.client.post('/api/processing/jobs/'+job['job_id']+'/lease',headers=headers).status_code,409)
            self.assertEqual(self.result(job,headers).status_code,409)
            self.assertEqual(self.client.post('/api/processing/jobs/'+job['job_id']+'/cleanup',headers=self.node).status_code,200)
        with get_connection() as conn:
            row=conn.execute('SELECT status,local_cleanup_status FROM processing_job WHERE job_id=?',(job['job_id'],)).fetchone()
            self.assertEqual((row['status'],row['local_cleanup_status']),('cancelled','confirmed'))
            self.assertEqual(conn.execute('SELECT status FROM event_media WHERE media_id=?',(media['media_id'],)).fetchone()[0],'failed')
        self.expire(time.time()+300)
        self.assertEqual(self.client.post('/api/media/'+media['media_id']+'/redact',headers=self.admin).status_code,202)

    def test_failed_media_cannot_reprocess_remotely_or_locally(self):
        from backend.media.service import reprocess_media,get_media
        media=self.upload();job,headers=self.claim()
        self.client.post('/api/processing/jobs/'+job['job_id']+'/failure',headers=headers,json={'error_code':'media_processing_failed'}).raise_for_status()
        self.client.post('/api/processing/jobs/'+job['job_id']+'/cleanup',headers=self.node).raise_for_status()
        self.expire()
        response=self.client.post('/api/media/'+media['media_id']+'/redact',headers=self.admin)
        self.assertEqual(response.status_code,409)
        with patch('backend.media.service.redact_image') as redact:
            with self.assertRaisesRegex(ValueError,'event_media_expired'):reprocess_media(media['media_id'])
            redact.assert_not_called()
        public=get_media(media['media_id'])
        self.assertFalse(public['can_reprocess']);self.assertEqual(public['reprocess_unavailable_reason'],'event_media_expired')
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM processing_job WHERE media_id=?',(media['media_id'],)).fetchone()[0],1)

    def test_expired_new_ingest_and_enqueue_are_rejected(self):
        from backend.processing.service import enqueue
        media=self.upload();self.expire()
        response=self.client.post('/api/events/EVT_0001/media?kind=image',headers=self.admin,content=self.image)
        self.assertEqual(response.status_code,409)
        with self.assertRaisesRegex(ValueError,'event_media_expired'):enqueue(media['media_id'])

    def test_expired_old_generations_do_not_overwrite_new_processing_state(self):
        first=self.upload();job,headers=self.claim();second=self.upload()
        with get_connection() as conn:
            for media in (first,second):
                conn.execute("UPDATE event_media SET processing_generation=processing_generation+1,error_code='new-generation-sentinel' WHERE media_id=?",(media['media_id'],))
        self.expire()
        self.assertEqual(self.client.post('/api/processing/jobs/'+job['job_id']+'/cleanup',headers=self.node).status_code,200)
        self.assertIsNone(self.client.post('/api/processing/claim',headers=self.node).json()['job'])
        with get_connection() as conn:
            for media in (first,second):
                row=conn.execute('SELECT status,error_code FROM event_media WHERE media_id=?',(media['media_id'],)).fetchone()
                self.assertEqual((row['status'],row['error_code']),('processing','new-generation-sentinel'))

    def test_future_retention_still_allows_private_source_and_processing(self):
        self.upload();self.expire(time.time()+300)
        job,headers=self.claim()
        self.assertEqual(self.client.get(job['source_url'],headers=headers).content,self.image)
        self.assertEqual(self.result(job,headers).status_code,200)
