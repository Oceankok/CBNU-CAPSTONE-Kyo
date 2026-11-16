"""Actual HTTP/temporary DB contracts; fake published bytes are not a redaction test."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.tests import test_continuous_events as fixtures
from backend.db.event_repository import get_connection
from backend.processing import lifecycle
from backend.auto_decision.rules import evaluate, Criteria


class ManualIncidentTests(unittest.TestCase):
    setUp=fixtures.ContinuousEventTests.setUp
    ts=fixtures.ContinuousEventTests.ts
    start=fixtures.ContinuousEventTests.start
    progress=fixtures.ContinuousEventTests.progress
    segment=fixtures.ContinuousEventTests.segment

    def manifest(self,camera,sequence=0,start=-2,end=5):
        return self.client.post(self.base+'/'+self.ident+'/segments',headers=self.nodes[0],json={
            'session_id':self.session,'camera_id':camera,'sequence':sequence,
            'capture_start_at':self.ts(start),'capture_end_at':self.ts(end)})

    def report(self,camera,**changes):
        body=dict(session_id=self.session,sequence=1,state='incomplete',
                  captured_ranges=[{'start_at':self.ts(0),'end_at':self.ts(3)}],
                  missing_ranges=[{'start_at':self.ts(-2),'end_at':self.ts(0),'reason':'missing_preroll'}],
                  time_basis='source_utc_unverified',clock_offset_seconds=None,
                  clock_uncertainty_seconds=None,reason='missing_preroll')
        return self.client.post(self.base+'/'+self.ident+'/cameras/'+camera+'/capture-reports',
                                headers=self.nodes[0],json={**body,**changes})

    def test_legacy_canonical_and_manual_mode_are_distinct(self):
        legacy=lifecycle.canonical(lifecycle.Start.model_validate(self.body))
        self.assertNotIn('evidence_mode',json.loads(legacy))
        self.assertEqual(self.start(evidence_mode='manual_review',review_only=False,confidence=.9).status_code,422)
        self.start(evidence_mode='manual_review').raise_for_status()
        self.assertEqual(self.start(evidence_mode='manual_review').status_code,200)
        self.assertEqual(self.start().status_code,409)

    def test_unknown_clock_only_manual_and_no_fabricated_zero(self):
        self.start().raise_for_status()
        self.assertEqual(self.report('CAM_001').status_code,422)
        with get_connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM processing_camera_capture WHERE event_id=?',(self.ident,)).fetchone()[0],0)

    def test_twenty_second_monotonic_incident_camera_scoped_partial_segments(self):
        self.start(evidence_mode='manual_review').raise_for_status()
        self.progress(end=20).raise_for_status()
        self.assertEqual(self.progress(end=20).status_code,200)
        self.assertEqual(self.progress(sequence=2,end=19).status_code,422)
        a=self.manifest('CAM_001',start=0,end=10);a.raise_for_status()
        self.manifest('CAM_001',1,11,22).raise_for_status()
        ref=self.manifest('CAM_002',start=2,end=18);ref.raise_for_status()
        retry=self.manifest('CAM_001',start=0,end=10)
        retry.raise_for_status()
        self.assertEqual(len(retry.json()['segments']),3)
        retried=next(s for s in retry.json()['segments'] if s['camera_id']=='CAM_001' and s['sequence']==0)
        self.assertEqual(retried,a.json()['segments'][0])
        self.assertEqual(self.manifest('CAM_001',1,9,22).status_code,409)
        self.assertEqual(self.manifest('CAM_999').status_code,404)
        end=self.progress(sequence=2,end=20,kind='close',close_reason='source_gap',complete=False,final_segment_count=2)
        end.raise_for_status()
        self.assertEqual(self.progress(sequence=3,end=21).status_code,409)
        event=self.client.get('/api/events/'+self.ident,headers=self.admin).json()['event']
        self.assertEqual(event['camera_id'],'CAM_001')
        self.assertEqual(event['duration_sec'],20)
        roles={(x['camera_id'],x['role']) for x in end.json()['segments']}
        self.assertEqual(roles,{('CAM_001','clip'),('CAM_002','reference')})
        with get_connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM candidate_event WHERE event_id=?',(self.ident,)).fetchone()[0],1)

    def test_partial_reference_playable_without_becoming_origin_or_automatic(self):
        self.start(evidence_mode='manual_review').raise_for_status()
        parts=self.manifest('CAM_002',start=0,end=3).json()['segments']
        self.progress(kind='close',end=3,close_reason='source_gap',complete=False,final_segment_count=0).raise_for_status()
        self.report('CAM_001',captured_ranges=[],missing_ranges=[{'start_at':self.ts(-2),'end_at':self.ts(5),'reason':'source_missing'}],reason='source_missing').raise_for_status()
        self.report('CAM_002').raise_for_status()
        folder=Path(self.enterContext(TemporaryDirectory()));file=folder/'redacted.mp4';file.write_bytes(b'TEST published bytes only')
        self.enterContext(patch('backend.media.paths.owned_path',return_value=file))
        self.enterContext(patch('backend.media.service.owned_path',return_value=file))
        with get_connection() as c:
            c.execute("INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,created_at,request_id,capture_start_at,capture_end_at) VALUES(?,?,?,'video','reference',?,'ready','complete',CURRENT_TIMESTAMP,?,?,?)",
                      (str(uuid4()),self.ident,'CAM_002','storage/test.mp4',parts[0]['request_id'],self.ts(0),self.ts(3)))
            c.execute("UPDATE event_clip_request SET status='received' WHERE request_id=?",(parts[0]['request_id'],))
        event=self.client.get('/api/events/'+self.ident,headers=self.admin).json()['event']
        self.assertEqual(event['camera_id'],'CAM_001')
        self.assertTrue(event['review_availability']['can_review'])
        bycam={x['camera_id']:x for x in event['collection']['cameras']}
        self.assertEqual(bycam['CAM_001']['evidence_status'],'unavailable')
        self.assertEqual(bycam['CAM_002']['evidence_status'],'partial')
        self.assertIsNone(event['video_clip_path'] or None)
        self.assertTrue(event['media'][0]['url'])
        review=self.client.post('/api/events/'+self.ident+'/review',headers=self.admin,json={
            'review_result':'confirmed','review_reason_code':'other','review_comment':'Reviewed surviving reference; primary unavailable'})
        self.assertEqual(review.status_code,200,review.text)

    def test_complete_unknown_time_keeps_manual_auto_exclusion(self):
        self.start(evidence_mode='manual_review').raise_for_status();self.segment().raise_for_status()
        self.progress(kind='close',end=3,close_reason='ppe_present',complete=True,final_segment_count=1).raise_for_status()
        self.report('CAM_001',state='complete',missing_ranges=[],reason=None,
                    captured_ranges=[{'start_at':self.ts(-2),'end_at':self.ts(5)}]).raise_for_status()
        self.assertEqual(self.report('CAM_002',clock_uncertainty_seconds=0).status_code,422)
        with get_connection() as c:
            diagnostic={'blocking_reasons':[], 'whole_event_evidence':{}}
            decision=evaluate(c,self.ident,Criteria(minimum_observed_seconds=3.,minimum_frame_samples=46),diagnostic)
            self.assertFalse(decision['eligible'])
            self.assertIn('manual_review_evidence_not_automatic',decision['blocking_reasons'])

    def test_partial_unredacted_media_never_has_public_url(self):
        self.start(evidence_mode='manual_review').raise_for_status();self.segment().raise_for_status()
        self.progress(kind='close',end=3,close_reason='source_gap',complete=False,final_segment_count=1).raise_for_status()
        with get_connection() as c:
            c.execute("INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,created_at) VALUES(?,?,?,'video','clip',?,'failed','failed',CURRENT_TIMESTAMP)",
                      (str(uuid4()),self.ident,'CAM_001','storage/unverified.mp4'))
        event=self.client.get('/api/events/'+self.ident,headers=self.admin).json()['event']
        self.assertFalse(event['review_availability']['can_review'])
        self.assertTrue(all(m['url'] is None for m in event['media']))
