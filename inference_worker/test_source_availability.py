import json
from unittest import TestCase
from inference_worker import test_processor_capture_context as context_tests
from inference_worker.continuous_source_reader import SourceUnavailableError
from inference_worker.continuous_source_mux import SourceMuxError
from inference_worker import test_lifecycle_journal as journal_tests

class AvailabilityTests(TestCase):
    def make(self):return context_tests.ContextReaderTests().make()
    def test_missing_list_row_available_old_source_drains(self):
        c,r=self.make();r.poll();c.seq=1
        c.list_streams=lambda:[]
        def availability(stream,after):
            row=next(s for s in c.streams if s['stream_id']==stream)
            return dict(row,last_sequence=1,next_sequence=after+1,next_chunk_state='available',reason='available')
        c.stream_availability=availability
        self.assertEqual(len(r.poll()),4)
        self.assertEqual(r.primary_capture_context(),'ctx-main')

    def test_unavailable_evidence_has_diagnostic_metadata(self):
        for reason in ('raw_expired','raw_missing','raw_unavailable','receipt_missing'):
            c,r=self.make();r.poll();c.list_streams=lambda:[]
            c.stream_availability=lambda stream,after:dict(c.streams[0],last_sequence=1,
                next_sequence=after+1,next_chunk_state='unavailable',reason=reason)
            with self.assertRaises(SourceUnavailableError) as caught:r.poll()
            error=caught.exception
            self.assertEqual((error.camera,error.stream,error.expected_sequence,error.availability_reason),
                             ('main','a',1,reason))
            self.assertEqual(r.mux.ended,set())

    def test_missing_endpoint_does_not_guess_expiry_or_switch_context(self):
        c,r=self.make();r.poll();c.list_streams=lambda:[]
        c.stream_availability=lambda stream,after:None
        with self.assertRaises(SourceUnavailableError) as caught:r.poll()
        self.assertEqual(caught.exception.reason,'source_stream_unavailable')
        self.assertIsNone(caught.exception.availability_reason)

    def test_context_mismatch_and_expected_sequence_rejected(self):
        for change in ({'capture_context_id':'new-context'},{'next_sequence':99}):
            c,r=self.make();r.poll();c.list_streams=lambda:[]
            c.stream_availability=lambda stream,after:dict(c.streams[0],**dict(
                {'next_sequence':after+1,'next_chunk_state':'available'},**change))
            with self.assertRaisesRegex(SourceMuxError,'invalid_source_availability'):r.poll()

    def test_not_received_is_not_eos_and_download_race_is_missing(self):
        c,r=self.make();r.poll();c.list_streams=lambda:[]
        c.stream_availability=lambda stream,after:dict(next(s for s in c.streams if s['stream_id']==stream),
            last_sequence=0,next_sequence=after+1,next_chunk_state='not_received')
        self.assertEqual(r.poll(),[]);self.assertEqual(r.mux.ended,set())
        c.seq=1
        def unavailable(_):raise SourceMuxError('chunk_http_410')
        r.downloader=unavailable
        with self.assertRaisesRegex(SourceUnavailableError,'source_chunk_unavailable'):r.poll()

    def test_zero_first_frame_wait_has_deadline_not_eos(self):
        from inference_worker.run_continuous import check_source_progress
        c,r=self.make()
        check_source_progress(r.mux,now=100,elapsed=.5,max_lag_seconds=1)
        with self.assertRaisesRegex(SourceMuxError,'source_initial_frame_timeout'):
            check_source_progress(r.mux,now=100,elapsed=1,max_lag_seconds=1)
        self.assertEqual(r.mux.ended,set())

class DurableAvailabilityTests(TestCase):
    setUp=journal_tests.LifecycleJournalTests.setUp
    tearDown=journal_tests.LifecycleJournalTests.tearDown
    open=journal_tests.LifecycleJournalTests.open
    build=journal_tests.LifecycleJournalTests.build
    feed=journal_tests.LifecycleJournalTests.feed
    def test_failure_metadata_survives_restart_without_raw(self):
        metadata=dict(reason='source_chunk_unavailable',camera='ref',stream='stream',expected_sequence=7,
                      availability_reason='raw_expired')
        self.box.record_source_failure(metadata)
        session=self.box.session
        self.box.close();self.box=self.open()
        row=self.box.db.execute('SELECT metadata FROM source_failures WHERE session=?',(session,)).fetchone()
        self.assertEqual(json.loads(row[0]),metadata)
