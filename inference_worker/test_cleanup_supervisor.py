from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import numpy as np
from inference_worker.capture_outbox import CaptureOutbox
from inference_worker.cleanup_supervisor import CleanupSupervisor
from inference_worker.segment_spool import SegmentSpool, TimestampSegmentWriter


@unittest.skipUnless(shutil.which('ffmpeg'),'ffmpeg required')
class CleanupSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.now=[100.];self.spools={};self.verified=True
        self.box=self.open_box()
        self.box.create_event('e',camera_snapshot=['main','ref'])

    def open_box(self):
        return CaptureOutbox(self.root/'ledger',private_root=self.root/'raw',
            max_db_bytes=1_000_000,max_records=100,max_body_bytes=4096,
            source_ttl_seconds=10,clock=lambda:self.now[0])

    def tearDown(self):
        for spool in self.spools.values():spool.close()
        self.box.close();self.temp.cleanup()

    def source(self,camera='main'):
        folder=uuid4().hex
        self.box.begin_capture('e',camera=camera,spool_directory=folder)
        spool=SegmentSpool(self.root/'raw'/folder,max_bytes=100_000,max_segments=4,
            max_age_seconds=100,max_attempts=2,clock=lambda:self.now[0])
        self.spools[folder]=spool
        writer=TimestampSegmentWriter(spool,segment_seconds=1,output_fps=2,
            max_frame_pixels=256,max_input_gap_seconds=1,encoder_timeout_seconds=5)
        writer.push(0,np.zeros((16,16,3),np.uint8));seg=writer.finish(.5)[0]
        self.box.bind_segment('e',camera=camera,segment=seg,relative_path=folder+'/'+seg.segment_id+'.mp4')
        return spool,seg,spool.root/(seg.segment_id+'.mp4')

    def run_cleanup(self):
        return CleanupSupervisor(self.box,spools=self.spools,
            verified_receipt=lambda row:self.verified).run_once()

    def test_verified_delivery_two_camera_and_duplicate(self):
        sources=[self.source('main'),self.source('ref')]
        for _,seg,_ in sources:self.box.request_cleanup(seg.segment_id,reason='verified_upload_receipt')
        report=self.run_cleanup()
        self.assertEqual(len(report['cleaned']),2)
        self.assertTrue(all(r['outcome']=='verified_private_delivery' for r in report['cleaned']))
        self.assertTrue(all(not path.exists() for _,_,path in sources))
        self.assertEqual(self.box.event_status('e')['state'],'active')
        self.assertFalse(self.run_cleanup()['cleaned'])

    def test_unknown_custody_preserves_raw(self):
        _,seg,path=self.source();self.verified=False
        self.box.request_cleanup(seg.segment_id,reason='verified_upload_receipt')
        report=self.run_cleanup()
        self.assertTrue(path.exists());self.assertFalse(report['cleaned'])
        self.assertEqual(report['held'][0]['reason'],'private_custody_not_verified')

    def test_ttl_discard_is_incomplete_not_delivery(self):
        spool,seg,path=self.source();self.now[0]=110;self.verified=False
        report=self.run_cleanup()
        self.assertFalse(path.exists())
        self.assertEqual(self.box.event_status('e')['state'],'incomplete')
        self.assertEqual(self.box.segment_status(seg.segment_id)['reason'],'source_expired_or_clock_rollback')
        self.assertEqual(report['cleaned'][0]['outcome'],'incomplete_privacy_or_failure_discard')
        self.assertFalse(spool.discard_receipts()['items'])

    def test_unlink_failure_retry_keeps_audit(self):
        _,seg,path=self.source();self.now[0]=110
        original=Path.unlink
        def denied(p,*args,**kwargs):
            if p==path:raise PermissionError('busy')
            return original(p,*args,**kwargs)
        with patch.object(Path,'unlink',denied):
            report=self.run_cleanup()
        self.assertTrue(path.exists());self.assertTrue(report['held'])
        self.assertEqual(self.box.segment_status(seg.segment_id)['cleanup'],1)
        self.assertEqual(self.box.event_status('e')['state'],'incomplete')
        self.assertEqual(len(self.run_cleanup()['cleaned']),1)

    def test_restart_after_delivery_intent(self):
        spool,seg,path=self.source()
        self.box.request_cleanup(seg.segment_id,reason='verified_upload_receipt')
        self.box.close();self.box=self.open_box()
        self.assertEqual(len(self.run_cleanup()['cleaned']),1)
        self.assertFalse(path.exists())

    def test_orphan_and_aggregate_inventory_hold(self):
        spool,seg,path=self.source()
        orphan=spool.root/(uuid4().hex+'.part');orphan.write_bytes(b'orphan')
        self.box.request_cleanup(seg.segment_id,reason='verified_upload_receipt')
        self.box.close();self.box=self.open_box()
        report=self.run_cleanup()
        self.assertTrue(report['recovery_required'])
        self.assertTrue(path.exists());self.assertTrue(orphan.exists())
        self.assertFalse(report['cleaned'])
        orphan.unlink()  # isolated synthetic fixture, not supervisor behavior

    def test_unknown_manifest_and_changed_source_hold(self):
        spool,seg,path=self.source();path.write_bytes(b'changed')
        self.now[0]=110
        report=self.run_cleanup()
        self.assertTrue(path.exists());self.assertTrue(report['held'])
        self.assertEqual(self.box.event_status('e')['state'],'incomplete')

    def test_audit_failure_prevents_deletion(self):
        _,_,path=self.source();self.now[0]=110
        with patch.object(self.box,'reconcile',side_effect=RuntimeError('journal full')):
            with self.assertRaises(RuntimeError):self.run_cleanup()
        self.assertTrue(path.exists())

    def test_missing_source_acknowledges_only_incomplete(self):
        spool,seg,path=self.source();path.unlink()
        report=self.run_cleanup()
        self.assertEqual(report['cleaned'][0]['outcome'],'incomplete_privacy_or_failure_discard')
        self.assertEqual(self.box.segment_status(seg.segment_id)['reason'],'source_missing')
        self.assertNotEqual(self.box.segment_status(seg.segment_id)['state'],'uploaded_private')

    def test_unknown_claimed_custody_expires_as_incomplete(self):
        _,seg,path=self.source()
        self.box.request_cleanup(seg.segment_id,reason='verified_upload_receipt')
        self.verified=False;self.now[0]=110
        report=self.run_cleanup()
        self.assertFalse(path.exists())
        self.assertEqual(report['cleaned'][0]['outcome'],'incomplete_privacy_or_failure_discard')
        self.assertEqual(self.box.event_status('e')['state'],'incomplete')
        self.assertEqual(self.box.segment_status(seg.segment_id)['state'],'unavailable')

    def test_receipt_drain_failure_keeps_intent_until_restart_retry(self):
        spool,seg,path=self.source();self.now[0]=110
        with patch.object(spool,'acknowledge_discards',side_effect=OSError('journal write failed')):
            report=self.run_cleanup()
        self.assertTrue(report['held'])
        self.assertFalse(path.exists())
        self.assertEqual(self.box.segment_status(seg.segment_id)['cleanup'],1)
        self.assertTrue(spool.discard_receipts()['items'])
        folder=spool.root.name
        spool.close();self.box.close()
        self.box=self.open_box()
        self.spools[folder]=SegmentSpool(self.root/'raw'/folder,max_bytes=100_000,max_segments=4,
            max_age_seconds=100,max_attempts=2,clock=lambda:self.now[0])
        report=self.run_cleanup()
        self.assertEqual(len(report['cleaned']),1)
        self.assertEqual(self.box.segment_status(seg.segment_id)['cleanup'],0)
        self.assertFalse(self.spools[folder].discard_receipts()['items'])
        self.assertFalse(self.run_cleanup()['cleaned'])

    def test_ledger_completion_failure_after_receipt_drain_retries(self):
        spool,seg,path=self.source();self.now[0]=110
        with patch.object(self.box,'acknowledge_cleanup',side_effect=OSError('database write failed')):
            report=self.run_cleanup()
        self.assertTrue(report['held'])
        self.assertFalse(path.exists())
        self.assertFalse(spool.discard_receipts()['items'])
        self.assertEqual(self.box.segment_status(seg.segment_id)['cleanup'],1)
        self.assertEqual(len(self.run_cleanup()['cleaned']),1)
        self.assertEqual(self.box.segment_status(seg.segment_id)['cleanup'],0)

    def partial_discard(self):
        folder=uuid4().hex
        self.box.begin_capture('e',camera='ref',spool_directory=folder)
        spool=SegmentSpool(self.root/'raw'/folder,max_bytes=100_000,max_segments=4,
            max_age_seconds=100,max_attempts=2,clock=lambda:self.now[0])
        self.spools[folder]=spool
        writer=TimestampSegmentWriter(spool,segment_seconds=2,output_fps=2,
            max_frame_pixels=256,max_input_gap_seconds=1,encoder_timeout_seconds=5)
        frame=np.zeros((16,16,3),np.uint8)
        writer.push(0,frame);writer.push(.5,frame)
        writer.abort(reason='input_gap')
        return folder,spool

    def test_unbound_partial_receipt_joins_owner_before_ack(self):
        folder,spool=self.partial_discard()
        receipt=spool.discard_receipts()['items'][0]
        report=self.run_cleanup()
        self.assertEqual(report['receipts_joined'],[receipt['segment_id']])
        self.assertFalse(spool.discard_receipts()['items'])
        self.assertEqual(self.box.event_status('e')['state'],'incomplete')
        joined=self.box.spool_dispositions()
        self.assertEqual(len(joined),1)
        self.assertEqual((joined[0]['event'],joined[0]['camera'],joined[0]['spool']),('e','ref',folder))
        self.assertFalse(self.run_cleanup()['receipts_joined'])

    def test_partial_join_audit_full_keeps_receipt(self):
        _,spool=self.partial_discard();self.box.limit=2
        report=self.run_cleanup()
        self.assertTrue(report['held'])
        self.assertTrue(spool.discard_receipts()['items'])
        self.assertFalse(self.box.spool_dispositions())
        self.box.limit=100
        self.assertEqual(len(self.run_cleanup()['receipts_joined']),1)

    def test_partial_receipt_ack_failure_retries_exact_durable_join(self):
        _,spool=self.partial_discard()
        with patch.object(spool,'acknowledge_discards',side_effect=OSError('disk failure')):
            report=self.run_cleanup()
        self.assertTrue(report['held']);self.assertEqual(len(self.box.spool_dispositions()),1)
        self.assertTrue(spool.discard_receipts()['items'])
        self.box.close();self.box=self.open_box()
        self.assertEqual(len(self.run_cleanup()['receipts_joined']),1)
        self.assertEqual(len(self.box.spool_dispositions()),1)
        self.assertFalse(spool.discard_receipts()['items'])

    def test_unknown_partial_owner_receipt_is_not_dropped(self):
        folder,spool=self.partial_discard()
        with self.box.transaction():self.box.db.execute('DELETE FROM captures WHERE spool=?',(folder,))
        report=self.run_cleanup()
        self.assertTrue(report['held']);self.assertTrue(spool.discard_receipts()['items'])
        self.assertFalse(self.box.spool_dispositions())
