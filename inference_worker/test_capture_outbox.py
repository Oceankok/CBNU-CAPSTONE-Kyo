import json
from pathlib import Path
import shutil
import tempfile
import unittest
from uuid import uuid4
import numpy as np
from inference_worker.capture_outbox import CaptureOutbox, OutboxError
from inference_worker.segment_spool import Segment, SegmentSpool, TimestampSegmentWriter


class CaptureOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.now=[100.]
        self.box=self.open()

    def open(self,**changes):
        cfg=dict(private_root=self.base/'raw',max_db_bytes=1_000_000,max_records=100,
                 max_body_bytes=4096,source_ttl_seconds=20,clock=lambda:self.now[0])
        cfg.update(changes)
        return CaptureOutbox(self.base/'journal',**cfg)

    def tearDown(self):
        self.box.close();self.temp.cleanup()

    def create(self):
        self.box.create_event('e',camera_snapshot=['main','ref'])

    def enqueue(self,seq=0,**changes):
        cfg=dict(seq=seq,kind='start' if seq==0 else 'progress',idempotency_key='key'+str(seq),body={'seq':seq,'x':1})
        cfg.update(changes)
        return self.box.enqueue('e',**cfg)

    def source(self):
        folder=uuid4().hex;sid=uuid4().hex
        self.box.begin_capture('e',camera='main',spool_directory=folder)
        path=self.base/'raw'/folder/(sid+'.mp4')
        path.parent.mkdir();path.write_bytes(b'test')
        segment=Segment(sid,0,1,2,10,10,4,100)
        self.box.bind_segment('e',camera='main',segment=segment,relative_path=folder+'/'+path.name)
        return path,segment

    def test_restart_exact_retry_and_no_track_identity_reuse(self):
        self.create();body=self.enqueue();self.enqueue(1)
        self.box.close();self.box=self.open()
        self.assertEqual(self.box.event_status('e')['state'],'interrupted')
        self.assertEqual(self.box.pending()[0]['body'],body)
        self.assertEqual(self.enqueue(),body)
        with self.assertRaises(OutboxError): self.create()
        with self.assertRaises(OutboxError): self.enqueue(2)
        self.enqueue(2,kind='incomplete')
        self.box.acknowledge('e',0,receipt={'ok':1})
        self.assertEqual(self.box.pending()[0]['seq'],1)

    def test_request_conflicts_sequence_and_ack(self):
        self.create();self.enqueue()
        with self.assertRaises(OutboxError):self.enqueue(body={'different':True})
        with self.assertRaises(OutboxError):self.enqueue(2)
        self.enqueue(1)
        with self.assertRaises(OutboxError):self.box.acknowledge('e',1,receipt={})
        self.box.acknowledge('e',0,receipt={'ok':1})
        self.box.acknowledge('e',0,receipt={'ok':1})
        with self.assertRaises(OutboxError):self.box.acknowledge('e',0,receipt={'ok':2})

    def test_missing_source_recovery_and_durable_cleanup(self):
        self.create();path,segment=self.source();path.unlink()
        self.box.close();self.box=self.open()
        self.assertEqual(self.box.event_status('e')['reason'],'source_missing')
        self.assertEqual(self.box.cleanup_obligations()[0]['id'],segment.segment_id)
        self.box.acknowledge_cleanup(segment.segment_id)
        self.assertFalse(self.box.cleanup_obligations())

    def test_size_and_ttl_reconcile(self):
        self.create();path,segment=self.source();path.write_bytes(b'wrong-size')
        self.box.reconcile()
        self.assertIn('size',self.box.event_status('e')['reason'])
        with self.assertRaises(OutboxError):self.box.acknowledge_cleanup(segment.segment_id)
        with self.assertRaises(OutboxError):self.box.request_cleanup(segment.segment_id,reason='verified_upload_receipt')

    def test_ttl(self):
        self.create();self.source();self.now[0]=120;self.box.reconcile()
        self.assertEqual(self.box.event_status('e')['reason'],'source_expired_or_clock_rollback')
        self.assertEqual(len(self.box.cleanup_obligations()),1)

    def test_private_path_validation(self):
        self.create();path,segment=self.source()
        for bad in ('/tmp/file.mp4','../outside.mp4',str(path),'a/../../file'):
            with self.assertRaises(OutboxError):self.box.bind_segment('e',camera='main',segment=segment,relative_path=bad)
        path.unlink();path.symlink_to(self.base/'elsewhere')
        self.box.reconcile()
        self.assertEqual(self.box.event_status('e')['reason'],'source_unsafe_or_inaccessible')
        with self.assertRaises(OutboxError):self.box.acknowledge_cleanup(segment.segment_id)

    def test_lock_excludes_other_owner(self):
        with self.assertRaises(OSError): self.open()

    def test_record_and_body_capacity(self):
        self.box.close();self.box=self.open(max_records=2,max_body_bytes=100)
        self.create();self.enqueue()
        with self.assertRaises(OutboxError):self.enqueue(1)
        self.assertEqual(self.box.event_status('e')['next_seq'],1)
        with self.assertRaises(OutboxError):self.enqueue(body={'x':'a'*200})

    def test_recovery_before_first_start_has_incomplete_obligation(self):
        self.create();self.box.close();self.box=self.open()
        self.enqueue(kind='incomplete')
        self.assertEqual(self.box.pending()[0]['kind'],'incomplete')

    @unittest.skipUnless(shutil.which('ffmpeg'),'ffmpeg required')
    def test_real_spool_mapping_cleanup_roundtrip(self):
        self.create();folder=uuid4().hex
        self.box.begin_capture('e',camera='main',spool_directory=folder)
        spool=SegmentSpool(self.base/'raw'/folder,max_bytes=100_000,max_segments=4,
                           max_age_seconds=20,max_attempts=2,clock=lambda:self.now[0])
        try:
            writer=TimestampSegmentWriter(spool,segment_seconds=1,output_fps=2,
                       max_frame_pixels=256,max_input_gap_seconds=1,encoder_timeout_seconds=5)
            writer.push(0,np.zeros((16,16,3),np.uint8))
            segments=writer.finish(.5)
            seg=segments[0]
            self.box.bind_segment('e',camera='main',segment=seg,relative_path=folder+'/'+seg.segment_id+'.mp4')
            self.box.request_cleanup(seg.segment_id,reason='verified_upload_receipt')
            spool.acknowledge(seg.segment_id)
            self.box.acknowledge_cleanup(seg.segment_id)
            self.box.close();self.box=self.open()
            self.assertFalse(self.box.cleanup_obligations())
            self.assertNotEqual(self.box.event_status('e')['reason'],'source_missing')
        finally:spool.close()

    def test_restart_before_segment_binding_recovers_partial_owner(self):
        self.create();folder=uuid4().hex
        self.box.begin_capture('e',camera='ref',spool_directory=folder)
        directory=self.base/'raw'/folder;directory.mkdir()
        part=directory/(uuid4().hex+'.part');part.write_bytes(b'partial')
        mp4=directory/(uuid4().hex+'.mp4');mp4.write_bytes(b'unregistered')
        self.box.close();self.box=self.open()
        obligations=self.box.orphan_cleanup_obligations()
        self.assertEqual(len(obligations),2)
        self.assertTrue(all(row['camera']=='ref' and row['event']=='e' for row in obligations))
        self.assertEqual(self.box.event_status('e')['state'],'incomplete')
        with self.assertRaises(OutboxError):self.box.acknowledge_orphan_cleanup(obligations[0]['path'])
        part.unlink();mp4.unlink()
        for row in obligations:self.box.acknowledge_orphan_cleanup(row['path'])
        self.assertFalse(self.box.orphan_cleanup_obligations())

    def test_source_loss_holds_previously_queued_complete_close(self):
        self.create();path,_=self.source();self.enqueue()
        self.box.acknowledge('e',0,receipt={'ok':1})
        exact=self.enqueue(1,kind='close',body={'complete':True})
        path.unlink();self.box.reconcile()
        self.assertFalse(self.box.pending())
        blocked=self.box.blocked_terminal_requests()
        self.assertEqual(blocked[0]['body'],exact)
        self.assertEqual(blocked[0]['block_reason'],'source_missing')

    def test_recovery_inventory_capacity_keeps_ledger_accessible(self):
        self.box.close();self.box=self.open(max_records=2)
        self.create();folder=uuid4().hex
        self.box.begin_capture('e',camera='main',spool_directory=folder)
        directory=self.base/'raw'/folder;directory.mkdir()
        (directory/(uuid4().hex+'.part')).write_bytes(b'partial')
        self.box.close();self.box=self.open(max_records=2)
        self.assertEqual(self.box.event_status('e')['reason'],'orphan_inventory_capacity')
        self.assertEqual(self.box.recovery_obligations()[0]['spool'],folder)
        self.assertFalse(self.box.pending())

    def test_closed_allows_camera_report_but_not_manifest_or_progress(self):
        self.create();self.enqueue()
        self.enqueue(1,kind='close',body={'end':10})
        self.assertEqual(self.box.event_status('e')['state'],'closed')
        body=self.enqueue(2,kind='capture_report',body={'camera_id':'main','state':'complete'})
        self.assertEqual(self.box.event_status('e')['state'],'closed')
        self.assertEqual(self.box.event_status('e')['next_seq'],3)
        self.assertEqual(self.enqueue(2,kind='capture_report',body={'camera_id':'main','state':'complete'}),body)
        with self.assertRaises(OutboxError):self.enqueue(3,kind='manifest')
        with self.assertRaises(OutboxError):self.enqueue(3,kind='progress')
        self.enqueue(3,kind='capture_report',body={'camera_id':'ref','state':'incomplete'})
        self.assertEqual(self.box.event_status('e')['state'],'closed')

    def test_interrupted_camera_report_not_implicitly_authorized(self):
        self.create();self.enqueue();self.box.close();self.box=self.open()
        with self.assertRaises(OutboxError):self.enqueue(1,kind='capture_report')
        self.enqueue(1,kind='incomplete')
        with self.assertRaises(OutboxError):self.enqueue(2,kind='capture_report')

    def test_incomplete_pending_capture_accepts_good_segment_and_manifest(self):
        self.create();self.enqueue()
        folder=uuid4().hex;sid=uuid4().hex
        self.box.begin_capture('e',camera='main',spool_directory=folder)
        path=self.base/'raw'/folder/(sid+'.mp4');path.parent.mkdir();path.write_bytes(b'test')
        segment=Segment(sid,0,1,2,10,10,4,100)
        self.box.mark_incomplete('e',reason='reference_camera_failed')
        with self.assertRaises(OutboxError):self.box.bind_segment('e',camera='main',segment=segment,relative_path=folder+'/'+path.name)
        with self.box.transaction():self.box.db.execute("INSERT INTO pending_closes VALUES(?,?,?,1,'pending')",('e',self.box.session,b'{}'))
        self.box.bind_segment('e',camera='main',segment=segment,relative_path=folder+'/'+path.name)
        self.box.enqueue('e',seq=1,kind='manifest',idempotency_key='good-main',
                         body={'session_id':self.box.session,'sequence':0})
        self.assertEqual(self.box.segment_status(sid)['state'],'present')
        self.assertEqual(self.box.event_status('e')['state'],'incomplete')
        with self.assertRaises(OutboxError):self.box.enqueue('e',seq=2,kind='manifest',idempotency_key='bad-camera',
                         body={'session_id':self.box.session,'sequence':1,'camera_id':'ref'})
        with self.box.transaction():self.box.db.execute("UPDATE pending_closes SET state='finalized'")
        with self.assertRaises(OutboxError):self.box.enqueue('e',seq=2,kind='manifest',idempotency_key='late',
                         body={'session_id':self.box.session,'sequence':1})
        fresh=Segment(uuid4().hex,1,2,3,10,10,4,100)
        other=path.parent/(fresh.segment_id+'.mp4');other.write_bytes(b'test')
        with self.assertRaises(OutboxError):self.box.bind_segment('e',camera='main',segment=fresh,relative_path=folder+'/'+other.name)

    def test_partial_manifest_rejects_old_session_and_interrupted_owner(self):
        self.create();self.enqueue();folder=uuid4().hex
        self.box.begin_capture('e',camera='main',spool_directory=folder)
        with self.box.transaction():self.box.db.execute("INSERT INTO pending_closes VALUES(?,?,?,1,'pending')",('e',self.box.session,b'{}'))
        self.box.mark_incomplete('e',reason='reference_failed')
        with self.assertRaises(OutboxError):self.box.enqueue('e',seq=1,kind='manifest',idempotency_key='wrongsession',
                         body={'session_id':'0'*32,'sequence':0})
        old=self.box.session;self.box.close();self.box=self.open()
        with self.assertRaises(OutboxError):self.box.enqueue('e',seq=1,kind='manifest',idempotency_key='restart',
                         body={'session_id':old,'sequence':0})
