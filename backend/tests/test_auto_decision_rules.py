"""Real DB/hook tests with synthetic media; not detector accuracy validation."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch

from backend.auto_decision import settings, writer
from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.tests import test_continuous_events as continuous
from backend.tests import test_capture_shadow_refresh as capture


class ContinuousRuleTests(TestCase):
    setUp = continuous.ContinuousEventTests.setUp
    ts = continuous.ContinuousEventTests.ts
    start = continuous.ContinuousEventTests.start
    segment = continuous.ContinuousEventTests.segment
    progress = continuous.ContinuousEventTests.progress
    capture_report = continuous.ContinuousEventTests.capture_report
    ready_media = capture.CaptureShadowRefreshTests.ready_media
    complete = capture.CaptureShadowRefreshTests.complete
    history = capture.CaptureShadowRefreshTests.history
    assert_no_review = capture.CaptureShadowRefreshTests.assert_no_review

    def body_policy(self, mode='enforce', version=0):
        return {'mode':mode, 'decision_method':'continuous_rules_v1',
                'rule_criteria':{'minimum_observed_seconds':3.0,'minimum_frame_samples':46},
                'model_versions':['test'],'ppe_types':['helmet'],'camera_ids':['CAM_001'],
                'validation_reference':'synthetic-rule-test-not-field-validation',
                'expected_version':version,'reason':'synthetic rule test'}

    def save(self, mode='enforce', version=0, **changes):
        return self.client.put('/api/auto-decisions/settings',headers=self.admin,
                               json={**self.body_policy(mode,version),**changes})

    def prepare(self, mode='enforce', **event_changes):
        self.save(mode).raise_for_status()
        self.start(**event_changes).raise_for_status()
        self.segments=self.segment().raise_for_status().json()['segments']
        self.progress(kind='close',end=3,close_reason='ppe_present',complete=True,
                      final_segment_count=1).raise_for_status()
        self.folder=Path(self.enterContext(TemporaryDirectory()))
        self.files={}
        for target in ('backend.media.paths.owned_path','backend.media.service.owned_path',
                       'backend.auto_decision.routes.owned_path'):
            self.enterContext(patch(target,side_effect=self.files.__getitem__))
        self.enterContext(patch.dict(os.environ,{'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS':'0'}))

    def hook(self):
        with get_connection() as conn:
            if getattr(conn,'dialect','sqlite')=='postgresql':
                conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            begin_write(conn)
            return settings.process_ready(conn,self.ident)

    def review(self):
        with get_connection() as conn:
            row=conn.execute('SELECT * FROM event_review WHERE event_id=?',(self.ident,)).fetchone()
            return dict(row) if row else None

    def preview(self, **changes):
        body={k:v for k,v in self.body_policy('shadow').items() if k not in {'expected_version','reason'}}
        return self.client.post('/api/auto-decisions/settings/preview',headers=self.admin,
                                json={**body,'event_ids':[self.ident],**changes}).raise_for_status().json()['items'][0]

    def ready_shadow(self):
        self.prepare('shadow')
        self.ready_media()
        self.complete('CAM_001');self.complete('CAM_002')
        self.assert_no_review()

    def test_media_first_last_capture_confirms_once_without_inventing_confidence(self):
        self.prepare()
        with get_connection() as conn:
            retention=dict(conn.execute('SELECT * FROM event_retention WHERE event_id=?',(self.ident,)).fetchone())
        self.ready_media();self.complete('CAM_001')
        self.assert_no_review()
        result=self.complete('CAM_002')
        self.assertTrue(result['auto_decision_enabled'])
        review=self.review();self.assertEqual(review['review_result'],'confirmed')
        self.assertIsNone(review['reviewer_id']);self.assertEqual(review['second_review_needed'],1)
        audit=json.loads(review['review_comment'])
        self.assertEqual(audit['source'],'automatic_continuous_rules_v1')
        self.assertEqual(audit['settings_version'],1)
        self.assertEqual(audit['policy_configured_by'],'admin02')
        self.assertIsNone(audit['rule_evaluation']['score'])
        self.complete('CAM_002');self.assertFalse(self.hook()['applied'])
        self.assertEqual(self.review(),review)
        with get_connection() as conn:
            self.assertEqual(dict(conn.execute('SELECT * FROM event_retention WHERE event_id=?',(self.ident,)).fetchone()),retention)
        detail=self.client.get('/api/events/'+self.ident,headers=self.admin).raise_for_status().json()['event']
        self.assertEqual(detail['event_status'],'confirmed')
        self.assertTrue(detail['review_only']);self.assertIsNone(detail['ai_confidence'])
        self.assertTrue(detail['auto_decision_enabled'])

    def test_capture_first_last_receipt_confirms(self):
        self.prepare()
        self.complete('CAM_001');self.complete('CAM_002')
        self.assert_no_review()
        self.ready_media()
        self.assertEqual(self.review()['review_result'],'confirmed')

    def test_shadow_and_unsaved_preview_share_rules_without_review_or_probability(self):
        self.ready_shadow()
        item=self.preview()
        self.assertTrue(item['threshold_candidate'])
        self.assertFalse(item['automatic_confirmation_allowed'])
        self.assertIsNone(item['observed_confidence'])
        self.assertEqual(item['rule_evaluation'],self.history()[0]['result']['rule_evaluation'])
        self.assertFalse(self.preview(mode='off',camera_ids=[])['threshold_candidate'])
        self.assertFalse(self.preview(zone_names=['another-zone'])['threshold_candidate'])
        history=self.history()
        self.complete('CAM_002')
        self.assertEqual(self.history(),history)
        with get_connection() as conn:
            saved=json.loads(conn.execute('''SELECT policy_json FROM auto_decision_shadow_run r
                JOIN auto_decision_shadow_item i ON i.run_id=r.run_id WHERE i.event_id=?''',
                (self.ident,)).fetchone()[0])
        self.assertEqual(saved['decision_method'],'continuous_rules_v1')
        self.assertEqual(saved['rule_criteria'],{**self.body_policy()['rule_criteria'],
                                                'allow_terminal_helmet_rewear_gap':False})
        self.assert_no_review()

    def test_old_client_cannot_silently_replace_rule_policy_but_can_switch_off(self):
        self.save('shadow').raise_for_status()
        body={k:v for k,v in self.body_policy('shadow',1).items()
              if k not in {'decision_method','rule_criteria'}}
        response=self.client.put('/api/auto-decisions/settings',headers=self.admin,json=body)
        self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(response.json()['detail'],'explicit_decision_method_required')
        response=self.client.put('/api/auto-decisions/settings',headers=self.admin,json={**body,'mode':'off'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()['automatic_confirmation_enabled'])

    def test_rules_are_explicit_strict_and_do_not_activate_the_model_path(self):
        for changes in ({'rule_criteria':None},
                        {'rule_criteria':{'minimum_observed_seconds':True,'minimum_frame_samples':46}},
                        {'rule_criteria':{'minimum_observed_seconds':2,'minimum_frame_samples':46}},
                        {'rule_criteria':{'minimum_observed_seconds':3,'minimum_frame_samples':True}},
                        {'ppe_types':['gloves']},
                        {'decision_method':'validated_model'}):
            with self.subTest(changes=changes):self.assertEqual(self.save(**changes).status_code,422)
        self.assertEqual(self.save(validation_reference=None).status_code,409)
        self.assertEqual(self.save(decision_method='validated_model',rule_criteria=None).status_code,409)
        self.assertEqual(self.save().status_code,200)
        self.assertEqual(self.save().status_code,409)

    def test_missing_evidence_and_bad_observations_abstain(self):
        self.ready_shadow()
        cases=[('processing_event_lifecycle','gap_count',1,'continuous_observation_incomplete'),
               ('processing_event_lifecycle','gap_seconds',.1,'continuous_observation_incomplete'),
               ('processing_event_lifecycle','observed_seconds',2,'minimum_observed_seconds_not_met'),
               ('processing_event_lifecycle','phase','interrupted','closed_continuous_event_required'),
               ('processing_event_lifecycle','close_reason','observation_gap','abnormal_event_close_requires_review'),
               ('candidate_event','frame_sample_count',45,'minimum_frame_samples_not_met'),
               ('candidate_event','tracking_id',None,'tracked_absence_observation_required'),
               ('candidate_event','ppe_detected',1,'tracked_absence_observation_required')]
        self.save(version=1).raise_for_status()
        for table,column,value,reason in cases:
            with self.subTest(column=column),get_connection() as conn:
                old=conn.execute(f'SELECT {column} FROM {table} WHERE event_id=?',(self.ident,)).fetchone()[0]
                conn.execute(f'UPDATE {table} SET {column}=? WHERE event_id=?',(value,self.ident))
            result=self.hook();self.assertFalse(result['applied'])
            self.assertIn(reason,result['blocking_reasons'])
            with get_connection() as conn:
                conn.execute(f'UPDATE {table} SET {column}=? WHERE event_id=?',(old,self.ident))
        next(iter(self.files.values())).unlink()
        self.assertFalse(self.hook()['applied'])
        self.assert_no_review()

    def test_saved_suspected_relation_blocks_rule(self):
        self.ready_shadow()
        from backend.event_relations import service
        original=service.automatic_tag
        def saved(conn,event_id):
            return {**original(conn,event_id),'saved_record_count':1}
        with patch.object(service,'automatic_tag',side_effect=saved):
            self.assertIn('suspected_duplicate_requires_review',self.preview()['blocking_reasons'])

    def test_terminal_helmet_gap_can_be_previewed_and_enforced_only_when_enabled(self):
        self.check_terminal_helmet_gap(.267, eligible=True)

    def test_terminal_helmet_gap_uses_300ms_wire_boundary(self):
        # A nine-frame gap at 30 fps can round above 300 ms in epoch floats.
        self.check_terminal_helmet_gap(.3000001907348633, eligible=True)

    def test_terminal_helmet_gap_rejects_one_microsecond_over_wire_boundary(self):
        self.check_terminal_helmet_gap(.300001, eligible=False)

    def check_terminal_helmet_gap(self, gap_seconds, *, eligible):
        criteria={**self.body_policy()['rule_criteria'],'allow_terminal_helmet_rewear_gap':True}
        self.save('shadow',rule_criteria=criteria).raise_for_status()
        self.start().raise_for_status()
        observed=dict(end=4,observed_seconds=4-gap_seconds,gap_seconds=gap_seconds,gap_count=1)
        self.progress(**observed).raise_for_status()
        self.segments=self.segment(end=6).raise_for_status().json()['segments']
        gap=dict(start_at=self.ts(3.3),end_at=self.ts(3.3+gap_seconds),rewear_at=self.ts(4.5),samples_before=50)
        close=dict(kind='close',close_reason='ppe_present',complete=True,final_segment_count=1,**observed)
        bad={**gap,'end_at':self.ts(3.3+gap_seconds+.01)}
        self.assertEqual(self.progress(sequence=2,**close,rewear_gap=bad).status_code,422)
        self.progress(sequence=2,**close,rewear_gap=gap).raise_for_status()
        self.progress(sequence=2,**close,rewear_gap=gap).raise_for_status()
        self.folder=Path(self.enterContext(TemporaryDirectory()))
        self.files={}
        for target in ('backend.media.paths.owned_path','backend.media.service.owned_path',
                       'backend.auto_decision.routes.owned_path'):
            self.enterContext(patch(target,side_effect=self.files.__getitem__))
        self.enterContext(patch.dict(os.environ,{'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS':'0'}))
        self.ready_media()
        with get_connection() as conn:
            conn.execute('UPDATE processing_segment_validation SET frame_count=120,duration_seconds=8')
        for camera in ('CAM_001','CAM_002'):
            self.capture_report(camera,state='complete',
                captured_ranges=[dict(start_at=self.ts(-2),end_at=self.ts(6))]).raise_for_status()
        self.assert_no_review()
        self.assertIn('continuous_observation_incomplete',self.preview()['blocking_reasons'])
        preview=self.preview(rule_criteria=criteria)
        self.assertEqual(preview['rule_evaluation']['allowed_rewear_gap'] is not None,eligible)
        self.assertEqual(preview['threshold_candidate'],eligible)
        from backend.event_relations import service
        with patch.object(service,'automatic_tag',return_value={'candidate_count':1,'saved_record_count':0}):
            self.assertIn('suspected_duplicate_requires_review',
                          self.preview(rule_criteria=criteria)['blocking_reasons'])
        self.save(version=1,rule_criteria=criteria).raise_for_status()
        self.assertEqual(self.hook()['applied'],eligible)
        review=self.review()
        self.assertEqual(review['review_reason_code'] if review else None,
                         'automatic_policy_confirmed' if eligible else None)

    def test_admin_correction_preserves_auto_history_and_recounts_false_positive(self):
        self.prepare();self.complete('CAM_001');self.complete('CAM_002');self.ready_media()
        from backend.db.event_repository import generate_quarterly_stats
        before=generate_quarterly_stats('2026-Q4')['summary']['confirmed_count']
        review=self.review()
        result=self.client.put('/api/events/'+self.ident+'/review',headers=self.admin,
            json={'review_result':'false_positive','review_reason_code':'other','review_comment':'human correction'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertFalse(self.hook()['applied'])
        with get_connection() as conn:
            old=conn.execute('SELECT review_comment FROM event_review_history WHERE event_id=?',(self.ident,)).fetchone()[0]
            self.assertEqual(old,review['review_comment'])
            self.assertEqual(conn.execute('SELECT COALESCE(SUM(false_positive_count),0) FROM false_positive_aggregate').fetchone()[0],1)
        self.assertEqual(self.review()['reviewer_id'],'admin02')
        self.assertEqual(generate_quarterly_stats('2026-Q4')['summary']['confirmed_count'],before-1)

    def test_computed_duplicate_and_deleted_evidence_block_enforcement(self):
        self.ready_shadow()
        from uuid import uuid4
        peer='EVT_'+uuid4().hex
        body={key:value for key,value in self.body.items() if key not in
              {'session_id','policy_key','observed_seconds','gap_seconds','gap_count'}}
        self.client.post('/api/processing/events',headers=self.nodes[0],
                         json={**body,'event_id':peer,'camera_id':'CAM_002'}).raise_for_status()
        self.save(version=1).raise_for_status()
        self.assertIn('suspected_duplicate_requires_review',self.hook()['blocking_reasons'])
        with get_connection() as conn:
            conn.execute("UPDATE event_retention SET decision='delete' WHERE event_id=?",(self.ident,))
        self.assertIn('media_deleted_or_expired',self.hook()['blocking_reasons'])
        self.assert_no_review()

    def test_legacy_absence_event_never_qualifies_without_continuous_provenance(self):
        self.start().raise_for_status()
        with get_connection() as conn:
            conn.execute('DELETE FROM processing_event_lifecycle WHERE event_id=?',(self.ident,))
        item=self.preview()
        self.assertIn('closed_continuous_event_required',item['blocking_reasons'])
        self.assertFalse(item['rule_evaluation']['eligible'])

    def test_human_hold_is_never_overwritten(self):
        self.ready_shadow()
        self.client.post('/api/events/'+self.ident+'/review',headers=self.admin,
                         json={'review_result':'hold','review_reason_code':'unclear'}).raise_for_status()
        self.save(version=1).raise_for_status()
        self.assertFalse(self.hook()['applied'])
        self.assertEqual(self.review()['review_result'],'hold')

    def test_concurrent_completion_only_one_review(self):
        self.ready_shadow();self.save(version=1).raise_for_status()
        barrier=Barrier(2)
        def run():
            barrier.wait(timeout=10)
            return self.hook()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=[future.result(timeout=20) for future in [pool.submit(run),pool.submit(run)]]
        self.assertEqual(sum(item['applied'] for item in results),1)

    def test_failed_finalizer_rolls_back_and_capture_retry_recovers(self):
        self.prepare();self.ready_media();self.complete('CAM_001')
        original=writer.confirm
        def fail(*args):
            original(*args)
            raise RuntimeError('synthetic rollback after review insert')
        with patch.object(writer,'confirm',side_effect=fail),self.assertLogs('backend.processing.capture',level='WARNING'):
            self.complete('CAM_002')
        self.assert_no_review()
        self.complete('CAM_002')
        self.assertEqual(self.review()['review_result'],'confirmed')

    def test_off_switch_stops_already_configured_rule_and_does_not_backfill(self):
        self.prepare();self.save('off',version=1).raise_for_status()
        self.complete('CAM_001');self.complete('CAM_002');self.ready_media()
        self.assert_no_review()
        self.save(version=2).raise_for_status()
        self.assert_no_review()  # Saving settings does not scan historical events.
        self.assertTrue(self.hook()['applied'])
