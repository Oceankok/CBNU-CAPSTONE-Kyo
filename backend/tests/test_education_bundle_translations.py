import json
import unittest
from unittest.mock import patch
from backend.tests import test_education_bundles as fixtures
from backend.db.event_repository import get_connection
from backend.zones.membership import replace_user_zones
from backend.education.models import MaterialContent


class BundleTranslationTests(unittest.TestCase):
    preview = fixtures.BundleTests.preview
    create = fixtures.BundleTests.create
    publish = fixtures.BundleTests.publish

    def setUp(self):
        fixtures.BundleTests.setUp(self)
        self.original=self.publish(self.create()).json()
        self.base='/api/education/bundles/'+self.original['bundle_id']+'/translations'

    def translation(self,language='en'):
        return self.client.post(self.base,headers=self.admin,json={
            'language':language,'expected_source_version':self.original['version'],
            'title':'Combined helmet training','body':'Inspect your helmet before work.','checklist':['Check chin strap']})

    def publish_translation(self,row):
        return self.client.post('/api/education/translations/'+row['translation_id']+'/publish',headers=self.admin,
            json={'approved':True,'expected_version':row['version']})

    def worker_items(self,language='en'):
        response=self.client.get('/api/worker/education-bundles',headers=self.worker,params={'language':language})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['items']

    def test_worker_translation_keeps_membership_and_review_boundaries(self):
        row=self.translation().json()
        self.assertEqual(row['source_kind'],'bundle')
        self.assertIsNone(row['material_id'])
        self.assertEqual(row['bundle_id'],self.original['bundle_id'])
        self.assertEqual(self.worker_items()[0]['language'],'ko')
        self.assertTrue(self.worker_items()[0]['translation_fallback'])
        self.assertEqual(self.publish_translation(row).status_code,200)
        result=self.worker_items()[0]
        self.assertEqual(result['title'],'Combined helmet training')
        self.assertEqual(result['language'],'en')
        self.assertFalse(result['translation_fallback'])
        self.assertEqual(result['assigned_zones'],['프레스 구역'])
        for private in ('sources','confirmed_count','priority_score','snapshot','created_by','자재 이동 구역'):
            self.assertNotIn(private,json.dumps(result,ensure_ascii=False))
        self.assertEqual(self.worker_items('fr')[0]['language'],'ko')
        with get_connection() as conn:
            replace_user_zones(conn,'worker01',['조립 구역'])
        self.assertEqual(self.worker_items(),[])

    def test_original_lifecycle_invalidates_translation_and_never_changes_original(self):
        row=self.translation().json();self.publish_translation(row)
        path='/api/education/bundles/'+self.original['bundle_id']
        self.assertEqual(self.client.get(path,headers=self.admin).json(),self.original)
        self.assertEqual(self.client.post(path+'/unpublish',headers=self.admin,json={'expected_version':1}).status_code,200)
        self.assertEqual(self.worker_items(),[])
        stale=self.client.get('/api/education/translations/'+row['translation_id'],headers=self.admin).json()
        self.assertTrue(stale['stale'])
        self.assertEqual(self.client.post(path+'/publish',headers=self.admin,json={'expected_version':2,'approved':True}).status_code,200)
        self.assertEqual(self.worker_items()[0]['language'],'ko')
        self.assertEqual(self.translation().status_code,409)

    def test_source_snapshot_filter_cannot_be_bypassed_by_translation(self):
        row=self.translation().json();self.publish_translation(row)
        with get_connection() as conn:
            conn.execute("UPDATE education_recommendation SET education_topic='changed' WHERE recommendation_id='A'")
        self.assertEqual(self.worker_items(),[])

    def test_generation_uses_content_only_and_revalidates_bundle(self):
        content=MaterialContent(title='Translated',body='Text',checklist=['Check'])
        payload={'language':'en','expected_source_version':1,'allow_external_processing':True}
        with patch('backend.education.translation_provider.generate',return_value=(content,'test-model')) as generate:
            result=self.client.post(self.base+'/generate',headers=self.admin,json=payload)
            self.assertEqual(result.status_code,201,result.text)
            sent,language=generate.call_args.args
            self.assertEqual(sent.model_dump(),fixtures.CONTENT)
            self.assertEqual(language,'en')
            self.assertEqual(result.json()['status'],'draft')
        def mutate(*args):
            self.client.post('/api/education/bundles/'+self.original['bundle_id']+'/unpublish',headers=self.admin,json={'expected_version':1})
            return content,'test-model'
        with patch('backend.education.translation_provider.generate',side_effect=mutate):
            self.assertEqual(self.client.post(self.base+'/generate',headers=self.admin,json=payload).status_code,409)
        self.assertEqual(len(self.client.get(self.base,headers=self.admin).json()['items']),1)

    def test_permissions_validation_and_unique_publication(self):
        self.assertEqual(self.client.get(self.base,headers=self.worker).status_code,403)
        self.assertEqual(self.translation('ko').status_code,422)
        first=self.translation().json();second=self.translation().json()
        self.assertEqual(self.publish_translation(first).status_code,200)
        self.assertEqual(self.publish_translation(second).status_code,200)
        rows=self.client.get(self.base,headers=self.admin).json()['items']
        self.assertEqual(sorted(r['status'] for r in rows),['published','superseded'])
        self.assertEqual(self.publish_translation(second).status_code,409)
        self.assertEqual(self.client.get('/api/worker/education-bundles?language=en_US',headers=self.worker).status_code,422)

    def test_bundle32_to_translation34_preserves_published_bundle(self):
        from backend.db.connection import is_postgres, schema_version
        from backend.db.init_db import init_database
        if is_postgres():
            self.skipTest('SQLite version simulation; real PostgreSQL migration pending')
        with get_connection() as conn:
            conn.execute('DROP TABLE education_translation')
            conn.execute('PRAGMA user_version=32')
        init_database()
        row=self.translation().json()
        self.assertEqual(self.publish_translation(row).status_code,200)
        init_database()
        self.assertEqual(self.worker_items()[0]['language'],'en')
        self.assertEqual(self.client.get('/api/education/bundles/'+self.original['bundle_id'],headers=self.admin).json(),self.original)
        with get_connection() as conn:
            self.assertEqual(schema_version(conn),34)
