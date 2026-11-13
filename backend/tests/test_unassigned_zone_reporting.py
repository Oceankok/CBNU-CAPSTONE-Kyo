"""A valid camera without a zone must not crash review or aggregate caches."""
import unittest

from backend.db import event_repository as repository
from backend.tests.support import isolated_api, login, ready_media


class UnassignedZoneReportingTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        created = self.client.post('/api/cameras', headers=self.admin,
                                   json={'camera_id': 'CAM_NO_ZONE'})
        self.assertEqual(created.status_code, 201, created.text)
        event = repository.get_candidate_event_by_id('EVT_0001')
        event.update(event_id='EVT_NO_ZONE', camera_id='CAM_NO_ZONE', event_status='pending',
                     timestamp_start='2030-04-01T01:00:00Z', timestamp_end='2030-04-01T01:00:03Z',
                     thumbnail_path='', video_clip_path='')
        repository.insert_candidate_event(event)
        self.enterContext(ready_media('EVT_NO_ZONE'))

    def review(self, result):
        response = self.client.post('/api/events/EVT_NO_ZONE/review', headers=self.admin,
                                    json={'review_result': result, 'review_reason_code': 'test'})
        self.assertEqual(response.status_code, 200, response.text)

    def test_false_positive_uses_existing_unassigned_summary_key(self):
        self.review('false_positive')
        with repository.get_connection() as conn:
            row = conn.execute("SELECT zone_name,false_positive_count FROM false_positive_aggregate WHERE quarter='2030-Q2'").fetchone()
        self.assertEqual(row['zone_name'], '')
        self.assertEqual(row['false_positive_count'], 1)
        self.assertEqual(repository.generate_quarterly_stats('2030-Q2')['summary']['false_positive_count'], 1)

    def test_confirmed_unassigned_event_has_stats_and_admin_recommendation(self):
        self.review('confirmed')
        stats = repository.generate_quarterly_stats('2030-Q2')
        self.assertEqual(stats['summary']['confirmed_count'], 1)
        self.assertEqual(stats['by_zone'][0]['zone_name'], '')
        recommendation = repository.generate_education_recommendations('2030-Q2')['items'][0]
        self.assertEqual(recommendation['zone_name'], '')
        self.assertIn('구역 미지정', recommendation['education_topic'])
        self.assertIsNone(repository.get_candidate_event_by_id('EVT_NO_ZONE')['zone_name'])
