import unittest

from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login


class WorkerEducationTests(unittest.TestCase):
    def setUp(self):
        self.context = isolated_api()
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        with get_connection() as conn:
            conn.execute("DELETE FROM education_recommendation")
            for quarter in ("2025-Q4", "2026-Q1", "2026-Q2"):
                conn.execute("INSERT OR IGNORE INTO quarterly_summary(quarter,created_at) VALUES(?,datetime('now'))", (quarter,))
            conn.execute("UPDATE app_user SET zone_name='프레스 구역' WHERE user_id='worker01'")
            for rec_id, quarter, zone, rank in [
                ("ED_OLD", "2025-Q4", "프레스 구역", 1),
                ("ED_FIRST", "2026-Q1", "프레스 구역", 1),
                ("ED_SECOND", "2026-Q1", "프레스 구역", 2),
                ("ED_OTHER_ZONE", "2026-Q2", "자재 이동 구역", 1),
            ]:
                conn.execute("""INSERT INTO education_recommendation(
                    recommendation_id,quarter,recommendation_rank,ppe_type,zone_name,
                    education_topic,priority_score,confirmed_count,generated_at)
                    VALUES(?,?,?,'helmet',?,'안전모 착용 교육',99,42,datetime('now'))""",
                    (rec_id,quarter,rank,zone))

    def test_authentication_and_role(self):
        self.assertEqual(self.client.get("/api/worker/education").status_code,401)
        self.assertEqual(self.client.get("/api/worker/education",headers=login(self.client)).status_code,403)
        headers = login(self.client,"worker01")
        self.assertEqual(self.client.get("/api/worker/education",headers=headers).status_code,200)
        self.assertEqual(self.client.get("/api/recommendations",headers=headers).status_code,403)

    def test_latest_zone_quarter_and_allowlisted_fields(self):
        result = self.client.get("/api/worker/education?zone_name=other-zone&quarter=2026-Q2",headers=login(self.client,"worker01"))
        self.assertEqual(result.status_code,200,result.text)
        body = result.json()
        self.assertEqual(body["quarter"],"2026-Q1")
        self.assertEqual(body["zone_name"],"프레스 구역")
        self.assertEqual([row["recommendation_id"] for row in body["items"]],["ED_FIRST","ED_SECOND"])
        self.assertIsNone(body["empty_reason"])
        for row in body["items"]:
            self.assertEqual(set(row),{"recommendation_id","ppe_type","zone_name","education_topic","material"})
            self.assertIsNone(row["material"])

    def test_unassigned_and_empty_zones_do_not_fall_back_to_other_zones(self):
        headers = login(self.client,"worker01")
        with get_connection() as conn:
            conn.execute("UPDATE app_user SET zone_name=NULL WHERE user_id='worker01'")
        body = self.client.get("/api/worker/education",headers=headers).json()
        self.assertEqual(body,{"quarter":None,"zone_name":None,"items":[],"empty_reason":"zone_unassigned"})
        with get_connection() as conn:
            conn.execute("UPDATE app_user SET zone_name='추천 없는 구역' WHERE user_id='worker01'")
        body = self.client.get("/api/worker/education",headers=headers).json()
        self.assertEqual(body["items"],[])
        self.assertIsNone(body["quarter"])
        self.assertEqual(body["empty_reason"],"recommendations_unavailable")
