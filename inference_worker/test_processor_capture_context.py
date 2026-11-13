import json
from unittest import TestCase
from inference_worker.continuous_source_mux import SourceMuxError
from inference_worker import test_continuous_source_reader as source_tests
from inference_worker import test_lifecycle_journal as journal_tests


class ContextReaderTests(TestCase):
    def make(self):
        client,reader=source_tests.ReaderTests().make()
        client.streams=[dict(s,capture_context_id='ctx-'+s['camera_id'],
                             capture_session_id='session-'+s['camera_id'],assignment_version=1)
                        for s in client.streams]
        previous=client.list_chunks
        def chunks(stream,after):
            source=next(s for s in client.streams if s['stream_id']==stream)
            return [dict(c,**{k:source[k] for k in ('capture_context_id','capture_session_id','assignment_version')})
                    for c in previous(stream,after)]
        client.list_chunks=chunks
        return client,reader

    def test_context_frozen_and_chunk_metadata_matches(self):
        c,r=self.make();self.assertEqual(len(r.poll()),4)
        self.assertEqual(r.primary_capture_context(),'ctx-main')
        c.seq=1;self.assertEqual(len(r.poll()),4)

    def test_context_transition_on_same_stream_fails_closed(self):
        c,r=self.make();r.poll();c.seq=1
        c.streams[0]['assignment_version']=2
        with self.assertRaisesRegex(SourceMuxError,'capture_context_changed'):r.poll()

    def test_reference_context_transition_also_fails_closed(self):
        c,r=self.make();r.poll();c.seq=1
        c.streams[1]['capture_context_id']='new-ref'
        with self.assertRaisesRegex(SourceMuxError,'capture_context_changed'):r.poll()

    def test_partial_or_invalid_context_rejected(self):
        for change in ({'capture_session_id':None},{'assignment_version':True},{'capture_context_id':'../bad'}):
            c,r=self.make();c.streams[0].update(change)
            with self.assertRaisesRegex(SourceMuxError,'invalid_capture_context'):r.poll()

    def test_missing_chunk_context_rejected_before_download(self):
        c,r=self.make();original=c.list_chunks
        def chunks(stream,after):
            return [{k:v for k,v in row.items() if k!='capture_context_id'} for row in original(stream,after)]
        c.list_chunks=chunks
        r.downloader=lambda ident:self.fail('must reject before downloading')
        with self.assertRaises(SourceMuxError):r.poll()

    def test_required_context_rejects_legacy_but_default_compatible(self):
        c,r=source_tests.ReaderTests().make();r.require_capture_context=True
        with self.assertRaisesRegex(SourceMuxError,'capture_context_required'):r.poll()
        c,r=source_tests.ReaderTests().make();self.assertEqual(len(r.poll()),4)
        self.assertIsNone(r.primary_capture_context())

    def test_stopped_tail_drains_original_stream_with_new_context_present(self):
        c,r=self.make();r.poll();c.seq=1
        for row in c.streams:
            row.update(capture_stopped=True,final_sequence=1,last_sequence=1)
        c.streams += [dict(row,stream_id='new-'+row['stream_id'],capture_stopped=False,
                          capture_context_id='new-'+row['capture_context_id'],last_sequence=1)
                      for row in c.streams]
        output=r.poll()
        self.assertEqual(len(output),4)
        self.assertEqual(r.sequence,{'main':1,'ref':1})
        self.assertEqual(r.primary_capture_context(),'ctx-main')
        self.assertEqual(output[-1][1]-output[0][1],.5)
        with self.assertRaisesRegex(SourceMuxError,'session_stopped'):r.poll()

    def test_new_reader_excludes_stopped_old_context(self):
        c,r=self.make()
        old=[dict(row,capture_stopped=True,final_sequence=0,last_sequence=0) for row in c.streams]
        c.streams=[dict(row,stream_id='new-'+row['stream_id'],capture_context_id='new-'+row['capture_context_id'])
                   for row in c.streams]+old
        self.assertEqual(len(r.poll()),4)
        self.assertEqual(r.primary_capture_context(),'new-ctx-main')
        c,r=self.make()
        for row in c.streams:row.update(capture_stopped=True,final_sequence=0,last_sequence=0)
        with self.assertRaisesRegex(SourceMuxError,'camera_missing'):r.poll()

    def test_stopped_final_bounds_and_missing_tail_fail_closed(self):
        c,r=self.make();r.poll();c.seq=2
        c.streams[0].update(capture_stopped=True,final_sequence=1,last_sequence=1)
        c.streams[1]['last_sequence']=c.seq
        with self.assertRaisesRegex(SourceMuxError,'source_chunk_unavailable'):r.poll()
        c,r=self.make();r.poll()
        c.streams[0].update(capture_stopped=True,final_sequence=1,last_sequence=2)
        c.streams[1]['last_sequence']=c.seq
        with self.assertRaisesRegex(SourceMuxError,'invalid_capture_final'):r.poll()
        c,r=self.make();r.poll();c.seq=1
        c.streams[1].update(capture_stopped=True,final_sequence=0,last_sequence=0)
        output=r.poll()
        self.assertEqual([cam for cam,_,_ in output],['main','main'])
        self.assertEqual(r.sequence,{'main':1,'ref':0})
        self.assertEqual(r.mux.ended,{'ref'})

    def test_capture_final_cannot_change_or_reopen(self):
        for update,error in ((dict(final_sequence=2,last_sequence=2),'final_sequence_changed'),
                             (dict(capture_stopped=False),'stop_reverted')):
            c,r=self.make();r.poll()
            c.streams[0].update(capture_stopped=True,final_sequence=1,last_sequence=1)
            c.streams[1]['last_sequence']=c.seq
            with self.assertRaisesRegex(SourceMuxError,'source_chunk_unavailable'):r.poll()
            c.streams[0].update(update)
            with self.assertRaisesRegex(SourceMuxError,error):r.poll()

    def test_asymmetric_final_sequences_both_directions_and_new_context(self):
        for shorter in ('main','ref'):
            c,r=self.make();r.poll();c.seq=1
            for row in c.streams:
                final=0 if row['camera_id']==shorter else 1
                row.update(capture_stopped=True,final_sequence=final,last_sequence=final)
            c.streams += [dict(row,stream_id='new-'+row['stream_id'],capture_stopped=False,
                              capture_context_id='new-'+row['capture_context_id']) for row in c.streams]
            out=r.poll();self.assertEqual(len(out),2)
            self.assertTrue(all(cam!=shorter for cam,_,_ in out))
            self.assertEqual(r.sequence[shorter],0)
            self.assertEqual(r.mux.bytes,0)
            with self.assertRaisesRegex(SourceMuxError,'session_stopped'):r.poll()

    def test_missing_final_chunk_does_not_authorize_eos(self):
        c,r=self.make();r.poll()
        for row in c.streams:row.update(capture_stopped=True,final_sequence=1,last_sequence=1)
        with self.assertRaisesRegex(SourceMuxError,'source_chunk_unavailable'):r.poll()
        self.assertEqual(r.mux.ended,set())
        c.streams=[]
        with self.assertRaisesRegex(SourceMuxError,'source_stream_unavailable'):r.poll()

    def test_first_tail_chunk_cannot_skip_unconsumed_sequence(self):
        c,r=self.make();c.seq=-1
        self.assertEqual(r.poll(),[])
        c.seq=1
        for row in c.streams:row.update(capture_stopped=True,final_sequence=1,last_sequence=1)
        with self.assertRaisesRegex(SourceMuxError,'source_chunk_unavailable'):r.poll()
        self.assertEqual(r.mux.ended,set())

    def test_primary_eos_freezes_live_reference_cutoff_without_fake_stop(self):
        c,r=self.make();r.poll();c.seq=1
        c.streams[0].update(capture_stopped=True,final_sequence=0,last_sequence=0)
        c.streams[1]['last_sequence']=1
        out=r.poll();self.assertEqual(len(out),2)
        self.assertTrue(all(cam=='ref' for cam,_,_ in out))
        self.assertEqual(r.cutoffs,{'ref':1})
        self.assertNotIn('ref',r.finals)
        self.assertEqual(r.end_reasons['ref'],'reference_drain_cutoff')
        c.seq=2;c.streams[1]['last_sequence']=2
        c.streams.append(dict(c.streams[0],stream_id='new-main',capture_stopped=False,
                              capture_context_id='new-context'))
        with self.assertRaisesRegex(SourceMuxError,'primary_source_ended'):r.poll()
        self.assertEqual(r.sequence['ref'],1)
        self.assertEqual(r.primary_capture_context(),'ctx-main')


class ContextJournalTests(TestCase):
    setUp=journal_tests.LifecycleJournalTests.setUp
    tearDown=journal_tests.LifecycleJournalTests.tearDown
    open=journal_tests.LifecycleJournalTests.open
    build=journal_tests.LifecycleJournalTests.build
    feed=journal_tests.LifecycleJournalTests.feed
    # Reuse real durable journal setup, not a fake request recorder.
    def test_start_persists_source_context_and_retries_do_not_change_it(self):
        self.bridge.bind_capture_context('ctx-main')
        event=self.feed()[0]['event_id']
        original=self.box.pending()[0]['body']
        self.assertEqual(json.loads(original)['capture_context_id'],'ctx-main')
        self.bridge.bind_capture_context('ctx-main')
        with self.assertRaisesRegex(ValueError,'new_session'):
            self.bridge.bind_capture_context('ctx-new')
        self.assertEqual(self.box.pending()[0]['body'],original)
        self.box.close();self.box=self.open();self.bridge=self.build()
        self.assertEqual(self.box.pending()[0]['body'],original)
        self.bridge.bind_capture_context('ctx-new')
        new=self.feed()[0]['event_id'];self.assertNotEqual(new,event)
        self.assertEqual(next(json.loads(row['body']) for row in self.box.pending()
                              if json.loads(row['body']).get('event_id')==new)['capture_context_id'],'ctx-new')

    def test_cannot_bind_after_observations_or_upgrade_legacy_journal(self):
        self.feed(0)
        with self.assertRaisesRegex(ValueError,'before_observations'):
            self.bridge.bind_capture_context('ctx-main')
        self.bridge=self.build();self.bridge.bind_capture_context(None)
        with self.assertRaisesRegex(ValueError,'new_session'):
            self.bridge.bind_capture_context('ctx-main')
