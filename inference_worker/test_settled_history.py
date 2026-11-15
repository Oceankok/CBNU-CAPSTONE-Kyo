"""Settled producer history needs no mutation, even after remote protection."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
from uuid import uuid4

from inference_worker.capture_outbox import CaptureOutbox
from inference_worker.continuous_dispatcher import ContinuousDispatcher
from inference_worker.continuous_payloads import _time,segment_payload
from inference_worker.continuous_transport import UploadPlan
from inference_worker.lifecycle_journal import LifecycleJournal
from inference_worker.segment_spool import Segment
from inference_worker.session_recovery import SessionRecovery,RecoveryRequired
from inference_worker.test_continuous_dispatcher import FakeTransport
from backend.capture_grid import required_capture_end
from datetime import datetime, timezone, timedelta


class SettledHistoryTests(TestCase):
    def make(self, *, incomplete=False,discard=False,no_sources=False,capture_output_fps=None,tail_jitter=0,
             epoch=None,retain_encoder_grid=False):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        f=SimpleNamespace(root=Path(temp.name),transport=FakeTransport(),
            now=1767225610.1234567 if epoch is None else epoch+10)
        def open_box():
            return CaptureOutbox(f.root/'journal',private_root=f.root/'raw',max_db_bytes=2000000,
                max_records=300,max_body_bytes=30000,source_ttl_seconds=100,clock=lambda:f.now)
        def dispatch():
            return ContinuousDispatcher(f.box,f.transport,enabled=True,max_delivery_records=30,max_delivery_db_bytes=2000000)
        f.box=open_box();f.dispatcher=dispatch()
        def close():f.dispatcher.close();f.box.close()
        self.addCleanup(close)
        f.session=f.box.session
        f.box.bind_runtime({'endpoint':'https://test','primary':'main'})
        journal=LifecycleJournal(f.box,camera_id='main',camera_snapshot=['main','ref'],model_version='fixture',
            missing_grace_seconds=1,threshold_seconds=3,max_sample_gap_seconds=.5,progress_emit_interval_seconds=1,
            capture_output_fps=capture_output_fps)
        epoch=f.now-10
        absent={'tracking_id':'t','ppe_type':'helmet','status':'suspected_no_helmet',
                'verified_ppe_confidence':None,'policy_scope_key':'fixture'}
        for step in range(13):
            transitions=journal.update([absent],epoch+step*.25)
        f.event=next(t['event_id'] for t in transitions if t['kind']=='start')
        if tail_jitter:journal.update([absent],epoch+3+tail_jitter)
        def ack(row,*,segments=()):
            body=json.loads(row['body']);kind='close' if row['kind']=='incomplete' else row['kind']
            camera=row['idem'].split(':')[0] if kind=='capture_report' else None
            receipt={'event_id':f.event,'accepted_command':{'kind':kind,'session_id':f.session,
                'sequence':0 if kind=='start' else body['sequence'],'camera_id':camera},
                'lifecycle':{'phase':'interrupted' if incomplete else 'closed'},'segments':list(segments)}
            f.box.acknowledge(f.event,row['seq'],receipt=receipt)
        ack(f.box.db.execute('SELECT * FROM requests').fetchone())
        f.sources={};left=epoch-2
        right=required_capture_end(datetime.fromtimestamp(epoch,tz=timezone.utc),
            datetime.fromtimestamp(epoch+3+tail_jitter,tz=timezone.utc),capture_output_fps).timestamp()
        if retain_encoder_grid:
            right=left+round((right-left)*capture_output_fps)/capture_output_fps
        for camera in (() if no_sources else ('main','ref') if discard or not incomplete else ('main',)):
            directory,sid=uuid4().hex,uuid4().hex
            f.box.begin_capture(f.event,camera=camera,spool_directory=directory)
            path=f.box.root/directory/(sid+'.mp4');path.parent.mkdir();path.write_bytes(('source-'+camera).encode())
            segment=Segment(sid,0,left,right,14,2,path.stat().st_size,f.now)
            f.box.bind_segment(f.event,camera=camera,segment=segment,relative_path=directory+'/'+sid+'.mp4')
            f.sources[camera]=(segment,path)
        journal.update([{**absent,'status':'helmet'}],epoch+3.25+tail_jitter)
        for pending in f.box.db.execute("SELECT * FROM requests WHERE ack IS NULL ORDER BY seq").fetchall():
            ack(pending)
        if incomplete:f.box.mark_incomplete(f.event,reason='camera_capture_incomplete')
        remote=[]
        if not no_sources:
            body=segment_payload(SimpleNamespace(capture_start=left,capture_end=right),session_id=f.session,event_sequence=0)
            seq=f.box.event_status(f.event)['next_seq']
            f.box.enqueue(f.event,seq=seq,kind='manifest',idempotency_key='manifest',body=body)
            remote=[{'event_id':f.event,'camera_id':c,'role':'clip' if c=='main' else 'reference',
                     'request_id':'REQ_'+c,'sequence':0,'capture_start_at':body['capture_start_at'],
                     'capture_end_at':body['capture_end_at']} for c in ('main','ref')]
            ack(f.box.db.execute('SELECT * FROM requests WHERE seq=?',(seq,)).fetchone(),segments=remote)
        f.box.finalize_pending_close(f.event,complete=not incomplete,final_segment_count=0 if no_sources else 1)
        ack(f.box.db.execute("SELECT * FROM requests WHERE kind IN ('close','incomplete')").fetchone())
        for camera in ('main','ref'):
            available=camera in f.sources and not (discard and camera=='ref')
            body={'session_id':f.session,'sequence':1,'state':'incomplete' if incomplete else 'complete',
                  'captured_ranges':[{'start_at':_time(left),'end_at':_time(right)}] if available else [],
                  'missing_ranges':[] if available else [{'start_at':_time(left),'end_at':_time(right),'reason':'source_failed'}],
                  'reason':'camera_capture_incomplete' if incomplete else None}
            seq=f.box.event_status(f.event)['next_seq']
            f.box.enqueue(f.event,seq=seq,kind='capture_report',idempotency_key=camera+':report',body=body)
            ack(f.box.db.execute('SELECT * FROM requests WHERE seq=?',(seq,)).fetchone())
        for camera,(segment,path) in f.sources.items():
            if discard and camera=='ref':
                f.box.request_cleanup(segment.segment_id,reason='source_corrupt')
            else:
                plan=UploadPlan(f.event,'REQ_'+camera,camera,path.parent.name+'/'+path.name,path.stat().st_size,
                    hashlib.sha256(path.read_bytes()).hexdigest(),left,right,left,right,True)
                f.dispatcher.register_upload(segment.segment_id,plan,session_id=f.session,
                    role='clip' if camera=='main' else 'reference')
        f.dispatcher.upload_once()
        for segment,path in f.sources.values():
            path.unlink();f.box.acknowledge_cleanup(segment.segment_id);path.parent.rmdir()
        f.dispatcher.close();f.box.close();f.box=open_box();f.dispatcher=dispatch()
        f.transport.uploads.clear()
        def protected(method,path,*,body=None,**kwargs):
            self.assertTrue(path.endswith('/operation-receipt'))
            return {'event_id':f.event,'session_id':f.session,'sequence':body['sequence'],
                'submitted_sha256':'a'*64,'receipt_status':'committed_exact',
                'accepted_operation':{'sha256':'a'*64,'body':body},'recovery_blockers':['already_reviewed']}
        f.transport.metadata_read=Mock(side_effect=protected)
        f.recovery=SessionRecovery(f.box,f.dispatcher,endpoint='https://test')
        return f

    def assert_noop(self,f):
        before=f.box.db.total_changes
        self.assertEqual(f.recovery.run(),[f.event])
        self.assertEqual(f.box.db.total_changes,before)
        f.transport.metadata_read.assert_not_called()
        self.assertEqual(f.transport.uploads,[])

    def assert_protected(self,f):
        # A stale success marker must not bypass ANY remaining obligation.
        f.box.recovery_state(f.event,'reconciled','stale_marker')
        with self.assertRaisesRegex(RecoveryRequired,'protected_historical_event'):
            f.recovery.run()
        self.assertTrue(f.transport.metadata_read.called)
        self.assertEqual(f.transport.uploads,[])

    def test_normal_complete_without_recovery_marker_is_a_local_noop(self):
        f=self.make()
        self.assertEqual(f.box.db.execute('SELECT count(*) FROM recovery_states').fetchone()[0],0)
        self.assert_noop(f)

    def test_opted_in_offgrid_end_is_settled_after_restart_without_mutation(self):
        f=self.make(capture_output_fps=15,tail_jitter=.008)
        start=json.loads(f.box.db.execute("SELECT body FROM requests WHERE kind='start'").fetchone()[0])
        self.assertEqual(start['capture_output_fps'],15)
        self.assert_noop(f)

    def test_supported_117fps_one_wire_microsecond_outer_end_is_settled(self):
        f=self.make(capture_output_fps=117,tail_jitter=.49,
            epoch=1794759195.0032425,retain_encoder_grid=True)
        actual=f.sources['main'][0].capture_end
        required=required_capture_end(datetime.fromtimestamp(1794759195.0032425,tz=timezone.utc),
            datetime.fromtimestamp(1794759195.0032425+3.49,tz=timezone.utc),117)
        self.assertEqual(datetime.fromtimestamp(actual,tz=timezone.utc)-required,timedelta(microseconds=1))
        self.assertGreater(actual-required.timestamp(),1e-6)
        self.assert_noop(f)

    def test_supported_117fps_two_wire_microsecond_plan_difference_stays_protected(self):
        f=self.make(capture_output_fps=117,tail_jitter=.49,
            epoch=1794759195.0032425,retain_encoder_grid=True)
        sid=f.sources['main'][0].segment_id
        with f.dispatcher.db:
            plan=json.loads(f.dispatcher.db.execute('SELECT plan FROM uploads WHERE segment=?',(sid,)).fetchone()[0])
            plan['required_end']+=1e-6
            f.dispatcher.db.execute('UPDATE uploads SET plan=? WHERE segment=?',(json.dumps(plan),sid))
        self.assert_protected(f)

    def test_internal_one_microsecond_capture_gap_remains_unsettled(self):
        f=self.make()
        row=f.box.db.execute("SELECT * FROM requests WHERE kind='capture_report' AND idem='main:report'").fetchone()
        body=json.loads(row['body']);span=body['captured_ranges'][0]
        middle=datetime.fromisoformat(span['start_at'])+timedelta(seconds=3)
        body['captured_ranges']=[{'start_at':span['start_at'],'end_at':middle.isoformat()},
            {'start_at':(middle+timedelta(microseconds=1)).isoformat(),'end_at':span['end_at']}]
        payload=json.dumps(body,sort_keys=True,separators=(',',':')).encode()
        with f.box.transaction():
            f.box.db.execute('UPDATE requests SET body=?,digest=? WHERE event=? AND seq=?',
                (payload,hashlib.sha256(payload).hexdigest(),f.event,row['seq']))
        self.assert_protected(f)

    def test_retained_117fps_range_uses_wire_outer_precision_and_exact_adjacency(self):
        epoch=1794759195.0032425;left=epoch-2
        required=required_capture_end(datetime.fromtimestamp(epoch,tz=timezone.utc),
            datetime.fromtimestamp(epoch+3.49,tz=timezone.utc),117)
        right=left+round((required.timestamp()-left)*117)/117
        camera={'camera_id':'main','required_start_at':_time(left),'required_end_at':required.isoformat()}
        rows=[{'start':left,'end':right}]
        box=SimpleNamespace(reconcile=lambda:None,db=SimpleNamespace(execute=lambda *args:rows))
        recovery=SessionRecovery(box,SimpleNamespace(transport=None),endpoint='https://test')
        ranges,complete=recovery._available_ranges('fixture',camera)
        self.assertTrue(complete)
        self.assertEqual(ranges,[{'start_at':_time(left),'end_at':_time(right)}])
        middle=datetime.fromtimestamp(left,tz=timezone.utc)+timedelta(seconds=3)
        rows[:]=[{'start':left,'end':middle.timestamp()},
            {'start':(middle+timedelta(microseconds=1)).timestamp(),'end':right}]
        self.assertFalse(recovery._available_ranges('fixture',camera)[1])

    def test_settled_incomplete_missing_discarded_or_zero_sources_is_a_local_noop(self):
        for options in ({},{'discard':True},{'no_sources':True}):
            with self.subTest(options=options):self.assert_noop(self.make(incomplete=True,**options))

    def test_prior_protected_failure_marker_does_not_turn_settled_history_into_work(self):
        f=self.make();f.box.recovery_state(f.event,'required','protected_historical_event');self.assert_noop(f)

    def test_missing_required_camera_report_does_not_pass_an_empty_queue(self):
        f=self.make()
        with f.box.transaction():
            f.box.db.execute("DELETE FROM requests WHERE kind='capture_report' AND idem='ref:report'")
            f.box.db.execute('UPDATE events SET next_seq=next_seq-1')
        self.assertEqual(f.box.unacknowledged_count(),0);self.assert_protected(f)

    def test_unacknowledged_command_remains_protected(self):
        f=self.make()
        with f.box.transaction():f.box.db.execute("UPDATE requests SET ack=NULL WHERE kind='start'")
        self.assert_protected(f)

    def test_cleanup_intent_remains_protected_even_after_file_disappears(self):
        f=self.make()
        with f.box.transaction():f.box.db.execute('UPDATE segments SET cleanup=1')
        self.assert_protected(f)

    def test_residual_raw_or_unknown_owned_file_is_preserved_and_protected(self):
        for name in ('raw.mp4','unknown-file'):
            with self.subTest(name=name):
                f=self.make();segment,path=f.sources['main'];path.parent.mkdir()
                raw=path.parent/name;raw.write_bytes(b'preserve')
                self.assert_protected(f);self.assertEqual(raw.read_bytes(),b'preserve')

    def test_pending_orphan_and_ambiguous_owner_remain_protected(self):
        for orphan in (True,False):
            with self.subTest(orphan=orphan):
                f=self.make()
                with f.box.transaction():
                    if orphan:f.box.db.execute("INSERT INTO orphans VALUES(?,?,?,'unknown',1)",('orphan.mp4',f.event,'main'))
                    else:f.box.db.execute("UPDATE captures SET state='reconciliation_required'")
                self.assert_protected(f)

    def test_unfinished_close_remains_protected(self):
        f=self.make()
        with f.box.transaction():f.box.db.execute("UPDATE pending_closes SET state='interrupted'")
        self.assert_protected(f)

    def test_receipt_plan_session_and_upload_state_bindings_cannot_be_ignored(self):
        changes=(('receipt','source_sha256','b'*64),('receipt','request_id','WRONG'),
                 ('receipt','role','reference'),('receipt','source_bytes',1),
                 ('plan','relative_path','wrong/path.mp4'),('plan','required_end',0),
                 ('session',None,'f'*32),('state',None,'manual'))
        for column,key,value in changes:
            with self.subTest(column=column,key=key):
                f=self.make();sid=f.sources['main'][0].segment_id
                with f.dispatcher.db:
                    if key is not None:
                        body=json.loads(f.dispatcher.db.execute('SELECT '+column+' FROM uploads WHERE segment=?',(sid,)).fetchone()[0])
                        body[key]=value;value=json.dumps(body)
                    f.dispatcher.db.execute('UPDATE uploads SET '+column+'=? WHERE segment=?',(value,sid))
                self.assert_protected(f)

    def test_incomplete_missing_coverage_or_disposition_is_not_settled(self):
        for reason in ('report','source'):
            with self.subTest(reason=reason):
                f=self.make(incomplete=True,discard=True)
                with f.box.transaction():
                    if reason=='report':
                        row=f.box.db.execute("SELECT * FROM requests WHERE idem='ref:report'").fetchone()
                        body=json.loads(row['body']);body['missing_ranges']=[]
                        raw=json.dumps(body,sort_keys=True,separators=(',',':')).encode()
                        f.box.db.execute('UPDATE requests SET body=?,digest=? WHERE seq=?',(raw,hashlib.sha256(raw).hexdigest(),row['seq']))
                    else:f.box.db.execute("UPDATE segments SET reason=NULL WHERE camera='ref'")
                self.assert_protected(f)

    def test_conflicting_retired_history_remains_protected(self):
        f=self.make();seq=f.box.db.execute("SELECT seq FROM requests WHERE kind='close'").fetchone()[0]
        f.box.retire_request(f.event,seq,reason='conflict',receipt={})
        self.assert_protected(f)

    def test_settled_history_still_requires_original_endpoint_before_any_io(self):
        f=self.make()
        with self.assertRaisesRegex(RecoveryRequired,'original_endpoint_mismatch'):
            SessionRecovery(f.box,f.dispatcher,endpoint='https://different.test').run()
        f.transport.metadata_read.assert_not_called()

    def historical_ack(self,f,**changes):
        row=f.box.db.execute("SELECT * FROM requests WHERE kind='close'").fetchone()
        body=json.loads(row['body'])
        proof={'event_id':f.event,'session_id':f.session,'sequence':body['sequence'],
               'receipt_status':'committed_exact','submitted_sha256':'a'*64,
               'accepted_operation':{'sha256':'a'*64,'body':{**body,'timestamp_end':body['timestamp_end'].replace('+00:00','Z')}},
               'lifecycle':{'phase':'closed','revision':body['sequence']},'recovery_blockers':[]}
        proof.update(changes)
        with f.box.transaction():f.box.db.execute("UPDATE requests SET ack=NULL WHERE kind='close'")
        f.recovery._historical_ack(f.event,row,proof)
        return proof

    def test_complete_historical_ack_after_lost_response_is_a_bound_local_noop(self):
        f=self.make();self.historical_ack(f)
        f.box.recovery_state(f.event,'reconciled','previous_session_not_resumed')
        self.assert_noop(f)

    def test_invalid_historical_operation_proof_remains_protected(self):
        changes=({'receipt_status':'not_committed'}, {'event_id':'OTHER'},
                 {'accepted_operation':{'sha256':'b'*64,'body':{}}},
                 {'lifecycle':{'phase':'interrupted'}}, {'lifecycle':{'phase':'closed','revision':999}})
        for change in changes:
            with self.subTest(change=change):
                f=self.make();self.historical_ack(f,**change);self.assert_protected(f)
        f=self.make();proof=self.historical_ack(f)
        ack=json.loads(f.box.db.execute("SELECT ack FROM requests WHERE kind='close'").fetchone()[0])
        ack['operation_receipt']['accepted_operation']['body']['complete']=False
        with f.box.transaction():f.box.db.execute("UPDATE requests SET ack=? WHERE kind='close'",(json.dumps(ack),))
        self.assert_protected(f)
