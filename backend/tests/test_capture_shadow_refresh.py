"""Isolated SQLite fixtures for capture/clip arrival order and shadow isolation."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from backend.auto_decision import settings, shadow
from backend.db.connection import begin_write
from backend.db.event_repository import get_connection
from backend.processing import receipts
from backend.tests import test_continuous_events as fixture


class CaptureShadowRefreshTests(TestCase):
    setUp = fixture.ContinuousEventTests.setUp
    ts = fixture.ContinuousEventTests.ts
    start = fixture.ContinuousEventTests.start
    segment = fixture.ContinuousEventTests.segment
    progress = fixture.ContinuousEventTests.progress
    capture_report = fixture.ContinuousEventTests.capture_report

    def prepare(self, *, shadow_enabled=True):
        self.start(review_only=False,confidence=.95).raise_for_status()
        self.segments=self.segment().raise_for_status().json()['segments']
        self.progress(kind='close',end=3,confidence=.95,close_reason='ppe_present',
                      complete=True,final_segment_count=1).raise_for_status()
        if shadow_enabled:
            policy={'mode':'shadow','threshold':.9,'model_versions':['test'],'ppe_types':['helmet'],
                    'camera_ids':['CAM_001'],'expected_version':0,'reason':'synthetic capture order'}
            self.client.put('/api/auto-decisions/settings',headers=self.admin,json=policy).raise_for_status()
        self.folder=Path(self.enterContext(TemporaryDirectory()))
        self.files={}
        self.enterContext(patch('backend.media.paths.owned_path',side_effect=self.files.__getitem__))
        self.enterContext(patch('backend.auto_decision.routes.owned_path',side_effect=self.files.__getitem__))
        self.enterContext(patch.dict(os.environ,{'PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS':'0'}))

    def ready_media(self):
        with get_connection() as conn:
            begin_write(conn)
            media={}
            for row in self.segments:
                ident=str(uuid4())
                key='storage/'+ident+'.mp4'
                self.files[key]=self.folder/ident
                self.files[key].write_bytes(b'synthetic-redacted-test')
                media[row['request_id']]=ident
                conn.execute("""INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,
                    status,redaction_status,created_at,request_id,capture_start_at,capture_end_at)
                    VALUES(?,?,?,'video',?,?,'ready','complete',CURRENT_TIMESTAMP,?,?,?)""",
                    (ident,self.ident,row['camera_id'],row['role'],key,row['request_id'],
                     row['capture_start_at'],row['capture_end_at']))
                conn.execute('''INSERT INTO processing_segment_validation(
                    media_id,generation,frame_count,fps,duration_seconds,result_sha256)
                    VALUES(?,0,105,15,7,?)''',(ident,'4'*64))
            conn.execute("UPDATE event_clip_request SET status='received' WHERE event_id=?",(self.ident,))
            for req,ident in media.items():
                receipts.record(conn,self.ident,req,ident,('5'*64,12))

    def complete(self, camera):
        return self.capture_report(camera,state='complete').raise_for_status().json()

    def history(self):
        return self.client.get('/api/auto-decisions/shadow',headers=self.admin,
            params={'event_id':self.ident}).raise_for_status().json()['items']

    def assert_no_review(self):
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT event_status FROM candidate_event WHERE event_id=?',
                (self.ident,)).fetchone()[0],'pending')
            self.assertIsNone(conn.execute('SELECT 1 FROM event_review WHERE event_id=?',
                (self.ident,)).fetchone())

    def test_media_first_last_camera_appends_and_identical_replay_dedupes(self):
        self.prepare()
        self.ready_media()
        prior,=self.history()
        self.assertFalse(prior['result']['threshold_candidate'])
        self.complete('CAM_001')
        self.assertEqual(self.history(),[prior])
        result=self.complete('CAM_002')
        latest,old=self.history()
        self.assertEqual(old,prior)
        self.assertTrue(latest['result']['threshold_candidate'])
        self.assertFalse(latest['result']['automatic_confirmation_allowed'])
        self.assertEqual(self.complete('CAM_002'),result)
        self.assertEqual(self.history(),[latest,prior])
        self.assert_no_review()

    def test_capture_first_existing_media_hook_records_once(self):
        self.prepare()
        self.complete('CAM_001')
        self.complete('CAM_002')
        self.assertEqual(self.history(),[])
        self.ready_media()
        item,=self.history()
        self.assertTrue(item['result']['threshold_candidate'])
        self.complete('CAM_002')
        self.assertEqual(self.history(),[item])
        self.assert_no_review()

    def test_shadow_failure_does_not_undo_report_and_identical_retry_recovers(self):
        self.prepare()
        self.ready_media()
        prior,=self.history()
        self.complete('CAM_001')
        original=shadow.record_policy_shadow

        def fail_after_shadow_writes(*args):
            original(*args)
            raise RuntimeError('synthetic secondary transaction rollback')

        with (patch.object(shadow,'record_policy_shadow',side_effect=fail_after_shadow_writes),
              self.assertLogs('backend.processing.capture',level='WARNING') as logged):
            accepted=self.complete('CAM_002')
        self.assertTrue(any('report commit (RuntimeError)' in item for item in logged.output))
        self.assertEqual(self.history(),[prior])
        with get_connection() as conn:
            self.assertIsNotNone(conn.execute('SELECT 1 FROM processing_camera_capture WHERE event_id=? AND camera_id=?',
                (self.ident,'CAM_002')).fetchone())
        self.assertEqual(self.complete('CAM_002'),accepted)
        ready,old=self.history()
        self.assertTrue(ready['result']['threshold_candidate'])
        self.assertEqual(old,prior)
        self.assert_no_review()

    def test_off_records_no_shadow_or_review(self):
        self.prepare(shadow_enabled=False)
        self.ready_media()
        with patch.object(shadow,'record_policy_shadow',side_effect=AssertionError('OFF must not record')) as recorder:
            self.complete('CAM_001')
            accepted=self.complete('CAM_002')
            self.assertEqual(self.complete('CAM_002'),accepted)
        recorder.assert_not_called()
        self.assertEqual(self.history(),[])
        self.assert_no_review()

    def test_capture_callback_does_not_dispatch_hypothetical_enforce_policy(self):
        self.prepare(shadow_enabled=False)
        self.ready_media()
        policy=settings.Policy(mode='enforce',model_versions=['test'],ppe_types=['helmet'],
                               camera_ids=['CAM_001'],validation_reference='test-only')
        with (patch.object(settings,'load',return_value=(policy,1,{})),
              # Public responses now inspect policy activation. Isolate that
              # read from the callback guard being tested here.
              patch.object(settings,'enabled_for_event',return_value=False),
              patch.object(settings,'activation_blockers',side_effect=AssertionError('capture cannot enforce')) as gates):
            self.complete('CAM_001')
            self.complete('CAM_002')
        gates.assert_not_called()
        self.assertEqual(self.history(),[])
        self.assert_no_review()
