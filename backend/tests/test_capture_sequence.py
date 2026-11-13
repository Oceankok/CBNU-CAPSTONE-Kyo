import io
import os
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from video_sender.chunks import completed_chunks, next_sequence, publish_completed_segments


class CaptureSequenceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))

    def test_empty_directory_starts_at_zero(self):
        self.assertEqual(next_sequence(self.root), 0)
        self.assertEqual(completed_chunks(self.root), [])

    def test_reserves_mp4_and_timestamp_numbers_only(self):
        for name in ('chunk_00079.mp4', 'chunk_00120.txt', 'chunk_bad.mp4', 'other999.txt'):
            (self.root / name).write_bytes(b'leftover')
        (self.root / 'chunk_99999.mp4').mkdir()
        self.assertEqual(next_sequence(self.root), 121)

    def test_cleanup_intent_reserves_sequence_after_media_removed(self):
        (self.root / 'chunk_00125.cleanup').touch()
        self.assertEqual(next_sequence(self.root), 126)

    def test_numeric_order_excludes_active_tail_after_five_digits(self):
        for name in ('chunk_99998.mp4', 'chunk_99999.mp4', 'chunk_100000.mp4'):
            (self.root / name).write_bytes(b'fixture')
        self.assertEqual([path.name for path in completed_chunks(self.root)],
                         ['chunk_99998.mp4', 'chunk_99999.mp4'])
        self.assertEqual(next_sequence(self.root), 100001)

    def test_incomplete_segments_are_hidden_even_when_later_segment_exists(self):
        for sequence in (20, 21, 22):
            (self.root / f'chunk_{sequence:05d}.mp4.part').write_bytes(b'not finalized')
        manifest = self.root / '.capture.list'
        manifest.write_text('chunk_00020.mp4.part\nchunk_00021.mp4.part', encoding='utf-8')
        publish_completed_segments(self.root, manifest)
        self.assertTrue((self.root / 'chunk_00020.mp4').exists())
        self.assertFalse((self.root / 'chunk_00021.mp4').exists())
        self.assertFalse((self.root / 'chunk_00022.mp4').exists())
        self.assertEqual([p.name for p in completed_chunks(self.root)], ["chunk_00020.mp4"])
        self.assertEqual(next_sequence(self.root), 23)

    def test_publication_retries_shared_file_without_losing_source(self):
        source = self.root / 'chunk_00000.mp4.part'
        source.write_bytes(b'finalized')
        manifest = self.root / '.capture.list'
        manifest.write_text(source.name + '\n', encoding='utf-8')
        with patch.object(Path, 'rename', side_effect=PermissionError):
            publish_completed_segments(self.root, manifest)
        self.assertTrue(source.exists())
        publish_completed_segments(self.root, manifest)
        self.assertEqual(source.with_suffix('').read_bytes(), b'finalized')
        publish_completed_segments(self.root, manifest)  # repeated list entry is harmless

    def test_manifest_cannot_publish_paths_or_overwrite_existing_video(self):
        ready = self.root / 'chunk_00000.mp4'
        ready.write_bytes(b'previous')
        pending = self.root / 'chunk_00000.mp4.part'
        pending.write_bytes(b'new')
        manifest = self.root / '.capture.list'
        manifest.write_text('../chunk_00001.mp4.part\nchunk_00000.mp4.part\n', encoding='utf-8')
        publish_completed_segments(self.root, manifest)
        self.assertEqual(ready.read_bytes(), b'previous')
        self.assertTrue(pending.exists())
        publish_completed_segments(self.root, self.root / 'missing.list')

    def test_repeated_manifest_does_not_recreate_deleted_markers(self):
        from video_sender.live_sender import delete_chunk_files
        pending = self.root / 'chunk_00000.mp4.part'
        pending.write_bytes(b'finished')
        manifest = self.root / '.list'
        manifest.write_text(pending.name + '\n')
        publish_completed_segments(self.root, manifest)
        self.assertTrue(delete_chunk_files(pending.with_suffix('')))
        publish_completed_segments(self.root, manifest)
        self.assertFalse((self.root / 'chunk_00000.ready').exists())

    def test_capture_restart_aligns_ffmpeg_and_timestamp_without_overwrite(self):
        old_video = self.root / 'chunk_00999.mp4'
        old_video.write_bytes(b'previous session')
        old_stamp = old_video.with_suffix('.txt')
        old_stamp.write_text('previous timestamp', encoding='utf-8')
        camera = Mock()
        camera.isOpened.return_value = True
        camera.get.side_effect = lambda prop: 16
        camera.read.side_effect = [(True, np.zeros((16, 16, 3), dtype=np.uint8)), (False, None)]
        process = Mock(stdin=io.BytesIO())
        process.wait.return_value = 0
        with patch.dict(os.environ, {'VIDEO_OUTPUT_DIR': str(self.root)}), \
             patch.object(cv2, 'VideoCapture', return_value=camera), \
             patch('subprocess.Popen', return_value=process) as start:
            runpy.run_module('video_sender.capture', run_name='__main__')
        command = start.call_args.args[0]
        self.assertEqual(command[command.index('-segment_start_number') + 1], '1000')
        self.assertEqual(command[command.index('-segment_format') + 1], 'mp4')
        self.assertEqual(command[command.index('-map') + 1], '0:v:0')
        self.assertTrue(command[-1].endswith('.mp4.part'))
        self.assertTrue((self.root / 'chunk_01000.txt').is_file())
        self.assertEqual(old_video.read_bytes(), b'previous session')
        self.assertEqual(old_stamp.read_text(encoding='utf-8'), 'previous timestamp')
        camera.release.assert_called_once()
        process.wait.assert_called_once()
