import json
import os
import unittest
from unittest.mock import patch, MagicMock
from backend.tests import test_education_materials as fixtures
from backend.education.models import MaterialContent
from backend.education import translation_provider
from backend.education.provider import GenerationError


class TranslationTests(unittest.TestCase):
    draft = fixtures.EducationMaterialTests.draft
    publish = fixtures.EducationMaterialTests.publish
    insert_recommendation = staticmethod(fixtures.EducationMaterialTests.insert_recommendation)
    # Reuse isolated fixtures; inherited source lifecycle tests also check compatibility.
    def setUp(self):
        fixtures.EducationMaterialTests.setUp(self)
        self.original=self.draft().json()
        self.original=self.publish(self.original['material_id']).json()
        self.base='/api/education/materials/'+self.original['material_id']+'/translations'

    def translation(self, language='en'):
        return self.client.post(self.base,headers=self.admin,json={
            'language':language,'expected_source_version':self.original['version'],
            'title':'Helmet check','body':'Inspect your helmet before work.', 'checklist':['Inspect shell','Wear correctly']})

    def publish_translation(self,row,approved=True):
        return self.client.post('/api/education/translations/'+row['translation_id']+'/publish',headers=self.admin,
            json={'expected_version':row['version'],'approved':approved})

    def worker_text(self, language='en'):
        return self.client.get('/api/worker/education',headers=self.worker,params={'language':language}).json()['items'][0]['material']

    def test_translation_review_and_fallback(self):
        row=self.translation().json()
        self.assertIn(self.original['title'],self.worker_text())
        self.assertEqual(self.publish_translation(row,False).status_code,400)
        self.assertEqual(self.publish_translation(row).status_code,200)
        self.assertIn('Helmet check',self.worker_text())
        self.assertIn(self.original['title'],self.worker_text('ko'))
        self.assertIn(self.original['title'],self.worker_text('fr'))
        self.assertNotIn('admin02',self.worker_text())
        self.assertEqual(self.client.get('/api/education/materials/'+self.original['material_id'],headers=self.admin).json(),self.original)

    def test_source_revision_invalidates_translation(self):
        row=self.translation().json()
        self.assertEqual(self.publish_translation(row).status_code,200)
        mid=self.original['material_id']
        self.assertEqual(self.client.post('/api/education/materials/'+mid+'/unpublish',headers=self.admin,json={'expected_version':1}).status_code,200)
        self.assertIsNone(self.worker_text())
        self.assertEqual(self.publish(mid,version=2).status_code,200)
        self.assertIn(self.original['title'],self.worker_text())
        stored=self.client.get('/api/education/translations/'+row['translation_id'],headers=self.admin).json()
        self.assertTrue(stored['stale'])

    def test_translation_validation_permissions_and_conflicts(self):
        self.assertEqual(self.translation('ko').status_code,422)
        self.assertEqual(self.translation('../en').status_code,422)
        self.assertEqual(self.client.get(self.base,headers=self.worker).status_code,403)
        self.assertEqual(self.client.get(self.base).status_code,401)
        row=self.translation().json()
        path='/api/education/translations/'+row['translation_id']
        body={'title':'Revised','body':'Revised content','checklist':['Check'],'expected_version':9}
        self.assertEqual(self.client.patch(path,headers=self.admin,json=body).status_code,409)
        body['expected_version']=0
        self.assertEqual(self.client.patch(path,headers=self.admin,json=body).status_code,200)
        self.assertEqual(self.publish_translation(row).status_code,409)
        self.assertEqual(self.client.get('/api/worker/education?language=ENGLISH',headers=self.worker).status_code,422)

    def test_replacement_and_unpublish(self):
        first=self.translation().json(); self.publish_translation(first)
        second=self.translation().json(); self.publish_translation(second)
        rows=self.client.get(self.base,headers=self.admin).json()['items']
        self.assertEqual(sorted(r['status'] for r in rows),['published','superseded'])
        path='/api/education/translations/'+second['translation_id']+'/unpublish'
        self.assertEqual(self.client.post(path,headers=self.admin,json={'expected_version':1}).status_code,200)
        self.assertIn(self.original['title'],self.worker_text())

    def test_generation_success_is_draft_and_requires_review(self):
        content=MaterialContent(title='Generated',body='Translated body',checklist=['Check'])
        with patch('backend.education.translation_provider.generate',return_value=(content,'test-model')) as generate:
            result=self.client.post(self.base+'/generate',headers=self.admin,json={
                'language':'vi','expected_source_version':1,'allow_external_processing':True})
            self.assertEqual(result.status_code,201,result.text)
            self.assertEqual(result.json()['status'],'draft')
            self.assertEqual(result.json()['provider'],'openai')
            self.assertEqual(generate.call_count,1)
        self.assertIn(self.original['title'],self.worker_text('vi'))

    def test_v31_upgrade_and_reinitialization_preserve_source_and_translation(self):
        from backend.db.event_repository import get_connection
        from backend.db.connection import is_postgres
        from backend.db.init_db import init_database
        if is_postgres():
            self.skipTest('SQLite legacy-version simulation')
        with get_connection() as conn:
            conn.execute('DROP TABLE education_translation')
            conn.execute('PRAGMA user_version=31')
        init_database()
        row=self.translation().json()
        init_database()
        saved=self.client.get('/api/education/translations/'+row['translation_id'],headers=self.admin)
        self.assertEqual(saved.status_code,200)
        self.assertEqual(saved.json(),row)
        self.assertEqual(self.client.get('/api/education/materials/'+self.original['material_id'],headers=self.admin).json(),self.original)

    def test_generation_explicit_disabled_and_stale(self):
        body={'language':'en','expected_source_version':1}
        with patch('backend.education.translation_provider.generate') as generate:
            self.assertEqual(self.client.post(self.base+'/generate',headers=self.admin,json=body).status_code,400)
            generate.assert_not_called()
        body['allow_external_processing']=True
        with patch.dict(os.environ,{'PPE_EDUCATION_TRANSLATION_PROVIDER':''}):
            self.assertEqual(self.client.post(self.base+'/generate',headers=self.admin,json=body).status_code,503)
        content=MaterialContent(title='Translation',body='Translated body',checklist=['Check'])
        def mutate(*args):
            self.client.post('/api/education/materials/'+self.original['material_id']+'/unpublish',headers=self.admin,json={'expected_version':1})
            return content,'test-model'
        with patch('backend.education.translation_provider.generate',side_effect=mutate):
            self.assertEqual(self.client.post(self.base+'/generate',headers=self.admin,json=body).status_code,409)
        self.assertEqual(self.client.get(self.base,headers=self.admin).json()['items'],[])


class TranslationProviderTests(unittest.TestCase):
    def test_provider_sends_only_source_text_and_safe_options(self):
        source=MaterialContent(title='제목',body='본문',checklist=['확인'])
        result=MaterialContent(title='Title',body='Body',checklist=['Check'])
        response=MagicMock(status_code=200,content=b'{}')
        response.json.return_value={'status':'completed','model':'test-model','output':[{'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':result.model_dump_json()}]}]}
        with patch.dict(os.environ,{'PPE_EDUCATION_TRANSLATION_PROVIDER':'openai','PPE_EDUCATION_TRANSLATION_OPENAI_API_KEY':'test-only','PPE_EDUCATION_TRANSLATION_MODEL':'test-model'}), patch('backend.education.translation_provider.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value=response
            self.assertEqual(translation_provider.generate(source,'en'),(result,'test-model'))
            kwargs=client.return_value.__enter__.return_value.post.call_args.kwargs
            self.assertFalse(kwargs['json']['store'])
            self.assertEqual(json.loads(kwargs['json']['input']),{'target_language':'en','source_language':'ko','content':source.model_dump()})
            self.assertFalse(client.call_args.kwargs['trust_env'])
            self.assertFalse(client.call_args.kwargs['follow_redirects'])
            response.status_code=500
            with self.assertRaises(GenerationError) as exc:
                translation_provider.generate(source,'en')
            self.assertEqual(str(exc.exception),'education_translation_provider_unavailable')
