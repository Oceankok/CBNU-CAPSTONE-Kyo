from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import requests
from video_sender import live_sender as sender
from video_sender.chunks import publish_completed_segments


class SenderRegressionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(sender, 'OUTPUT_DIR', self.root))
        self.stream = self.enterContext(patch.object(sender, 'create_stream', return_value='stream'))

    def chunk(self, index):
        p = self.root / f'chunk_{index:05d}.mp4.part'
        p.write_bytes(b'finished')
        manifest = self.root / '.test.list'
        manifest.write_text(p.name + '\n')
        publish_completed_segments(self.root, manifest)
        p = p.with_suffix('')
        p.with_suffix('.txt').write_text(datetime.now(timezone.utc).isoformat())
        return p

    def test_last_finalized_chunk_is_sent_and_all_sidecars_removed(self):
        p = self.chunk(0)
        with patch.object(sender, 'send_chunk') as send, patch.object(sender.time, 'sleep', side_effect=KeyboardInterrupt):
            sender.watch_and_send()
        self.assertEqual(send.call_count, 1)
        for suffix in ('.mp4', '.txt', '.ready'):
            self.assertFalse(p.with_suffix(suffix).exists())

    def test_failed_mp4_or_timestamp_delete_retries_without_upload(self):
        for failed_suffix in ('.mp4', '.txt', '.ready'):
            with self.subTest(suffix=failed_suffix):
                p = self.chunk(0)
                original = Path.unlink
                failed = []
                def unlink(path, *args, **kwargs):
                    if path == p.with_suffix(failed_suffix) and not failed:
                        failed.append(path)
                        raise PermissionError('busy')
                    return original(path, *args, **kwargs)
                sleeps = []
                def sleep(_):
                    sleeps.append(1)
                    if len(sleeps) == 2:
                        raise KeyboardInterrupt
                with patch.object(Path, 'unlink', unlink), patch.object(sender, 'send_chunk') as send, patch.object(sender.time, 'sleep', sleep):
                    sender.watch_and_send()
                self.assertEqual(send.call_count, 1)
                self.assertFalse(p.with_suffix(failed_suffix).exists())

    def test_permanent_rejection_does_not_reconnect_or_block_later_chunk(self):
        for status in (413, 422):
            with self.subTest(status=status):
                first, second = self.chunk(0), self.chunk(1)
                attempted = []
                def send(stream, sequence, *args):
                    attempted.append(sequence)
                    if sequence == 0:
                        response = requests.Response()
                        response.status_code = status
                        raise requests.HTTPError(response=response)
                with patch.object(sender, 'send_chunk', side_effect=send), patch.object(sender.time, 'sleep', side_effect=KeyboardInterrupt):
                    sender.watch_and_send()
                self.assertEqual(attempted, [0, 1])
                self.assertFalse(first.exists())
                self.assertFalse(second.exists())
        self.assertEqual(self.stream.call_count, 2)

    def test_rejected_chunk_cleanup_failure_is_retried_without_resend(self):
        first, second = self.chunk(0), self.chunk(1)
        attempted, failed, sleeps = [], [], []
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == first and not failed:
                failed.append(path)
                raise PermissionError('busy')
            return original(path, *args, **kwargs)
        def send(stream, sequence, *args):
            attempted.append(sequence)
            if sequence == 0:
                response = requests.Response()
                response.status_code = 422
                raise requests.HTTPError(response=response)
        def sleep(_):
            sleeps.append(1)
            if len(sleeps) == 2:
                raise KeyboardInterrupt
        with patch.object(Path, 'unlink', unlink), patch.object(sender, 'send_chunk', side_effect=send), patch.object(sender.time, 'sleep', sleep):
            sender.watch_and_send()
        self.assertEqual(attempted, [0, 1])
        self.assertFalse(first.exists())
        self.assertEqual(self.stream.call_count, 1)

    def test_cleanup_intent_survives_sender_restart(self):
        p = self.chunk(0)
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == p:
                raise PermissionError('busy')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', unlink), patch.object(sender, 'send_chunk') as send, patch.object(sender.time, 'sleep', side_effect=KeyboardInterrupt):
            sender.watch_and_send()
        self.assertEqual(send.call_count, 1)
        self.assertTrue(p.exists())
        self.assertTrue(p.with_suffix('.cleanup').exists())
        with patch.object(sender, 'send_chunk') as resend, patch.object(sender.time, 'sleep', side_effect=KeyboardInterrupt):
            sender.watch_and_send()
        resend.assert_not_called()
        self.assertFalse(p.exists())
        self.assertFalse(p.with_suffix('.cleanup').exists())

    def test_restart_cleanup_runs_even_when_server_is_offline(self):
        p = self.chunk(0)
        p.with_suffix('.cleanup').touch()
        with patch.object(sender, 'create_stream', side_effect=requests.ConnectionError('offline')), patch.object(sender.time, 'sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                sender.watch_and_send()
        self.assertFalse(p.exists())
        self.assertFalse(p.with_suffix('.cleanup').exists())
