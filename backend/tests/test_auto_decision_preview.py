from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import unittest

from backend.tests.support import isolated_api, login
from backend.db.event_repository import get_connection


class AutoDecisionPreviewTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        token = self.client.post('/api/nodes',headers=self.admin,json={'node_id':'preview-node','name':'test'}).json()['token']
        self.node = {'Authorization':'Bearer '+token}
        self.client.put('/api/processing/workers/preview-node',headers=self.admin,json={'enabled':True}).raise_for_status()
        self.ident='EVT_'+uuid4().hex
        stamp=datetime.now(timezone.utc).isoformat()
        body={'event_id':self.ident,'camera_id':'CAM_001','ppe_type':'helmet','timestamp_start':stamp,
              'timestamp_end':stamp,'confidence':0.9,'frame_sample_count':1,'model_version':'test-model'}
        response=self.client.post('/api/processing/events',headers=self.node,json=body)
        self.assertEqual(response.status_code,201,response.text)
        request=response.json()['clip_requests'][0]['request_id']
        self.directory=self.enterContext(TemporaryDirectory())
        self.video=Path(self.directory)/'fixture.mp4';self.video.write_bytes(b'synthetic verified fixture')
        self.enterContext(patch('backend.auto_decision.routes.owned_path',return_value=self.video))
        with get_connection() as conn:
            conn.execute("UPDATE event_clip_request SET status='received' WHERE event_id=?",(self.ident,))
            conn.execute("INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,created_at,request_id) VALUES(?,?,?,'video','clip','storage/fake.mp4','ready','complete',CURRENT_TIMESTAMP,?)",('preview-media',self.ident,'CAM_001',request))
        self.policy={'event_ids':[self.ident],'threshold':0.9,'model_versions':['test-model'],'ppe_types':['helmet'],'policy_label':'test-policy'}

    def preview(self,**updates):
        return self.client.post('/api/auto-decisions/preview',headers=self.admin,json={**self.policy,**updates})

    def item(self,**updates):
        response=self.preview(**updates);self.assertEqual(response.status_code,200,response.text)
        return response.json()['items'][0]

    def state(self):
        with get_connection() as conn:
            return {table:[dict(row) for row in conn.execute('SELECT * FROM '+table)] for table in
                    ('candidate_event','event_review','event_review_history','quarterly_summary','education_recommendation','event_retention')}

    def test_threshold_diagnostic_never_changes_state_or_enables_confirmation(self):
        before=self.state()
        response=self.preview().json()
        self.assertTrue(response['items'][0]['threshold_candidate'])
        self.assertFalse(response['automatic_confirmation_enabled'])
        self.assertFalse(response['persisted'])
        self.assertFalse(response['items'][0]['automatic_confirmation_allowed'])
        self.assertEqual(before,self.state())
        changed=self.item(threshold=0.91)
        self.assertFalse(changed['threshold_candidate'])
        self.assertNotEqual(changed['evidence_fingerprint'],response['items'][0]['evidence_fingerprint'])

    def test_review_only_and_legacy_provenance_are_ineligible(self):
        with get_connection() as conn:conn.execute('UPDATE processing_review_only_event SET review_only=1 WHERE event_id=?',(self.ident,))
        item=self.item(threshold=0);self.assertFalse(item['threshold_candidate']);self.assertIsNone(item['observed_confidence'])
        self.assertIn('review_only',item['blocking_reasons'])
        with get_connection() as conn:conn.execute('DELETE FROM processing_review_only_event WHERE event_id=?',(self.ident,))
        self.assertIn('submission_provenance_missing',self.item()['blocking_reasons'])

    def test_human_review_prevents_candidate_even_if_event_pending(self):
        with get_connection() as conn:
            conn.execute("INSERT INTO event_review(review_id,event_id,reviewer_id,review_result) VALUES('preview-review',?,'admin02','hold')",(self.ident,))
        self.assertIn('already_reviewed_or_not_pending',self.item()['blocking_reasons'])
        before=self.state();self.preview();self.assertEqual(before,self.state())

    def test_missing_failed_or_deleted_evidence_blocks(self):
        with get_connection() as conn:conn.execute("UPDATE event_clip_request SET status='failed' WHERE event_id=?",(self.ident,))
        self.assertIn('evidence_incomplete',self.item()['blocking_reasons'])
        with get_connection() as conn:
            conn.execute("UPDATE event_clip_request SET status='received' WHERE event_id=?",(self.ident,))
            conn.execute("UPDATE event_retention SET decision='delete' WHERE event_id=?",(self.ident,))
        self.assertIn('media_deleted_or_expired',self.item()['blocking_reasons'])
        self.video.unlink()
        self.assertIn('primary_redacted_video_missing',self.item()['blocking_reasons'])

    def test_scope_and_input_validation(self):
        self.assertIn('model_out_of_scope',self.item(model_versions=['other'])['blocking_reasons'])
        self.assertIn('ppe_out_of_scope',self.item(ppe_types=['vest'])['blocking_reasons'])
        for updates in ({'threshold':None},{'threshold':1.1},{'threshold':-0.1},{'model_versions':[]},{'ppe_types':['bad']},{'event_ids':[self.ident]*2},{'activate':True}):
            self.assertEqual(self.preview(**updates).status_code,422)
        self.assertEqual(self.preview(event_ids=['missing']).status_code,404)

    def test_admin_only_and_raw_paths_not_exposed(self):
        self.assertEqual(self.client.post('/api/auto-decisions/preview',json=self.policy).status_code,401)
        self.assertEqual(self.client.post('/api/auto-decisions/preview',headers=login(self.client,'worker01'),json=self.policy).status_code,403)
        text=self.preview().text
        self.assertNotIn('storage/fake',text)
        self.assertNotIn(str(self.video),text)

    def test_continuous_open_and_closed_incomplete_are_blocked(self):
        with get_connection() as conn:
            conn.execute("INSERT INTO processing_event_lifecycle(event_id,owner_node_id,session_id,policy_key,phase,start_payload,observed_seconds,gap_seconds,gap_count,updated_at) VALUES(?,'preview-node','session','policy','open','{}',3,0,0,0)",(self.ident,))
        self.assertIn('continuous_event_not_closed',self.item()['blocking_reasons'])
        with get_connection() as conn:conn.execute("UPDATE processing_event_lifecycle SET phase='closed' WHERE event_id=?",(self.ident,))
        self.assertIn('continuous_evidence_incomplete',self.item()['blocking_reasons'])
