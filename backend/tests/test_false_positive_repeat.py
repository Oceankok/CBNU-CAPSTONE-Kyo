"""Repeated identical classifications must not increment the event count."""
import unittest
from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login, ready_media


class FalsePositiveRepeatTests(unittest.TestCase):
    def test_repeated_false_positive_keeps_count_and_records_review_history(self):
        with isolated_api() as client, ready_media():
            headers = login(client)
            body = {'review_result': 'false_positive', 'review_reason_code': 'test',
                    'second_review_needed': True}
            result = client.post('/api/events/EVT_0001/review', headers=headers, json=body)
            self.assertEqual(result.status_code, 200, result.text)
            with get_connection() as conn:
                before = [dict(row) for row in conn.execute('SELECT * FROM false_positive_aggregate ORDER BY aggregate_id')]
            for _ in range(2):
                result = client.put('/api/events/EVT_0001/review', headers=headers, json=body)
                self.assertEqual(result.status_code, 200, result.text)
            with get_connection() as conn:
                after = [dict(row) for row in conn.execute('SELECT * FROM false_positive_aggregate ORDER BY aggregate_id')]
                history = conn.execute("SELECT COUNT(*) FROM event_review_history WHERE event_id='EVT_0001'").fetchone()[0]
            self.assertEqual(after, before)
            self.assertEqual(history, 2)
