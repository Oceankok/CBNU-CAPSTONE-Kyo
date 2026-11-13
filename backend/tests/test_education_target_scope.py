"""Opt-in editor target binding; old clients retain compatibility, not protection."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from backend.tests import test_education_materials as fixtures
from backend.db.event_repository import get_connection
from backend.education.models import MaterialContent


class EducationTargetScopeTests(unittest.TestCase):
    setUp=fixtures.EducationMaterialTests.setUp
    insert_recommendation=staticmethod(fixtures.EducationMaterialTests.insert_recommendation)
    draft=fixtures.EducationMaterialTests.draft

    def scope(self):
        with get_connection() as conn:
            return dict(conn.execute("SELECT recommendation_id,quarter,zone_name,ppe_type,education_topic FROM education_recommendation WHERE recommendation_id='REC_CURRENT'").fetchone())

    def reassign(self):
        with get_connection() as conn:
            conn.execute("UPDATE education_recommendation SET zone_name='자재 이동 구역' WHERE recommendation_id='REC_CURRENT'")

    def test_matching_scope_manual_save_keeps_selected_target(self):
        scope=self.scope();response=self.draft(expected_scope=scope)
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()['zone_name'],scope['zone_name'])
        self.assertNotIn('expected_scope',response.json())

    def test_scope_can_be_built_from_list_and_preserves_unassigned_zone(self):
        with get_connection() as conn:
            conn.execute("UPDATE education_recommendation SET zone_name='' WHERE recommendation_id='REC_CURRENT'")
        result=self.client.get('/api/recommendations',headers=self.admin,params={'quarter':'2026-Q3'}).json()
        selected=next(item for item in result['items'] if item['recommendation_id']=='REC_CURRENT')
        scope={key:selected[key] for key in ('recommendation_id','zone_name','ppe_type','education_topic')}
        scope['quarter']=result['quarter']
        response=self.draft(expected_scope=scope)
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()['zone_name'],'')
        self.reassign()
        self.assertEqual(self.draft(expected_scope=scope).status_code,409)

    def test_stale_manual_target_is_rejected_without_writing(self):
        scope=self.scope();self.reassign()
        response=self.draft(expected_scope=scope)
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()['detail'],'education_recommendation_changed')
        with get_connection() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM education_material').fetchone()[0],0)

    def test_deleted_target_and_path_scope_mismatch_rejected(self):
        scope=self.scope()
        self.assertEqual(self.draft('REC_OTHER',expected_scope=scope).status_code,409)
        with get_connection() as conn:conn.execute("DELETE FROM education_recommendation WHERE recommendation_id='REC_CURRENT'")
        self.assertEqual(self.draft(expected_scope=scope).status_code,409)
        self.assertEqual(self.draft().status_code,404)

    def test_old_manual_body_remains_compatible_but_not_stale_protected(self):
        self.reassign();response=self.draft()
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.json()['zone_name'],'자재 이동 구역')
        self.assertEqual(self.draft(expected_scope=None).status_code,201)

    def test_stale_generation_rejected_before_provider_or_paid_work(self):
        scope=self.scope();self.reassign()
        with patch('backend.education.provider.settings') as settings:
            response=self.client.post('/api/recommendations/REC_CURRENT/materials/generate',headers=self.admin,json={'expected_scope':scope})
            self.assertEqual(response.status_code,409)
            settings.assert_not_called()

    def test_generation_no_body_compatible_and_race_still_guarded(self):
        content=MaterialContent(title='safe',body='body',checklist=['check'])
        with patch('backend.education.provider.settings',return_value=SimpleNamespace(provider='mock')),patch('backend.education.provider.generate',return_value=(content,'test-model')):
            response=self.client.post('/api/recommendations/REC_CURRENT/materials/generate',headers=self.admin)
            self.assertEqual(response.status_code,201,response.text)
        scope=self.scope()
        def generate(_):
            self.reassign();return content,'test-model'
        with patch('backend.education.provider.settings',return_value=SimpleNamespace(provider='mock')),patch('backend.education.provider.generate',side_effect=generate):
            response=self.client.post('/api/recommendations/REC_CURRENT/materials/generate',headers=self.admin,json={'expected_scope':scope})
            self.assertEqual(response.status_code,409)
        with get_connection() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM education_material').fetchone()[0],1)

    def test_scope_schema_is_strict_and_incomplete_snapshot_rejected(self):
        scope=self.scope();scope.pop('quarter')
        self.assertEqual(self.draft(expected_scope=scope).status_code,422)
        scope=self.scope();scope['rank']=1
        self.assertEqual(self.draft(expected_scope=scope).status_code,422)
