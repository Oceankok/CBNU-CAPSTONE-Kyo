import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from inference_worker.capture_outbox import CaptureOutbox,OutboxError
from inference_worker.lifecycle_journal import LifecycleJournal
from inference_worker.test_event_lifecycle import observation


class LifecycleJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.box=self.open();self.bridge=self.build()

    def open(self,**changes):
        cfg=dict(private_root=self.root/'raw',max_db_bytes=1_000_000,max_records=200,
                 max_body_bytes=4096,source_ttl_seconds=100,clock=lambda:100.)
        cfg.update(changes)
        return CaptureOutbox(self.root/'ledger',**cfg)

    def build(self):
        return LifecycleJournal(self.box,camera_id='main',camera_snapshot=[
            {'camera_id':'main','role':'primary'},{'camera_id':'ref','role':'reference'}],
            model_version='synthetic-v1',missing_grace_seconds=.5,
            threshold_seconds=3,max_sample_gap_seconds=.5,progress_emit_interval_seconds=0)

    def tearDown(self):
        self.box.close();self.temp.cleanup()

    def feed(self,end=6,tracks=('a',)):
        rows=[]
        for i in range(end+1):rows.extend(self.bridge.update([observation(t) for t in tracks],i*.5))
        return rows

    def test_three_second_start_exactbody_and_snapshot(self):
        self.assertEqual(self.feed(5),[])
        rows=self.bridge.update([observation('a')],3)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['kind'],'start')
        pending=self.box.pending();self.assertEqual(len(pending),1)
        body=json.loads(pending[0]['body'])
        self.assertEqual(body['model_version'],'synthetic-v1')
        self.assertEqual(body['session_id'],self.box.session)
        self.assertNotIn('cameras',body)
        self.assertEqual(len(json.loads(self.box.event_status(rows[0]['event_id'])['snapshot'])),2)
        self.assertEqual(self.bridge.update([observation('a')],3),[])
        self.assertEqual(len(self.box.pending()),1)

    def test_multiple_starts_rollback_db_and_live_reference(self):
        self.box.limit=5 # two starts need six rows: event+request+head each
        self.feed(5,tracks=('a','b'))
        old=self.bridge.lifecycle
        with self.assertRaises(OutboxError):self.bridge.update([observation('a'),observation('b')],3)
        self.assertIs(self.bridge.lifecycle,old)
        self.assertEqual(self.bridge.lifecycle.last_timestamp,2.5)
        self.assertFalse(self.box.pending())
        self.assertEqual(self.box.db.execute('SELECT count(*) FROM events').fetchone()[0],0)
        self.assertEqual(self.box.db.execute('SELECT count(*) FROM lifecycle_heads').fetchone()[0],0)
        self.box.limit=200
        rows=self.bridge.update([observation('a'),observation('b')],3)
        self.assertEqual(len(rows),2)
        self.assertEqual(len(self.box.pending()),2)

    def test_api_sequence_independent_of_manifest_command(self):
        event=self.feed()[0]['event_id']
        self.box.enqueue(event,seq=1,kind='manifest',idempotency_key='manifest-0',body={'sequence':0})
        self.bridge.update([observation('a')],3.5)
        row=self.box.db.execute('SELECT * FROM requests WHERE event=? AND seq=2',(event,)).fetchone()
        self.assertEqual(json.loads(row['body'])['sequence'],1)
        self.assertEqual(row['kind'],'progress')

    def test_close_is_durable_intent_not_http_then_restart_interrupts(self):
        event=self.feed()[0]['event_id']
        start=self.box.pending()[0]['body']
        self.bridge.update([observation('a')],3.5)
        rows=self.bridge.update([observation('a',status='helmet')],4)
        self.assertEqual(rows[0]['kind'],'close')
        intents=self.box.pending_close_intents()
        self.assertEqual(len(intents),1)
        intent=json.loads(intents[0]['transition'])
        self.assertEqual(intent['end_time'],3.5)
        self.assertEqual(intents[0]['next_api_seq'],2)
        self.assertEqual(self.box.db.execute("SELECT count(*) FROM requests WHERE kind='close'").fetchone()[0],0)
        self.box.close();self.box=self.open();self.bridge=self.build()
        self.assertEqual(self.box.pending_close_intents()[0]['state'],'interrupted')
        self.assertEqual(self.box.event_status(event)['state'],'interrupted')
        self.assertEqual(self.box.pending()[0]['body'],start)
        self.assertEqual(self.bridge.lifecycle.states,{})
        new=self.feed()[0]['event_id']
        self.assertNotEqual(new,event)

    def test_close_and_progress_frame_all_rollback_on_error(self):
        rows=self.feed(tracks=('a','b'));events={r['tracking_id']:r['event_id'] for r in rows}
        old=self.bridge.lifecycle
        original=self.box._room
        def fail_room():raise OutboxError('disk admission failed')
        with patch.object(self.box,'_room',side_effect=fail_room):
            with self.assertRaises(OutboxError):self.bridge.update([
                observation('a',status='helmet'),observation('b')],3.5)
        self.assertIs(self.bridge.lifecycle,old)
        self.assertFalse(self.box.pending_close_intents())
        for event in events.values():self.assertEqual(self.box.event_status(event)['next_seq'],1)
        rows=self.bridge.update([observation('a',status='helmet'),observation('b')],3.5)
        self.assertEqual({r['kind'] for r in rows},{'progress','close'})
        self.assertEqual(len(self.box.pending_close_intents()),1)

    def test_close_all_persists_before_replacing_lifecycle(self):
        self.feed();old=self.bridge.lifecycle
        with patch.object(self.box,'apply_lifecycle_batch',side_effect=RuntimeError('I/O failure')):
            with self.assertRaises(RuntimeError):self.bridge.close_all('shutdown')
        self.assertIs(self.bridge.lifecycle,old)
        self.assertEqual(len(self.bridge.close_all('shutdown')),1)
        self.assertFalse(self.bridge.lifecycle.states)
        self.assertEqual(len(self.box.pending_close_intents()),1)

    def prepare_close(self, manifest_sequences=()):
        event=self.feed()[0]['event_id']
        self.bridge.update([observation('a')],3.5)
        self.bridge.close_all('ppe_present')
        for sequence in manifest_sequences:
            seq=self.box.event_status(event)['next_seq']
            self.box.enqueue(event,seq=seq,kind='manifest',idempotency_key='manifest-'+str(seq),
                body={'session_id':self.box.session,'sequence':sequence})
        return event

    def acknowledge_all(self,event):
        for row in self.box.db.execute('SELECT seq FROM requests WHERE event=? ORDER BY seq',(event,)).fetchall():
            self.box.acknowledge(event,row['seq'],receipt={'verified':row['seq']})

    def test_finalize_after_manifest_ack_exact_idempotent_and_api_sequence(self):
        event=self.prepare_close((0,1));self.acknowledge_all(event)
        exact=self.box.finalize_pending_close(event,complete=True,final_segment_count=2)
        body=json.loads(exact)
        self.assertEqual(body['sequence'],2)
        self.assertEqual(body['kind'],'close')
        self.assertTrue(body['complete'])
        self.assertEqual(self.box.event_status(event)['next_seq'],5)
        self.assertEqual(self.box.pending_close_intents()[0]['state'],'finalized')
        self.assertEqual(self.box.finalize_pending_close(event,complete=True,final_segment_count=2),exact)
        with self.assertRaises(OutboxError):self.box.finalize_pending_close(event,complete=False,final_segment_count=2)
        self.box.close();self.box=self.open()
        self.assertEqual(self.box.finalize_pending_close(event,complete=True,final_segment_count=2),exact)

    def test_finalize_requires_ack_contiguous_manifest_prefix(self):
        event=self.prepare_close((0,2))
        with self.assertRaises(OutboxError):self.box.finalize_pending_close(event,complete=True,final_segment_count=2)
        self.acknowledge_all(event)
        with self.assertRaises(OutboxError):self.box.finalize_pending_close(event,complete=True,final_segment_count=2)
        self.assertEqual(self.box.pending_close_intents()[0]['state'],'pending')
        self.assertEqual(self.box.event_status(event)['state'],'active')

    def test_incomplete_finalization_and_restart_block(self):
        event=self.prepare_close();self.acknowledge_all(event)
        with self.box.transaction():
            self.box.db.execute("UPDATE events SET state='incomplete',reason='camera_missing' WHERE id=?",(event,))
        with self.assertRaises(OutboxError):self.box.finalize_pending_close(event,complete=True,final_segment_count=0)
        body=json.loads(self.box.finalize_pending_close(event,complete=False,final_segment_count=0))
        self.assertFalse(body['complete']);self.assertEqual(body['kind'],'close')
        self.assertEqual(self.box.pending()[0]['kind'],'incomplete')

    def test_old_pending_session_never_automatically_finalizes(self):
        event=self.prepare_close();self.acknowledge_all(event)
        self.box.close();self.box=self.open()
        with self.assertRaises(OutboxError):self.box.finalize_pending_close(event,complete=False,final_segment_count=0)
        self.assertEqual(self.box.pending_close_intents()[0]['state'],'interrupted')

    def test_finalize_transaction_rolls_back_intent_and_command(self):
        event=self.prepare_close();self.acknowledge_all(event)
        before=self.box.event_status(event)['next_seq']
        # Fail after enqueue but before final state update using SQLite trigger.
        self.box.db.execute("""CREATE TEMP TRIGGER fail_finalize BEFORE UPDATE OF state ON pending_closes
          WHEN NEW.state='finalized' BEGIN SELECT RAISE(ABORT,'synthetic finalization failure'); END""")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):self.box.finalize_pending_close(event,complete=True,final_segment_count=0)
        self.assertEqual(self.box.event_status(event)['next_seq'],before)
        self.assertEqual(self.box.event_status(event)['state'],'active')
        self.assertEqual(self.box.pending_close_intents()[0]['state'],'pending')
        self.assertFalse(self.box.pending())
        self.box.db.execute('DROP TRIGGER fail_finalize')
        self.box.finalize_pending_close(event,complete=True,final_segment_count=0)

    def test_progress_cadence_keeps_full_observations_and_immediate_close(self):
        self.bridge.progress_interval=2
        event=self.feed()[0]['event_id']
        for t in (3.5,4,4.5):self.bridge.update([observation('a')],t)
        self.assertEqual(self.box.event_status(event)['next_seq'],1)
        self.assertEqual(self.bridge.lifecycle.last_timestamp,4.5)
        self.bridge.update([observation('a')],5)
        self.assertEqual(self.box.event_status(event)['next_seq'],2)
        self.bridge.update([observation('a')],5.5)
        self.bridge.close_all('shutdown')
        intent=self.box.pending_close_intents()[0]
        self.assertEqual(intent['next_api_seq'],3)
        self.assertEqual(self.box.event_status(event)['next_seq'],3)
        transition=json.loads(intent['transition'])
        self.assertEqual(transition['end_time'],5.5)
        self.assertEqual(transition['frame_sample_count'],12)

    def test_progress_cadence_failure_does_not_consume_emit_slot(self):
        self.bridge.progress_interval=1
        event=self.feed()[0]['event_id']
        self.bridge.update([observation('a')],3.5)
        with patch.object(self.box,'_room',side_effect=OutboxError('full')):
            with self.assertRaises(OutboxError):self.bridge.update([observation('a')],4)
        self.assertEqual(self.bridge.last_emitted[event],3)
        self.assertEqual(self.bridge.lifecycle.last_timestamp,3.5)
        self.bridge.update([observation('a')],4)
        self.assertEqual(self.bridge.last_emitted[event],4)
        self.assertEqual(self.box.event_status(event)['next_seq'],2)

    def test_zero_segment_failure_can_close_and_report_missing(self):
        event=self.feed()[0]['event_id']
        self.box.mark_incomplete(event,reason='camera_source_unavailable')
        self.bridge.close_all('source_failed')
        exact=self.box.finalize_pending_close(event,complete=False,final_segment_count=0)
        self.assertFalse(json.loads(exact)['complete'])
        seq=self.box.event_status(event)['next_seq']
        self.box.enqueue(event,seq=seq,kind='capture_report',idempotency_key='failure-report',
            body={'state':'incomplete','reason':'camera_source_unavailable'})
        with self.assertRaises(OutboxError):self.box.enqueue(event,seq=seq+1,kind='capture_report',
            idempotency_key='false-success',body={'state':'complete'})
        self.assertEqual(self.box.event_status(event)['reason'],'camera_source_unavailable')

    def test_incomplete_close_flushes_latest_positive_before_intent(self):
        self.bridge.progress_interval=10
        event=self.feed()[0]['event_id']
        self.bridge.update([observation('a')],3.5)
        self.box.mark_incomplete(event,reason='camera_failed')
        self.bridge.close_all('source_failed')
        row=self.box.db.execute("SELECT body FROM requests WHERE event=? AND kind='progress'",(event,)).fetchone()
        self.assertEqual(json.loads(row['body'])['sequence'],1)
        self.assertEqual(self.box.pending_close_intents()[0]['next_api_seq'],2)
        self.assertEqual(self.box.event_status(event)['state'],'incomplete')

    def test_active_process_restart_does_not_borrow_observation_duration(self):
        old_event = self.feed()[0]['event_id']
        old_session = self.box.session
        old_start = bytes(self.box.pending()[0]['body'])
        self.box.close()
        self.box = self.open()
        self.bridge = self.build()
        self.assertNotEqual(self.box.session, old_session)
        self.assertEqual(self.box.event_status(old_event)['state'], 'interrupted')
        # A recycled tracking ID must accumulate a fresh threshold after restart.
        for i in range(6):
            self.assertEqual(self.bridge.update([observation('a')], 10 + .5*i), [])
        new_event = self.bridge.update([observation('a')], 13)[0]['event_id']
        self.assertNotEqual(new_event, old_event)
        pending = {r['event']: r for r in self.box.pending()}
        self.assertEqual(bytes(pending[old_event]['body']), old_start)
        self.assertEqual(json.loads(pending[new_event]['body'])['session_id'], self.box.session)
        self.assertEqual(json.loads(pending[new_event]['body'])['observed_seconds'], 3)
        self.assertEqual(self.box.db.execute(
            "SELECT count(*) FROM requests WHERE event=? AND kind IN ('close','incomplete')",
            (old_event,)).fetchone()[0], 0)

    def test_restart_below_threshold_does_not_create_phantom_old_incident(self):
        self.assertEqual(self.feed(5), [])
        self.box.close()
        self.box = self.open()
        self.bridge = self.build()
        self.assertEqual(self.box.db.execute('SELECT count(*) FROM events').fetchone()[0], 0)
        for i in range(6):
            self.assertEqual(self.bridge.update([observation('a')], 20 + .5*i), [])
        result = self.bridge.update([observation('a')], 23)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['start_time'], 20)
