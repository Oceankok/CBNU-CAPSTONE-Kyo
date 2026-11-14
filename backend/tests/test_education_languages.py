import unittest
from unittest.mock import patch
from backend.education.languages import preferences, candidates, protect_worker_response
from starlette.responses import Response
from backend.tests.test_education_translations import TranslationTests
from backend.tests.test_education_bundle_translations import BundleTranslationTests


class NegotiationTests(unittest.TestCase):
    def test_header_order_normalization_and_exclusions(self):
        self.assertEqual(preferences(None, 'fr;q=0.4, EN-us;q=0.9, en;q=0'), ('en-US', 'fr'))
        self.assertEqual(preferences(None, 'en;q=NaN, fr;q=2, *;q=1'), ('ko',))
        self.assertEqual(preferences(None, 'fr;q=0.8,en;q=0.8'), ('fr','en'))
        self.assertEqual(preferences('ko', 'en'), 'ko')
        self.assertEqual(list(candidates('en-US')), ['en-US','en'])
        self.assertEqual(preferences(None, None), ('ko',))

    def test_cache_headers_merge_and_no_usable_language(self):
        response = Response(headers={'Vary': 'Origin'})
        protect_worker_response(response)
        protect_worker_response(response)
        self.assertEqual(response.headers['Vary'], 'Origin, Accept-Language')
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(preferences(None, 'en;q=0,ko;q=0'), ('ko',))
        wildcard = Response(headers={'Vary': '*'})
        protect_worker_response(wildcard)
        self.assertEqual(wildcard.headers['Vary'], '*')


class MaterialLocaleTests(TranslationTests):
    def item(self, header=None, query=None):
        response = self.client.get('/api/worker/education',
            headers={**self.worker, **({'Accept-Language': header} if header else {})},
            params={'language': query} if query else {})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['items'][0]

    def test_region_fallback_and_metadata_without_generation(self):
        self.publish_translation(self.translation().json())
        with patch('backend.education.translation_provider.generate', side_effect=AssertionError('no generation')):
            item = self.item('fr;q=0.1,en-US;q=0.9')
            self.assertEqual((item['language'], item['requested_language'], item['translation_fallback']), ('en','en-US',True))
            self.assertIn('Helmet check', item['material'])
            self.assertEqual(self.item('en', 'ko')['language'], 'ko')
            self.assertEqual(self.item(query='en-US')['language'], 'en')
            self.assertEqual(self.item('en-US,en;q=0')['language'], 'ko')
            self.assertEqual(self.item('ko;q=1,en;q=0.5')['language'], 'ko')

    def test_worker_cache_and_q_zero_default(self):
        response = self.client.get('/api/worker/education', headers={**self.worker,
            'Accept-Language': 'en;q=0,ko;q=0', 'Origin': 'http://localhost:5173'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        self.assertEqual({v.strip().lower() for v in response.headers['vary'].split(',')}, {'origin', 'accept-language'})
        item = response.json()['items'][0]
        self.assertEqual(item['language'], 'ko')
        self.assertEqual(item['requested_language'], 'ko')
        self.assertFalse(item['translation_fallback'])

    def test_exact_region_and_next_preference(self):
        self.publish_translation(self.translation('en').json())
        self.publish_translation(self.translation('en-US').json())
        item=self.item('en-US,fr;q=0.5')
        self.assertEqual(item['language'], 'en-US')
        self.assertFalse(item['translation_fallback'])
        self.assertEqual(self.item('fr,en;q=0.5')['language'], 'en')

    def test_draft_and_stale_not_exposed(self):
        row=self.translation().json()
        self.assertEqual(self.item('en-US')['language'],'ko')
        self.publish_translation(row)
        mid=self.original['material_id']
        self.client.post('/api/education/materials/'+mid+'/unpublish', headers=self.admin,json={'expected_version':1})
        missing=self.item('en-US')
        self.assertIsNone(missing['language'])
        self.assertFalse(missing['translation_fallback'])
        self.publish(mid,version=2)
        self.assertEqual(self.item('en-US')['language'],'ko')


class BundleLocaleTests(BundleTranslationTests):
    def test_bundle_cache_and_q_zero_default(self):
        response = self.client.get('/api/worker/education-bundles', headers={**self.worker,
            'Accept-Language': 'en;q=0,ko;q=0', 'Origin': 'http://localhost:5173'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        self.assertEqual({v.strip().lower() for v in response.headers['vary'].split(',')}, {'origin', 'accept-language'})
        self.assertEqual(response.json()['items'][0]['language'], 'ko')

    def test_browser_preference_and_region_fallback(self):
        self.publish_translation(self.translation().json())
        with patch('backend.education.translation_provider.generate', side_effect=AssertionError('no generation')):
            response=self.client.get('/api/worker/education-bundles',headers={**self.worker,'Accept-Language':'en-US,en;q=0.9'})
        item=response.json()['items'][0]
        self.assertEqual((item['language'],item['requested_language'],item['translation_fallback']),('en','en-US',True))
        self.assertEqual(self.worker_items('en-US')[0]['language'],'en')
        response=self.client.get('/api/worker/education-bundles?language=ko',headers={**self.worker,'Accept-Language':'en'})
        self.assertEqual(response.json()['items'][0]['language'],'ko')
