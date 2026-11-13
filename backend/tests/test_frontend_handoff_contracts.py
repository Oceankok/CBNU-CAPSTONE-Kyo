"""Backend evidence for W12/W14; does not implement the frontend fixes."""
import unittest
from unittest.mock import patch
from backend.tests.support import isolated_api, login
from backend.db.event_repository import get_connection


class FrontendHandoffContracts(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)

    def test_summary_count_is_not_limited_to_recent_window(self):
        with get_connection() as conn:
            conn.execute('DELETE FROM candidate_event')
            conn.executemany('''INSERT INTO candidate_event
                (event_id,camera_id,tracking_id,ppe_type,timestamp_start,timestamp_end,event_status)
                VALUES(?,?,?,?,?,?,?)''', [
                (f'EVT_WINDOW_{i:03}', 'CAM_001', str(i), 'helmet',
                 '2026-11-10T03:00:00+00:00', '2026-11-10T03:00:00+00:00', 'pending')
                for i in range(65)])
        response = self.client.get('/api/events/summary?recent_limit=50', headers=self.admin)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['by_status']['pending'], 65)
        self.assertEqual(len(payload['recent_pending']), 50)
        # The contract has a current count and bounded samples, not a since-cursor
        # count of arrivals. The client must not equate the sample with arrivals.
        with get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='confirmed' WHERE event_id='EVT_WINDOW_064'")
        next_payload = self.client.get('/api/events/summary?recent_limit=50', headers=self.admin).json()
        self.assertEqual(next_payload['by_status']['pending'], 64)
        self.assertEqual(len(next_payload['recent_pending']), 50)
        old_ids = {x['event_id'] for x in payload['recent_pending']}
        reappearing = {x['event_id'] for x in next_payload['recent_pending']} - old_ids
        self.assertEqual(len(reappearing), 1)  # Existing candidate, not a new arrival.

    def test_missing_recommendations_get_does_not_generate(self):
        with patch('backend.api.main.get_education_recommendations', return_value={'items': []}), \
             patch('backend.api.main.generate_education_recommendations') as generate:
            response = self.client.get('/api/recommendations?quarter=2026-Q2', headers=self.admin)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['detail'], 'Education recommendations not found')
        generate.assert_not_called()

    def test_unauthorized_recommendation_get_cannot_read_or_generate(self):
        with patch('backend.api.main.get_education_recommendations') as read, \
             patch('backend.api.main.generate_education_recommendations') as generate:
            response = self.client.get('/api/recommendations?quarter=2026-Q2')
        self.assertEqual(response.status_code, 401)
        read.assert_not_called()
        generate.assert_not_called()
