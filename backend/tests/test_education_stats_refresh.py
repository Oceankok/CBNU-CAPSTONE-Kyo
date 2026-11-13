"""Stats cache refresh must not erase or temporarily hide published education."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from unittest.mock import patch

from backend.db import event_repository as repository
from backend.tests.support import isolated_api, login
from backend.zones.membership import replace_user_zones
from backend.zones.routes import get_my_education
from backend.zones import routes


class EducationStatsRefreshTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        self.worker = login(self.client, 'worker01')
        with repository.get_connection() as conn:
            conn.execute('DELETE FROM education_recommendation')
            conn.execute("UPDATE candidate_event SET event_status='pending'")
            conn.execute("UPDATE candidate_event SET event_status='confirmed' WHERE event_id='EVT_0001'")
            replace_user_zones(conn, 'worker01', ['프레스 구역'])
        repository.generate_quarterly_stats('2026-Q2')
        # Before the fix this explicit initial creation is needed. Refresh below
        # deliberately never visits the recommendation page or generation API.
        repository.generate_education_recommendations('2026-Q2')
        rec = repository.get_education_recommendations('2026-Q2')['items'][0]
        self.rec_id = rec['recommendation_id']
        draft = self.client.post('/api/recommendations/'+self.rec_id+'/materials',
                                 headers=self.admin, json={'title': '게시 교육', 'body': '유지할 본문',
                                                           'checklist': ['안전모 확인']})
        self.assertEqual(draft.status_code, 201, draft.text)
        self.material_id = draft.json()['material_id']
        published = self.client.post('/api/education/materials/'+self.material_id+'/publish',
                                     headers=self.admin, json={'expected_version': 0, 'approved': True})
        self.assertEqual(published.status_code, 200, published.text)
        self.published = published.json()
        self.before = self.visible()
        self.assertIn('유지할 본문', self.before['items'][0]['material'])

    def visible(self):
        response = self.client.get('/api/worker/education', headers=self.worker)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_refresh_keeps_published_content_without_recommendation_page_visit(self):
        for _ in range(2):
            response = self.client.post('/api/stats/generate?quarter=2026-Q2', headers=self.admin)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.visible(), self.before)
        stored = self.client.get('/api/education/materials/'+self.material_id, headers=self.admin)
        self.assertEqual(stored.json(), self.published)

    def test_uncommitted_replacement_never_exposes_empty_rankings(self):
        original = repository._replace_education_recommendations
        observed = []

        def rebuild(conn, quarter):
            conn.execute('DELETE FROM education_recommendation WHERE quarter=?', (quarter,))
            # Separate read connection during the delete/insert interval sees the
            # old committed publication, not an empty cache.
            observed.append(get_my_education({'role': 'worker', 'user_id': 'worker01'}))
            return original(conn, quarter)

        with patch.object(repository, '_replace_education_recommendations', side_effect=rebuild):
            repository.generate_quarterly_stats('2026-Q2')
        self.assertEqual(observed, [self.before])
        self.assertEqual(self.visible(), self.before)

    def test_replacement_failure_rolls_back_stats_rankings_and_publication(self):
        before_stats = repository.get_quarterly_stats('2026-Q2')
        before_recs = repository.get_education_recommendations('2026-Q2')
        original = repository._replace_education_recommendations

        def fail(conn, quarter):
            original(conn, quarter)
            raise RuntimeError('synthetic replacement failure')

        with patch.object(repository, '_replace_education_recommendations', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'synthetic replacement failure'):
                repository.generate_quarterly_stats('2026-Q2')
        self.assertEqual(repository.get_quarterly_stats('2026-Q2'), before_stats)
        self.assertEqual(repository.get_education_recommendations('2026-Q2'), before_recs)
        self.assertEqual(self.visible(), self.before)

    def test_worker_response_uses_one_snapshot_across_concurrent_committed_change(self):
        updated, committed = Event(), Event()
        tasks = []

        def change_topic():
            with repository.get_connection() as conn:
                conn.execute("UPDATE education_recommendation SET education_topic='동시 변경 주제'")
                updated.set()
            committed.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            @contextmanager
            def reader():
                with repository.get_connection() as conn:
                    class InterleavedReader:
                        def __getattr__(self, name):
                            return getattr(conn, name)

                        def execute(self, sql, parameters=()):
                            if 'SELECT recommendation_id,ppe_type' in sql and not tasks:
                                tasks.append(pool.submit(change_topic))
                                self_case.assertTrue(updated.wait(5))
                                if getattr(conn, 'dialect', 'sqlite') == 'postgresql':
                                    # MVCC permits the writer to commit during the read.
                                    self_case.assertTrue(committed.wait(5))
                                else:
                                    # SQLite's explicit read transaction holds the old
                                    # snapshot and prevents the writer's commit until exit.
                                    self_case.assertFalse(committed.wait(0.2))
                            return conn.execute(sql, parameters)
                    yield InterleavedReader()

            self_case = self
            with patch.object(routes, 'get_connection', reader):
                observed = get_my_education({'role': 'worker', 'user_id': 'worker01'})
            tasks[0].result(timeout=5)
        self.assertEqual(observed, self.before)
        self.assertTrue(committed.is_set())
        self.assertIsNone(self.visible()['items'][0]['material'])

    def test_actual_recommendation_removal_hides_but_preserves_publication(self):
        with repository.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='hold' WHERE event_id='EVT_0001'")
        repository.generate_quarterly_stats('2026-Q2')
        self.assertEqual(self.visible()['items'], [])
        stored = self.client.get('/api/education/materials/'+self.material_id, headers=self.admin)
        self.assertEqual(stored.json(), self.published)

    def test_actual_topic_change_still_hides_old_publication(self):
        with patch.object(repository, '_get_education_topic', return_value='변경된 안전 지침'):
            repository.generate_quarterly_stats('2026-Q2')
        self.assertIsNone(self.visible()['items'][0]['material'])
        stored = self.client.get('/api/education/materials/'+self.material_id, headers=self.admin)
        self.assertEqual(stored.json(), self.published)
