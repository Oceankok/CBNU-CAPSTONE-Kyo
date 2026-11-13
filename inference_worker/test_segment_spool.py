from pathlib import Path
from tempfile import TemporaryDirectory
import json
import shutil
import subprocess
import sys
import time
from unittest.mock import patch
import unittest

import cv2
import numpy as np

from inference_worker.segment_spool import SegmentSpool, TimestampSegmentWriter, SpoolError


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required for synthetic MP4 tests')
class SegmentSpoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name) / 'private'
        self.now = [100.0]
        self.frame = np.zeros((48, 64, 3), np.uint8)

    def tearDown(self):
        self.temp.cleanup()

    def spool(self, **kwargs):
        config = dict(max_bytes=2_000_000, max_segments=8, max_age_seconds=60,
                      max_attempts=3, clock=lambda: self.now[0])
        config.update(kwargs)
        return SegmentSpool(self.root, **config)

    def writer(self, spool, **kwargs):
        config = dict(segment_seconds=2, output_fps=10, max_frame_pixels=64*48,
                      max_input_gap_seconds=.5, encoder_timeout_seconds=5)
        config.update(kwargs)
        return TimestampSegmentWriter(spool, **config)

    def create(self, spool, seconds=1):
        writer = self.writer(spool)
        rows = []
        for i in range(seconds * 10):
            rows.extend(writer.push(i / 10, self.frame))
        rows.extend(writer.finish(seconds))
        return rows

    def assert_mp4(self, spool, row):
        cap = cv2.VideoCapture(str(spool.path_for_upload(row.segment_id)))
        try:
            self.assertTrue(cap.isOpened())
            self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), row.frame_count)
            self.assertAlmostEqual(cap.get(cv2.CAP_PROP_FPS), row.fps)
            count = 0
            while cap.read()[0]:
                count += 1
            self.assertEqual(count, row.frame_count)
        finally:
            cap.release()

    def test_five_seconds_split_without_new_event_or_frame_loss(self):
        with self.spool() as spool:
            rows = self.create(spool, 5)
            self.assertEqual([r.frame_count for r in rows], [20,20,10])
            self.assertEqual([(r.capture_start,r.capture_end) for r in rows], [(0,2),(2,4),(4,5)])
            self.assertEqual([r.sequence for r in rows], [0,1,2])
            self.assertLessEqual(spool.bytes_used, spool.max_bytes)
            for row in rows:
                self.assert_mp4(spool,row)

    def test_exact_sixty_second_complete_file_not_sixty_plus_padding(self):
        with self.spool() as spool:
            writer = self.writer(spool, segment_seconds=60)
            rows = []
            for i in range(610):
                rows.extend(writer.push(i/10,self.frame))
            rows.extend(writer.finish(61))
            self.assertEqual([r.frame_count for r in rows], [600,10])
            self.assertEqual([r.capture_end-r.capture_start for r in rows], [60,1])
            for row in rows:
                self.assert_mp4(spool,row)

    def test_variable_source_fps_preserves_time_at_fixed_output_rate(self):
        with self.spool() as spool:
            writer = self.writer(spool, segment_seconds=60, output_fps=15)
            rows=[]
            for ts in [i/15 for i in range(75)]+[5+i/10 for i in range(20)]:
                rows.extend(writer.push(ts,self.frame))
            rows.extend(writer.finish(7))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0].frame_count,105)
            self.assertEqual(rows[0].capture_end,7)
            self.assert_mp4(spool,rows[0])

    def test_quota_caps_media_and_metadata_then_discards_partial(self):
        with self.spool(max_bytes=25000, max_segments=2) as spool:
            writer=self.writer(spool)
            rng=np.random.default_rng(123)
            with self.assertRaises(SpoolError):
                for i in range(100):
                    writer.push(i/10,rng.integers(0,256,self.frame.shape,dtype=np.uint8))
                writer.finish(10)
            self.assertLessEqual(spool.bytes_used,spool.max_bytes)
            self.assertFalse(list(self.root.glob('*.part')))

    def test_reopen_preserves_complete_and_removes_partial_and_orphan(self):
        with self.spool() as spool:
            row=self.create(spool)[0]
        partial=self.root/('a'*32+'.part')
        orphan=self.root/('b'*32+'.mp4')
        partial.write_bytes(b'partial')
        orphan.write_bytes(b'uncommitted')
        with self.spool() as spool:
            self.assertFalse(partial.exists())
            self.assertFalse(orphan.exists())
            self.assertEqual([r.segment_id for r in spool.pending()],[row.segment_id])
            self.assert_mp4(spool,spool.pending()[0])

    def test_success_removes_raw_and_duplicate_ack_is_safe(self):
        with self.spool() as spool:
            row=self.create(spool)[0]
            path=spool.mark_attempt(row.segment_id)
            self.assertTrue(path.exists())
            spool.acknowledge(row.segment_id)
            spool.acknowledge(row.segment_id)
            self.assertFalse(path.exists())
            self.assertEqual(spool.pending(),[])

    def test_attempt_limit_survives_restart(self):
        with self.spool(max_attempts=1) as spool:
            row=self.create(spool)[0]
            path=spool.mark_attempt(row.segment_id)
            self.assertTrue(path.exists())
        with self.spool(max_attempts=1) as spool:
            self.assertFalse(path.exists())
            self.assertFalse(spool.pending())

    def test_expiry_and_segment_count_limit(self):
        with self.spool(max_segments=1) as spool:
            row=self.create(spool)[0]
            with self.assertRaisesRegex(SpoolError,'count_limit'):
                self.create(spool)
            self.now[0]+=60
            self.assertEqual(spool.prune(),[row.segment_id])
            self.assertFalse(list(self.root.glob('*.mp4')))

    def test_active_lock_prevents_cleanup_by_second_owner(self):
        with self.spool():
            with self.assertRaisesRegex(SpoolError,'in_use'):
                self.spool()

    def test_unknown_directory_and_symlink_rejected_without_deletion(self):
        self.root.mkdir()
        other=self.root/'other.txt'
        other.write_text('keep')
        with self.assertRaisesRegex(SpoolError,'unrecognized'):
            self.spool()
        self.assertTrue(other.exists())

    def test_gap_aborts_active_source_instead_of_fabricating_long_hold(self):
        with self.spool() as spool:
            writer=self.writer(spool)
            writer.push(0,self.frame)
            writer.push(.1,self.frame)
            with self.assertRaisesRegex(SpoolError,'input_gap'):
                writer.push(2,self.frame)
            self.assertFalse(list(self.root.glob('*.part')))
            self.assertFalse(spool.pending())

    def test_only_one_frame_retained_and_invalid_resolution_rejected(self):
        with self.spool() as spool:
            writer=self.writer(spool)
            writer.push(0,self.frame)
            self.frame[:]=255
            self.assertFalse(writer.previous.any())
            self.assertEqual(writer.previous.nbytes,48*64*3)
            with self.assertRaises(ValueError):
                writer.push(.1,np.zeros((480,640,3),np.uint8))
            writer.abort()

    def test_non_grid_end_is_rejected_not_rounded_up_beyond_evidence(self):
        with self.spool() as spool:
            writer=self.writer(spool)
            writer.push(0,self.frame)
            with self.assertRaisesRegex(ValueError,'frame grid'):
                writer.finish(.15)
            writer.abort()

    def test_last_attempt_not_removed_by_new_segment_while_inflight(self):
        with self.spool(max_attempts=1) as spool:
            first=self.create(spool)[0]
            path=spool.mark_attempt(first.segment_id)
            self.create(spool)
            self.assertTrue(path.exists())
            self.assertNotIn(first.segment_id,[r.segment_id for r in spool.pending()])
            spool.mark_failed(first.segment_id)
            self.assertFalse(path.exists())

    def test_active_partial_age_limit_is_enforced(self):
        with self.spool() as spool:
            writer=self.writer(spool)
            writer.push(0,self.frame)
            writer.push(.1,self.frame)
            self.now[0]+=61
            with self.assertRaisesRegex(SpoolError,'expired'):
                writer.push(.2,self.frame)
            self.assertFalse(list(self.root.glob('*.part')))

    def test_stalled_encoder_stdin_times_out_without_blocking_cleanup(self):
        from inference_worker.segment_spool import _BoundedEncoder
        real_popen = subprocess.Popen
        def stalled(*args, **kwargs):
            return real_popen([sys.executable, '-c', 'import time; time.sleep(20)'],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, bufsize=0)
        self.root.mkdir()
        path=self.root/'stall.part'
        with patch('inference_worker.segment_spool.subprocess.Popen', side_effect=stalled):
            encoder=_BoundedEncoder(path,640,480,15,1_000_000,.3)
            started=time.monotonic()
            try:
                with self.assertRaisesRegex(SpoolError,'write_timeout'):
                    encoder.write(np.zeros((480,640,3),np.uint8))
                self.assertLess(time.monotonic()-started,2)
            finally:
                encoder.abort()
                path.unlink(missing_ok=True)
            self.assertIsNotNone(encoder.process.poll())
            self.assertFalse(encoder.reader.is_alive())

    def test_failed_delete_keeps_journal_for_bounded_retry(self):
        with self.spool() as spool:
            row=self.create(spool)[0]
            target=spool.path_for_upload(row.segment_id)
            real_unlink=Path.unlink
            def deny_target(path, *args, **kwargs):
                if path == target:
                    raise PermissionError('synthetic locked source')
                return real_unlink(path,*args,**kwargs)
            with patch.object(Path,'unlink',deny_target):
                with self.assertRaises(PermissionError):
                    spool.acknowledge(row.segment_id)
            self.assertIn(row.segment_id,spool.entries)
            self.assertTrue(target.exists())
            spool.acknowledge(row.segment_id)
            self.assertFalse(target.exists())

    def test_clock_rollback_does_not_extend_retention(self):
        with self.spool() as spool:
            row=self.create(spool)[0]
            self.now[0]-=1
            self.assertEqual(spool.prune(),[row.segment_id])

    def test_cleanup_pending_survives_restart_and_never_reuploads(self):
        with self.spool(max_attempts=3) as spool:
            row=self.create(spool)[0]
            target=spool.path_for_upload(row.segment_id)
            real_unlink=Path.unlink
            def deny_target(path, *args, **kwargs):
                if path == target:
                    raise PermissionError('synthetic locked source')
                return real_unlink(path,*args,**kwargs)
            with patch.object(Path,'unlink',deny_target):
                with self.assertRaises(PermissionError):
                    spool.acknowledge(row.segment_id)
            journal=json.loads((self.root/'journal.json').read_text())
            self.assertEqual(journal['cleanup_pending'],[row.segment_id])
        with self.spool() as recovered:
            self.assertFalse(target.exists())
            self.assertEqual(recovered.pending(),[])
            self.assertEqual(recovered.discard_receipts()['items'],[])

    def test_mark_attempt_and_prune_are_atomic_under_same_object_threads(self):
        import threading
        with self.spool(max_attempts=1) as spool:
            row=self.create(spool)[0]
            saved=threading.Event()
            release=threading.Event()
            pruned=threading.Event()
            failures=[]
            real_save=spool._save
            def delayed_save():
                real_save()
                saved.set()
                self.assertTrue(release.wait(2))
            def attempt():
                try:
                    spool.mark_attempt(row.segment_id)
                except BaseException as exc:
                    failures.append(exc)
            def prune():
                try:
                    spool.prune()
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    pruned.set()
            with patch.object(spool,'_save',delayed_save):
                a=threading.Thread(target=attempt)
                a.start()
                self.assertTrue(saved.wait(2))
                b=threading.Thread(target=prune)
                b.start()
                self.assertFalse(pruned.wait(.05))
                release.set()
                a.join(2); b.join(2)
            self.assertFalse(a.is_alive() or b.is_alive())
            self.assertFalse(failures)
            self.assertTrue((self.root/(row.segment_id+'.mp4')).exists())
            self.assertIn(row.segment_id,spool._inflight)

    def test_discard_receipts_survive_restart_and_ack(self):
        with self.spool(max_attempts=1) as spool:
            row=self.create(spool)[0]
            spool.mark_attempt(row.segment_id)
            spool.mark_failed(row.segment_id)
            receipt=spool.discard_receipts()['items'][0]
            self.assertEqual(receipt['segment_id'],row.segment_id)
            self.assertEqual(receipt['segment_sequence'],row.sequence)
            self.assertEqual(receipt['reason'],'attempts_exhausted')
        with self.spool() as spool:
            self.assertEqual(spool.discard_receipts()['items'],[receipt])
            spool.acknowledge_discards([receipt['receipt_sequence']])
            self.assertEqual(spool.discard_receipts()['items'],[])

    def test_restart_partial_orphan_and_missing_have_disposition_receipts(self):
        with self.spool() as spool:
            row=self.create(spool)[0]
        (self.root/(row.segment_id+'.mp4')).unlink()
        (self.root/('a'*32+'.part')).write_bytes(b'x')
        (self.root/('b'*32+'.mp4')).write_bytes(b'y')
        with self.spool() as spool:
            receipts=spool.discard_receipts()['items']
            self.assertEqual({r['reason'] for r in receipts},
                             {'restart_partial','restart_orphan','source_missing'})
            self.assertEqual({r['segment_id'] for r in receipts},{row.segment_id,'a'*32,'b'*32})

    def test_discard_overflow_is_durable_and_requires_reconciliation(self):
        with self.spool(max_segments=1) as spool:
            for _ in range(3):
                self.create(spool)
                self.now[0]+=61
                spool.prune()
            result=spool.discard_receipts()
            self.assertEqual(len(result['items']),2)
            self.assertEqual(result['overflow_count'],1)
            self.assertTrue(result['reconcile_required'])
        with self.spool(max_segments=1) as spool:
            self.assertTrue(spool.discard_receipts()['reconcile_required'])
            spool.acknowledge_discards([],reconciled_overflow=True)
            self.assertFalse(spool.discard_receipts()['reconcile_required'])

    def test_expiry_delete_failure_persists_unusable_disposition_before_unlink(self):
        with self.spool() as spool:
            row=self.create(spool)[0]
            target=spool.path_for_upload(row.segment_id)
            self.now[0]+=61
            real_unlink=Path.unlink
            def deny_target(path, *args, **kwargs):
                if path == target:
                    raise PermissionError('synthetic source lock')
                return real_unlink(path,*args,**kwargs)
            with patch.object(Path,'unlink',deny_target):
                with self.assertRaises(PermissionError):
                    spool.prune()
            disk=json.loads((self.root/'journal.json').read_text())
            self.assertIn(row.segment_id,disk['cleanup_pending'])
            self.assertEqual(disk['discard_receipts'][0]['reason'],'source_expired')
            self.assertTrue(target.exists())
        with self.spool() as spool:
            self.assertFalse(target.exists())
            self.assertFalse(spool.pending())
            self.assertEqual(len(spool.discard_receipts()['items']),1)
