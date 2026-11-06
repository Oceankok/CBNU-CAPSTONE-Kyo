
"""Regression tests for review-only PPE candidate submission."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from backend.db.event_repository import get_connection
from backend.processing.events import EventSubmission, submit
from backend.tests.support import isolated_api, login


class ReviewOnlySubmissionTests(unittest.TestCase):

    def setUp(self):
        context = isolated_api()
        self.client = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT camera_id
                FROM candidate_event
                WHERE event_id = 'EVT_0001'
                """
            ).fetchone()

        self.assertIsNotNone(row, "Seed camera not found")
        self.camera_id = row["camera_id"]

        now = datetime.now(timezone.utc)
        self.event_id = "EVT_" + uuid4().hex

        self.payload = {
            "event_id": self.event_id,
            "camera_id": self.camera_id,
            "tracking_id": "TRK_REVIEW_TEST",
            "ppe_type": "helmet",
            "timestamp_start": now - timedelta(seconds=3),
            "timestamp_end": now,
            "confidence": None,
            "frame_sample_count": 30,
            "model_version": "test-3class",
            "review_only": True,
        }

    def test_review_only_saves_without_broadcast(self):
        body = EventSubmission(**self.payload)

        with patch(
            "backend.processing.events.enqueue_event_broadcast"
        ) as broadcast:
            result = submit(body)
            broadcast.assert_not_called()

        self.assertTrue(result["created"])
        self.assertTrue(result["review_only"])
        self.assertFalse(result["broadcast"]["queued"])

        with get_connection() as conn:
            event = conn.execute(
                "SELECT * FROM candidate_event WHERE event_id=?",
                (self.event_id,),
            ).fetchone()

            mode = conn.execute(
                """
                SELECT review_only
                FROM processing_review_only_event
                WHERE event_id=?
                """,
                (self.event_id,),
            ).fetchone()

        self.assertIsNotNone(event)
        self.assertEqual(event["event_status"], "pending")
        self.assertEqual(event["ai_confidence"], 0.0)
        self.assertEqual(mode["review_only"], 1)

    def query_event(self, event_id):
        admin = login(self.client)
        detail = self.client.get('/api/events/' + event_id, headers=admin)
        self.assertEqual(detail.status_code, 200, detail.text)
        listing = self.client.get('/api/events', headers=admin)
        self.assertEqual(listing.status_code, 200, listing.text)
        event = detail.json()['event']
        listed = next(e for e in listing.json()['items'] if e['event_id'] == event_id)
        self.assertEqual(listed, event)
        return event

    def test_queries_expose_review_mode_without_zero_confidence(self):
        submit(EventSubmission(**self.payload))
        event = self.query_event(self.event_id)
        self.assertIs(event['review_only'], True)
        self.assertIsNone(event['ai_confidence'])
        # Public serialization must not rewrite storage or break identical retries.
        self.assertFalse(submit(EventSubmission(**self.payload))['created'])
        with get_connection() as conn:
            row = conn.execute('SELECT ai_confidence FROM candidate_event WHERE event_id=?', (self.event_id,)).fetchone()
        self.assertEqual(row[0], 0.0)

    def test_queries_preserve_normal_and_legacy_confidence(self):
        for confidence in (0.0, 0.88):
            ident = 'EVT_' + uuid4().hex
            with patch('backend.processing.events.enqueue_event_broadcast'):
                submit(EventSubmission(**{**self.payload, 'event_id': ident,
                    'review_only': False, 'confidence': confidence}))
            event = self.query_event(ident)
            self.assertIs(event['review_only'], False)
            self.assertEqual(event['ai_confidence'], confidence)
        with get_connection() as conn:
            legacy = conn.execute("SELECT ai_confidence FROM candidate_event WHERE event_id='EVT_0001'").fetchone()[0]
        event = self.query_event('EVT_0001')
        self.assertIs(event['review_only'], False)
        self.assertEqual(event['ai_confidence'], legacy)

    def test_mode_metadata_does_not_weaken_admin_query_permissions(self):
        submit(EventSubmission(**self.payload))
        for path in ('/api/events', '/api/events/' + self.event_id):
            self.assertEqual(self.client.get(path).status_code, 401)
        worker = login(self.client, 'worker01')
        for path in ('/api/events', '/api/events/' + self.event_id):
            self.assertEqual(self.client.get(path, headers=worker).status_code, 403)

    def test_retry_never_broadcasts(self):
        body = EventSubmission(**self.payload)

        with patch(
            "backend.processing.events.enqueue_event_broadcast"
        ) as broadcast:
            first = submit(body)
            second = submit(body)
            broadcast.assert_not_called()

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertFalse(second["broadcast"]["queued"])

    def test_mode_change_is_rejected(self):
        submit(EventSubmission(**self.payload))

        changed = {
            **self.payload,
            "review_only": False,
            "confidence": 0.85,
        }

        with patch(
            "backend.processing.events.enqueue_event_broadcast"
        ) as broadcast:
            with self.assertRaises(HTTPException) as error:
                submit(EventSubmission(**changed))

            broadcast.assert_not_called()

        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(
            error.exception.detail,
            "event_submission_mode_conflict",
        )


if __name__ == "__main__":
    unittest.main()
