"""Approved remaining-event reconciliation and reversible final-state counting."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from fastapi import HTTPException
from backend.api import main
from backend.db import event_repository as repository
from backend.db.connection import is_postgres, schema_version
from backend.db.init_db import init_database
from backend.db.migrations import LATEST_VERSION
from backend.tests.support import isolated_api, login, ready_media


class FalsePositiveReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        self.enterContext(ready_media())

    def verdict(self, result, *, create=False):
        method = self.client.post if create else self.client.put
        response = method('/api/events/EVT_0001/review', headers=self.admin,
                          json={'review_result': result, 'review_reason_code': 'test',
                                'second_review_needed': True})
        self.assertEqual(response.status_code, 200, response.text)

    def count(self):
        with repository.get_connection() as conn:
            return conn.execute('SELECT COALESCE(SUM(false_positive_count),0) FROM false_positive_aggregate').fetchone()[0]

    def test_false_positive_corrections_follow_current_verdict_without_double_counting(self):
        self.verdict('false_positive', create=True)
        for result, expected in [('false_positive',1), ('confirmed',0), ('false_positive',1),
                                 ('hold',0), ('false_positive',1), ('false_positive',1)]:
            self.verdict(result)
            self.assertEqual(self.count(), expected)
            self.assertEqual(repository.generate_quarterly_stats('2026-Q2')['summary']['false_positive_count'], expected)
        with repository.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM event_review_history WHERE event_id='EVT_0001'").fetchone()[0], 6)

    def test_camera_move_does_not_move_false_positive_contribution(self):
        self.verdict('false_positive', create=True)
        with repository.get_connection() as conn:
            conn.execute("UPDATE camera_info SET zone_name='자재 이동 구역' WHERE camera_id='CAM_001'")
        self.verdict('confirmed')
        self.assertEqual(self.count(), 0)
        self.verdict('false_positive')
        with repository.get_connection() as conn:
            self.assertEqual(conn.execute('SELECT zone_name FROM false_positive_aggregate').fetchone()[0], '프레스 구역')
        self.assertEqual(self.count(), 1)

    def test_two_events_in_same_group_reviewed_concurrently_count_once_each(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        new = repository.get_candidate_event_by_id('EVT_0001')
        new.update(event_id='EVT_SECOND', thumbnail_path='', video_clip_path='')
        repository.insert_candidate_event(new)
        barrier = Barrier(2)
        original = main.insert_event_review

        def raced(review):
            barrier.wait(timeout=5)
            return original(review)

        with ready_media('EVT_SECOND'), TemporaryDirectory() as directory:
            media = Path(directory)/'synthetic.jpg'
            media.write_bytes(b'synthetic-redacted-fixture')
            with patch('backend.media.service.owned_path', return_value=media), \
                 patch.object(main, 'insert_event_review', side_effect=raced):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    tasks = [pool.submit(main.create_event_review, ident, main.ReviewRequest(
                        review_result='false_positive', review_reason_code='test'), {'user_id': 'admin02'})
                        for ident in ('EVT_0001', 'EVT_SECOND')]
                    for task in tasks:
                        self.assertEqual(task.result(timeout=10)['status'], 'ok')
        self.assertEqual(self.count(), 2)
        with repository.get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM false_positive_aggregate').fetchone()[0], 1)

    def test_explicit_event_deletion_removes_its_contribution(self):
        self.verdict('false_positive', create=True)
        repository.delete_candidate_event('EVT_0001')
        self.assertEqual(self.count(), 0)

    def test_review_and_deletion_race_leave_no_orphan_count(self):
        self.verdict('hold', create=True)
        barrier = Barrier(2)
        original = main.update_event_review

        def raced(review):
            barrier.wait(timeout=5)
            return original(review)

        def review():
            try:
                main.update_existing_event_review('EVT_0001', main.ReviewRequest(
                    review_result='false_positive', review_reason_code='test'), {'user_id': 'admin02'})
                return 200
            except HTTPException as exc:
                return exc.status_code

        def delete():
            barrier.wait(timeout=5)
            repository.delete_candidate_event('EVT_0001')

        with patch.object(main, 'update_event_review', side_effect=raced):
            with ThreadPoolExecutor(max_workers=2) as pool:
                reviewing, deleting = pool.submit(review), pool.submit(delete)
                self.assertIn(reviewing.result(timeout=10), {200, 409})
                deleting.result(timeout=10)
        self.assertEqual(self.count(), 0)
        with repository.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM event_zone_snapshot WHERE event_id='EVT_0001'").fetchone()[0], 0)

    def test_rebuild_uses_korean_quarter_and_rolls_back_failure(self):
        from backend.db.false_positives import rebuild_false_positive_aggregates
        self.verdict('false_positive', create=True)
        with repository.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET timestamp_start='2030-03-31T16:00:00+00:00' WHERE event_id='EVT_0001'")
            rebuild_false_positive_aggregates(conn)
            row = conn.execute('SELECT quarter,false_positive_count FROM false_positive_aggregate').fetchone()
            self.assertEqual((row['quarter'], row['false_positive_count']), ('2030-Q2', 1))
            before = [dict(row) for row in conn.execute('SELECT * FROM false_positive_aggregate')]
        with patch('backend.db.false_positives._insert_group', side_effect=RuntimeError('synthetic insert failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic insert failure'):
                with repository.get_connection() as conn:
                    rebuild_false_positive_aggregates(conn)
        with repository.get_connection() as conn:
            self.assertEqual([dict(row) for row in conn.execute('SELECT * FROM false_positive_aggregate')], before)

    def test_v21_upgrade_rebuilds_test_cache_and_is_repeatable(self):
        self.verdict('false_positive', create=True)
        repository.generate_quarterly_stats('2026-Q2')
        with repository.get_connection() as conn:
            conn.execute('''INSERT INTO education_material(material_id,recommendation_id,quarter,zone_name,ppe_type,
                education_topic,title,body,checklist_json,status,created_by,created_at,updated_by,updated_at,
                published_by,published_at,version)
                VALUES('MAT_MIGRATION','historical-rec','2026-Q2','프레스 구역','helmet','기존 주제',
                '보존 제목','보존 본문','["확인"]','published','admin02','old','admin02','old','admin02','old',3)''')
            before_materials = [dict(row) for row in conn.execute('SELECT * FROM education_material ORDER BY material_id')]
            before_events = [dict(row) for row in conn.execute('SELECT * FROM candidate_event ORDER BY event_id')]
            before_reviews = [dict(row) for row in conn.execute('SELECT * FROM event_review ORDER BY event_id')]
            conn.execute('UPDATE false_positive_aggregate SET false_positive_count=999')
            conn.execute("INSERT INTO false_positive_aggregate(aggregate_id,quarter,zone_name,ppe_type,false_positive_count,updated_at) VALUES('deleted-test-event','2020-Q1','old','helmet',50,'old')")
            conn.execute('UPDATE app_schema_version SET version=21 WHERE singleton=1' if is_postgres() else 'PRAGMA user_version=21')
        init_database()
        self.assertEqual(self.count(), 1)
        self.assertEqual(repository.get_quarterly_stats('2026-Q2')['summary']['false_positive_count'], 1)
        with repository.get_connection() as conn:
            self.assertEqual(schema_version(conn), LATEST_VERSION)
            self.assertEqual([dict(row) for row in conn.execute('SELECT * FROM candidate_event ORDER BY event_id')], before_events)
            self.assertEqual([dict(row) for row in conn.execute('SELECT * FROM event_review ORDER BY event_id')], before_reviews)
            self.assertEqual([dict(row) for row in conn.execute('SELECT * FROM education_material ORDER BY material_id')], before_materials)
            first = [dict(row) for row in conn.execute('SELECT * FROM false_positive_aggregate')]
        init_database()
        with repository.get_connection() as conn:
            self.assertEqual([dict(row) for row in conn.execute('SELECT * FROM false_positive_aggregate')], first)
