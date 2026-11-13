from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import cv2
import numpy as np
from inference_worker.capture_outbox import CaptureOutbox
from inference_worker.private_chunk_decoder import PrivateChunkDecoder,PrivateDecodeError


class PrivateDecoderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.now=[100.]
        self.owner=CaptureOutbox(self.root/'state',private_root=self.root/'raw',max_db_bytes=1_000_000,
            max_records=100,max_body_bytes=4096,source_ttl_seconds=10,clock=lambda:self.now[0])
        self.decoder=None
        path=self.root/'synthetic.mp4'
        writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),10,(16,16))
        if not writer.isOpened():self.skipTest('test MP4 encoder unavailable')
        for _ in range(10):writer.write(np.zeros((16,16,3),np.uint8))
        writer.release();self.data=path.read_bytes();path.unlink()
        self.decoder=self.open()

    def open(self,**changes):
        cfg=dict(max_bytes=100_000,max_frames=20,max_frame_pixels=256,min_fps=1,max_fps=60,
            duration_tolerance_ms=10,ttl_seconds=10,max_audit_records=10,max_metadata_bytes=100_000)
        cfg.update(changes)
        return PrivateChunkDecoder(self.owner,**cfg)

    def tearDown(self):
        if self.decoder:self.decoder.close()
        self.owner.close();self.temp.cleanup()

    def test_normal_timed_frames_private_cleanup(self):
        rows=list(self.decoder(self.data,with_timing=True,duration_ms=1000))
        self.assertEqual(len(rows),10);self.assertEqual(rows[-1][0],9)
        self.assertEqual(rows[0][1],10)
        self.assertFalse(list(self.decoder.root.glob('*.mp4')))
        audit=self.decoder.audit();self.assertEqual(audit[0]['state'],'done')
        self.assertEqual(audit[0]['outcome'],'decoded')
        self.decoder.acknowledge_audit([audit[0]['id']]);self.assertFalse(self.decoder.audit())

    def test_killed_generator_restart_known_source_cleanup(self):
        gen=self.decoder.decode(self.data);next(gen)
        path=next(self.decoder.root.glob('*.mp4'));self.assertTrue(path.exists())
        self.decoder.capture.release();self.decoder.capture=None
        self.decoder.db.close();self.decoder.closed=True # emulate process death, bypass finally cleanup
        gen.close()
        self.decoder=self.open()
        self.assertFalse(path.exists())
        self.assertEqual(self.decoder.audit()[0]['outcome'],'restart_interrupted')

    def test_idle_generator_ttl_cleanup(self):
        gen=self.decoder.decode(self.data);next(gen);self.now[0]=110
        self.decoder.maintain()
        self.assertFalse(list(self.decoder.root.glob('*.mp4')))
        self.assertEqual(self.decoder.audit()[0]['outcome'],'privacy_ttl_expired')
        with self.assertRaises(PrivateDecodeError):next(gen)

    def test_unlink_failure_retry_and_audit_bound(self):
        original=Path.unlink
        def fail(path,*a,**kw):
            if path.suffix=='.mp4':raise PermissionError('busy')
            return original(path,*a,**kw)
        with patch.object(Path,'unlink',fail):
            with self.assertRaises(PermissionError):list(self.decoder(self.data))
        self.assertEqual(self.decoder.audit()[0]['state'],'cleanup_pending')
        self.decoder.maintain()
        self.assertFalse(list(self.decoder.root.glob('*.mp4')))
        self.assertEqual(self.decoder.audit()[0]['state'],'done')

    def test_unknown_raw_file_and_symlink_not_deleted(self):
        self.decoder.close()
        path=self.decoder.root/(uuid4().hex+'.mp4');path.write_bytes(b'unknown')
        with self.assertRaises(PrivateDecodeError):self.open()
        self.assertTrue(path.exists());path.unlink()
        path.symlink_to(self.root/'nonexistent')
        with self.assertRaises(PrivateDecodeError):self.open()
        self.assertTrue(path.is_symlink());path.unlink()

    def test_forged_manifest_path_does_not_delete_external_file(self):
        self.decoder.close()
        external=self.root/'precious.mp4';external.write_bytes(b'keep')
        db=sqlite3.connect(self.decoder.root/'ownership.sqlite3')
        db.execute("INSERT INTO files(id,expected_bytes,digest,created,state) VALUES('../precious',4,'x',100,'ready')")
        db.commit();db.close()
        with self.assertRaises(PrivateDecodeError):self.open()
        self.assertEqual(external.read_bytes(),b'keep')

    def test_frame_byte_and_audit_limits(self):
        with self.assertRaises(ValueError):list(self.decoder(b'x'*100001))
        self.decoder.max_frames=2
        with self.assertRaises(PrivateDecodeError):list(self.decoder(self.data))
        self.assertFalse(list(self.decoder.root.glob('*.mp4')))
        self.decoder.max_frames=20;self.decoder.max_records=1
        with self.assertRaises(PrivateDecodeError):list(self.decoder(self.data))

    def test_write_failure_is_audited_and_cleaned(self):
        with patch('inference_worker.private_chunk_decoder.os.write',side_effect=OSError('disk error')):
            with self.assertRaises(OSError):list(self.decoder(self.data))
        self.assertFalse(list(self.decoder.root.glob('*.mp4')))
        self.assertEqual(self.decoder.audit()[0]['outcome'],'decode_failed')

    def test_same_owner_second_decoder_rejected(self):
        with self.assertRaises(PrivateDecodeError):self.open()
