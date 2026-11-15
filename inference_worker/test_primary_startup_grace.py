"""Memory-only scheduling regressions; no camera, model or original-media edits."""
from datetime import datetime, timedelta, timezone
from unittest import TestCase

import numpy as np

from inference_worker.continuous_source_mux import SourceMux, SourceMuxError
from inference_worker.continuous_source_reader import ContinuousSourceReader
from inference_worker.run_continuous import poll_source


BASE = datetime(2026, 11, 15, 16, 0, 38, tzinfo=timezone.utc)


class PrimaryStartupGraceTests(TestCase):
    def make(self):
        class Client:
            available = {'main': 8, 'ref': 8}
            calls = []
            downloads = []
            starts = {}
            streams = [dict(camera_id=camera, stream_id=camera, last_sequence=sequence,
                            capture_context_id='ctx-' + camera,
                            capture_session_id='session-' + camera, assignment_version=7)
                       for camera, sequence in (('main', 8), ('ref', 7))]

            def list_streams(self):
                return self.streams

            def list_chunks(self, stream, after):
                self.calls.append((stream, after))
                sequence = self.available[stream]
                if sequence <= after:
                    return []
                start = self.starts.get((stream, sequence), sequence + (.275 if stream == 'main' else 0))
                identity = next(row for row in self.streams if row['stream_id'] == stream)
                return [dict(sequence=sequence, chunk_id=stream + str(sequence), duration_ms=1000,
                             capture_start_at=(BASE + timedelta(seconds=start)).isoformat(),
                             **{key: identity[key] for key in
                                ('capture_context_id', 'capture_session_id', 'assignment_version')})]

            def download(self, chunk):
                self.downloads.append(chunk)
                return b'synthetic-scheduling-fixture'

        client = Client()
        mux = SourceMux(camera_ids=['main', 'ref'], primary_camera='main', max_bytes=10000,
                        max_frames=128, max_frame_pixels=16, max_clock_error=.25,
                        clock_error_by_camera={'main': .191, 'ref': .191})

        def decode(*args, **kwargs):
            for index in range(2):
                yield index, 2, np.zeros((2, 2, 3), np.uint8)

        reader = ContinuousSourceReader(client, mux, offsets={'main': .253, 'ref': .253},
                    decoder=decode, max_chunk_bytes=100, max_chunk_gap_seconds=.02,
                    downloader=client.download, require_capture_context=True, degrade_references=True)
        self.addCleanup(mux.close)
        return client, reader

    def poll(self, reader, elapsed):
        return poll_source(reader, reader.mux, now=BASE.timestamp() + 11 + elapsed,
                           elapsed=elapsed, max_lag_seconds=20)

    def test_reference_first_waits_then_processes_original_bound_primary(self):
        client, reader = self.make()
        # Actual incident ordering: join main after 8, ref after 7; only ref 8
        # arrives before the first poll's downloads. Metadata stays unchanged.
        self.assertEqual(self.poll(reader, .2), [])
        self.assertEqual(client.calls, [('main', 8), ('ref', 7)])
        self.assertEqual(client.downloads, ['ref8'])
        self.assertEqual(set(reader.mux.latest), {'ref'})
        self.assertIsNone(reader.mux.failure)
        client.available = {'main': 9, 'ref': 9}
        frames = self.poll(reader, 1.2)
        self.assertTrue(any(camera == 'main' for camera, _, _ in frames))
        self.assertEqual([stamp for _, stamp, _ in frames], sorted(stamp for _, stamp, _ in frames))
        self.assertEqual(reader.primary_capture_context(), 'ctx-main')
        self.assertEqual(reader.streams, {'main': 'main', 'ref': 'ref'})
        self.assertEqual(reader.mux.ended, set())
        self.assertEqual(reader.reference_failures, {})

    def test_reference_progress_cannot_renew_primary_initial_deadline(self):
        client, reader = self.make()
        for second in range(20):
            client.available = {'main': 8, 'ref': 8 + second}
            self.assertEqual(self.poll(reader, second), [])
            self.assertNotIn('main', reader.mux.latest)
        client.available = {'main': 8, 'ref': 28}
        with self.assertRaisesRegex(SourceMuxError, '^source_initial_frame_timeout$'):
            self.poll(reader, 20)
        self.assertEqual(reader.mux.ended, set())

    def test_established_primary_stall_keeps_existing_deadline(self):
        client, reader = self.make()
        self.poll(reader, .2)
        client.available = {'main': 9, 'ref': 9}
        self.poll(reader, 1.2)
        self.poll(reader, 30)  # Drain the already queued primary frame first.
        with self.assertRaisesRegex(SourceMuxError, '^camera_missing_or_stalled$'):
            self.poll(reader, 30)

    def test_primary_capture_seam_guard_still_rejects_72ms(self):
        client, reader = self.make()
        self.poll(reader, .2)
        client.available = {'main': 9, 'ref': 9}
        self.poll(reader, 1.2)
        client.available = {'main': 10, 'ref': 10}
        client.starts[('main', 10)] = 10.275 + .072
        with self.assertRaisesRegex(SourceMuxError, '^camera_chunk_discontinuity$'):
            self.poll(reader, 2.2)
        self.assertNotIn('main10', client.downloads)
        self.assertEqual(reader.gap, .02)
