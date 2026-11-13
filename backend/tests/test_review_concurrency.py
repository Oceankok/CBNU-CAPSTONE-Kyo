"""Concurrent re-review must not overwrite a completed final decision."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from backend.api import main
from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login, ready_media


class ReviewConcurrencyTests(unittest.TestCase):
    def test_only_one_concurrent_final_rereview_is_accepted(self):
        with isolated_api() as client, ready_media():
            headers = login(client)
            initial = client.post('/api/events/EVT_0001/review', headers=headers,
                                  json={'review_result': 'hold', 'review_reason_code': 'unclear'})
            self.assertEqual(initial.status_code, 200, initial.text)
            barrier = Barrier(2)
            original = main.update_event_review

            def raced(review):
                barrier.wait(timeout=10)
                return original(review)

            def send(result):
                try:
                    main.update_existing_event_review('EVT_0001', main.ReviewRequest(
                        review_result=result, review_reason_code='race', review_comment=result),
                        {'user_id': 'admin02'})
                    return 200, result
                except HTTPException as exc:
                    return exc.status_code, result

            with patch.object(main, 'update_event_review', side_effect=raced):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(send, result) for result in ('confirmed', 'false_positive')]
                    results = [task.result(timeout=15) for task in futures]
            self.assertEqual(sorted(status for status, _ in results), [200, 409])
            winner = next(result for status, result in results if status == 200)
            with get_connection() as conn:
                final = conn.execute("SELECT review_result FROM event_review WHERE event_id='EVT_0001'").fetchone()[0]
                history = conn.execute("SELECT review_result FROM event_review_history WHERE event_id='EVT_0001'").fetchall()
            self.assertEqual(final, winner)
            self.assertEqual([row[0] for row in history], ['hold'])
