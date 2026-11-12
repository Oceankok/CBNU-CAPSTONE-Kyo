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

    def test_latest_supported_date_does_not_overflow(self):
        for endpoint in ('/api/events', '/api/events/summary'):
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint + '?date_to=9999-12-31', headers=self.admin)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['total'], 3)

    def test_list_and_summary_sort_mixed_time_formats_consistently(self):
        stamps = {
            'PAGE_A': '2026-11-10T00:30:00.000000+00:00',  # Korean 09:30
            'PAGE_B': '2026-11-10 09:00:00',  # Unknown legacy timezone
            'PAGE_C': '2026-11-09T23:45:00.000000+00:00',  # Korean 08:45
        }
        with get_connection() as conn:
            for key, stamp in stamps.items():
                conn.execute("UPDATE candidate_event SET timestamp_start=?,event_status='pending' WHERE event_id=?",
                             (stamp, self.event_ids[key]))
        expected = [self.event_ids[key] for key in ('PAGE_A', 'PAGE_B', 'PAGE_C')]
        listed = self.client.get('/api/events?limit=3', headers=self.admin).json()['items']
        recent = self.client.get('/api/events/summary', headers=self.admin).json()['recent_pending']
        self.assertEqual([item['event_id'] for item in listed], expected)
        self.assertEqual([item['event_id'] for item in recent], expected)
        with get_connection() as conn:
            stored = conn.execute('SELECT timestamp_start FROM candidate_event WHERE event_id=?',
                                  (self.event_ids['PAGE_B'],)).fetchone()[0]
        self.assertEqual(stored, stamps['PAGE_B'])

    def test_sort_keeps_subseconds_and_event_id_tie_break(self):
        with get_connection() as conn:
            for key, stamp in (
                ('PAGE_A', '2026-11-10T14:59:59.999999+00:00'),
                ('PAGE_B', '2026-11-10 23:59:59.999999'),
                ('PAGE_C', '2026-11-10T14:59:59.999998+00:00'),
            ):
                conn.execute("UPDATE candidate_event SET timestamp_start=?,event_status='pending' WHERE event_id=?",
                             (stamp, self.event_ids[key]))
        expected = sorted([self.event_ids['PAGE_A'], self.event_ids['PAGE_B']], reverse=True)
        expected.append(self.event_ids['PAGE_C'])
        for endpoint, field in (('/api/events', 'items'), ('/api/events/summary', 'recent_pending')):
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint, headers=self.admin)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([item['event_id'] for item in response.json()[field]], expected)

    def test_pagination_boundaries_and_access_control(self):
        response = self.client.get('/api/events?limit=2&offset=3', headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total'], 3)
        self.assertEqual(response.json()['items'], [])
        self.assertFalse(response.json()['has_more'])
        for query in ('limit=0', 'limit=101', 'offset=-1', 'min_confidence=1.1'):
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/api/events?' + query, headers=self.admin).status_code, 422)
        worker = login(self.client, 'worker01')
        for endpoint in ('/api/events', '/api/events/summary'):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(endpoint).status_code, 401)
                self.assertEqual(self.client.get(endpoint, headers=worker).status_code, 403)

    def test_v18_upgrade_adds_indexes_and_preserves_events(self):
        from backend.db.connection import schema_version
        from backend.db.migrations import LATEST_VERSION, apply_migrations
        from backend.db.postgres import initialize
        indexes = ('idx_candidate_event_status_time', 'idx_candidate_event_ppe_time', 'idx_camera_info_zone')
        with get_connection() as conn:
            for name in indexes:
                conn.execute(f'DROP INDEX IF EXISTS {name}')
            postgres = getattr(conn, 'dialect', 'sqlite') == 'postgresql'
            if postgres:
                conn.execute('UPDATE app_schema_version SET version=18 WHERE singleton=1')
            else:
                conn.execute('PRAGMA user_version=18')
        if postgres:
            initialize()
        else:
            with get_connection() as conn:
                apply_migrations(conn)
        with get_connection() as conn:
            self.assertEqual(schema_version(conn), LATEST_VERSION)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM candidate_event').fetchone()[0], 3)
            query = ("SELECT indexname FROM pg_indexes WHERE schemaname=current_schema()"
                     if postgres else "SELECT name FROM sqlite_master WHERE type='index'")
            found = {row[0] for row in conn.execute(query).fetchall()}
        self.assertTrue(set(indexes).issubset(found))


if __name__ == "__main__":
    unittest.main()


class MultipleZoneFilterTests(unittest.TestCase):
    setUp = EventPaginationApiTests.setUp
    def test_multiple_zones_match_list_count_and_summary(self):
        with get_connection() as conn:
            original = dict(conn.execute("SELECT * FROM camera_info WHERE camera_id=?", ("CAM_001",)).fetchone())
            conn.execute("UPDATE camera_info SET zone_name=? WHERE camera_id=?", ("Zone A", "CAM_001"))
            for key, zone in (("PAGE_B", "Zone B"), ("PAGE_C", "Zone C")):
                camera = {**original, "camera_id": key, "zone_name": zone}
                columns = list(camera)
                conn.execute("INSERT INTO camera_info (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")", list(camera.values()))
                conn.execute("UPDATE candidate_event SET camera_id=? WHERE event_id=?", (key, self.event_ids[key]))
        query = [("zone", "Zone A"), ("zone", "Zone B"), ("zone", "Zone A"), ("limit", "1")]
        first = self.client.get("/api/events", params=query, headers=self.admin)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["total"], 2)
        self.assertTrue(first.json()["has_more"])
        second = self.client.get("/api/events", params=[*query, ("offset", "1")], headers=self.admin)
        ids = {first.json()["items"][0]["event_id"], second.json()["items"][0]["event_id"]}
        self.assertEqual(ids, {self.event_ids["PAGE_A"], self.event_ids["PAGE_B"]})
        summary = self.client.get("/api/events/summary", params=query, headers=self.admin)
        self.assertEqual(summary.json()["total"], 2)
        self.assertEqual(summary.json()["by_zone"], {"Zone A": 1, "Zone B": 1})
        single = self.client.get("/api/events?zone=Zone%20B", headers=self.admin)
        self.assertEqual(single.json()["total"], 1)
        self.assertEqual(self.client.get("/api/events?zone=unknown", headers=self.admin).json()["total"], 0)

    def test_invalid_zone_values_are_rejected(self):
        for endpoint in ("/api/events", "/api/events/summary"):
            for values in ([""], ["x" * 101], ["x"] * 51):
                response = self.client.get(endpoint, params=[("zone", x) for x in values], headers=self.admin)
                self.assertEqual(response.status_code, 422, response.text)
