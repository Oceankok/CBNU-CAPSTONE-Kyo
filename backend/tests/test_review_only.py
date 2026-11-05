
"""Regression tests for review-only PPE candidate submission."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from backend.db.event_repository import get_connection
from backend.processing.events import EventSubmission, submit
from backend.tests.support import isolated_api


class ReviewOnlySubmissionTests(unittest.TestCase):

    def setUp(self):
        context = isolated_api()
        context.__enter__()
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
