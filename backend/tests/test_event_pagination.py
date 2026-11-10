import unittest
from uuid import uuid4

from backend.db.event_repository import get_connection, insert_candidate_event
from backend.tests.support import isolated_api, login


class EventPaginationApiTests(unittest.TestCase):
    def setUp(self):
        context = isolated_api()
        self.client = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.admin = login(self.client)
        camera_id = "CAM_001"
        with get_connection() as conn:
            conn.execute("DELETE FROM candidate_event")
        self.event_ids = {}
        for ident, stamp, ppe, status, confidence in (
            ("PAGE_A", "2026-11-10T01:00:00+00:00", "helmet", "pending", 0.91),
            ("PAGE_B", "2026-11-09T15:00:00+00:00", "vest", "confirmed", 0.84),
            ("PAGE_C", "2026-11-08T00:00:00+00:00", "helmet", "hold", 0.62),
        ):
            event_id = f"EVT_{ident}_{uuid4().hex[:6]}"
            self.event_ids[ident] = event_id
            insert_candidate_event({
                "event_id": event_id,
                "camera_id": camera_id,
                "tracking_id": ident,
                "ppe_type": ppe,
                "timestamp_start": stamp,
                "timestamp_end": stamp,
                "duration_sec": 1,
                "frame_sample_count": 1,
                "thumbnail_path": "",
                "video_clip_path": "",
                "ai_confidence": confidence,
                "person_detected": 1,
                "ppe_detected": 0,
                "model_version": "test",
                "event_status": status,
            })

    def test_server_side_pagination_and_filters(self):
        response = self.client.get("/api/events?limit=1&offset=1", headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["offset"], 1)
        self.assertEqual(payload["limit"], 1)
        self.assertTrue(payload["has_more"])
        self.assertEqual(len(payload["items"]), 1)

        filtered = self.client.get(
            "/api/events?ppe_type=vest&min_confidence=0.8&status=confirmed",
            headers=self.admin,
        )
        self.assertEqual(filtered.status_code, 200, filtered.text)
        self.assertEqual(filtered.json()["total"], 1)
        self.assertEqual(filtered.json()["items"][0]["ppe_type"], "vest")

    def test_pending_events_are_first_and_review_only_survives_confidence_filter(self):
        review_only_id = f"EVT_REVIEW_ONLY_{uuid4().hex[:6]}"
        insert_candidate_event({
            "event_id": review_only_id,
            "camera_id": "CAM_001",
            "tracking_id": "review-only",
            "ppe_type": "vest",
            "timestamp_start": "2026-11-10T02:00:00+00:00",
            "timestamp_end": "2026-11-10T02:00:00+00:00",
            "duration_sec": 1,
            "frame_sample_count": 1,
            "thumbnail_path": "",
            "video_clip_path": "",
            "ai_confidence": 0.1,
            "person_detected": 1,
            "ppe_detected": 0,
            "model_version": "test",
            "event_status": "hold",
        })
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO processing_review_only_event(event_id,review_only) VALUES(?,1)",
                (review_only_id,),
            )
        response = self.client.get("/api/events?limit=4&min_confidence=0.8", headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["items"][0]["event_status"], "pending")
        self.assertIn(review_only_id, {item["event_id"] for item in payload["items"]})
        review_item = next(item for item in payload["items"] if item["event_id"] == review_only_id)
        self.assertTrue(review_item["review_only"])
        self.assertIsNone(review_item["ai_confidence"])

    def test_date_filters_use_korea_calendar_days(self):
        response = self.client.get(
            "/api/events?date_from=2026-11-10&date_to=2026-11-10",
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 2)

    def test_summary_matches_filtered_event_set(self):
        response = self.client.get(
            "/api/events/summary?date_from=2026-11-10&date_to=2026-11-10",
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["by_status"], {"pending": 1, "confirmed": 1})
        self.assertEqual(payload["by_ppe_type"], {"helmet": 1, "vest": 1})
        self.assertEqual(len(payload["recent_pending"]), 1)
        self.assertEqual(payload["recent_pending"][0]["event_status"], "pending")

    def test_invalid_date_range_is_rejected(self):
        response = self.client.get(
            "/api/events?date_from=2026-11-11&date_to=2026-11-10",
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
