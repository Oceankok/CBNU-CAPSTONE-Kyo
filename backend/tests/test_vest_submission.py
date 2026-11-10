import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.db.event_repository import get_connection
from backend.processing.events import EventSubmission, submit
from backend.tests.support import isolated_api


class VestSubmissionContractTests(unittest.TestCase):
    def test_review_only_vest_candidate_is_stored_with_ppe_type(self):
        context = isolated_api()
        client = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        del client  # isolated_api also initializes the dedicated test database.

        with get_connection() as conn:
            camera_id = conn.execute(
                "SELECT camera_id FROM camera_info WHERE status='active' ORDER BY camera_id LIMIT 1"
            ).fetchone()[0]

        end = datetime.now(timezone.utc)
        event_id = "EVT_" + uuid4().hex
        body = EventSubmission(
            event_id=event_id,
            camera_id=camera_id,
            tracking_id="vest-contract",
            ppe_type="vest",
            timestamp_start=end - timedelta(seconds=3),
            timestamp_end=end,
            confidence=None,
            frame_sample_count=30,
            model_version="test-3class",
            review_only=True,
        )
        result = submit(body)
        self.assertTrue(result["created"])
        with get_connection() as conn:
            row = conn.execute(
                "SELECT ppe_type,event_status FROM candidate_event WHERE event_id=?",
                (event_id,),
            ).fetchone()
            mode = conn.execute(
                "SELECT review_only FROM processing_review_only_event WHERE event_id=?",
                (event_id,),
            ).fetchone()
        self.assertEqual(row["ppe_type"], "vest")
        self.assertEqual(row["event_status"], "pending")
        self.assertEqual(mode["review_only"], 1)


if __name__ == "__main__":
    unittest.main()
