import unittest

from backend.db.event_repository import get_connection
from backend.db.migrations import apply_migrations, LATEST_VERSION
from backend.tests.support import isolated_api, login


class UserZoneMembershipTests(unittest.TestCase):
    def setUp(self):
        self.context = isolated_api()
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.admin = login(self.client)
        for zone in ('A', 'B'):
            response = self.client.put('/api/zones/' + zone, headers=self.admin,
                                       json={'required_ppe': ['helmet'], 'rules': []})
            self.assertEqual(response.status_code, 200)

    def members(self, zone, users):
        return self.client.put('/api/zones/' + zone + '/members', headers=self.admin, json={'user_ids': users})

    def worker(self):
        return next(row for row in self.client.get('/api/users', headers=self.admin).json()['items'] if row['user_id'] == 'worker01')

    def test_zone_changes_preserve_other_memberships_and_legacy_projection(self):
        self.assertEqual(self.members('B', ['worker01']).status_code, 200)
        self.assertEqual(self.members('A', ['worker01']).status_code, 200)
        self.assertEqual(self.worker()['zone_names'], ['A', 'B'])
        self.assertEqual(self.worker()['zone_name'], 'A')
        worker_headers = login(self.client, 'worker01')
        body = self.client.get('/api/worker/zones', headers=worker_headers).json()
        self.assertEqual([row['zone_name'] for row in body['items']], ['A', 'B'])
        self.assertEqual(self.client.get('/api/worker/zone', headers=worker_headers).json()['zone_name'], 'A')
        self.assertEqual(self.members('A', []).status_code, 200)
        self.assertEqual(self.worker()['zone_names'], ['B'])
        self.assertEqual(self.worker()['zone_name'], 'B')
        self.assertEqual(self.client.delete('/api/zones/B', headers=self.admin).status_code, 409)
        self.assertEqual(self.members('B', []).status_code, 200)
        self.assertIsNone(self.worker()['zone_name'])
        self.assertEqual(self.client.delete('/api/zones/B', headers=self.admin).status_code, 200)

    def test_invalid_replacement_is_atomic_and_admin_only(self):
        self.members('A', ['worker01'])
        for ids in (['admin02'], ['worker01', 'missing'], ['worker01', 'worker01']):
            self.assertEqual(self.members('A', ids).status_code, 422)
            self.assertEqual(self.worker()['zone_names'], ['A'])
        self.assertEqual(self.client.put('/api/zones/A/members', json={'user_ids': []}).status_code, 401)
        self.assertEqual(self.client.put('/api/zones/A/members', headers=login(self.client, 'worker01'), json={'user_ids': []}).status_code, 403)

    def test_user_create_patch_arrays_and_legacy_replacement(self):
        body = {'user_id': 'multi', 'display_name': '다중 구역', 'password': 'test-password', 'role': 'worker', 'zone_names': ['B', 'A']}
        result = self.client.post('/api/users', headers=self.admin, json=body)
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.json()['zone_names'], ['A', 'B'])
        result = self.client.patch('/api/users/multi', headers=self.admin, json={'zone_name': 'B'})
        self.assertEqual(result.json()['zone_names'], ['B'])
        result = self.client.patch('/api/users/multi', headers=self.admin, json={'zone_names': ['A', 'missing'], 'display_name': 'changed'})
        self.assertEqual(result.status_code, 422)
        rows = self.client.get('/api/users', headers=self.admin).json()['items']
        saved = next(row for row in rows if row['user_id'] == 'multi')
        self.assertEqual(saved['display_name'], '다중 구역')
        self.assertEqual(saved['zone_names'], ['B'])
        self.assertEqual(self.client.patch('/api/users/admin02', headers=self.admin, json={'zone_names': ['A']}).status_code, 422)
        self.assertEqual(self.client.patch('/api/users/multi', headers=self.admin, json={'zone_names': None}).status_code, 422)

    def test_v6_migration_preserves_valid_assignment_and_is_idempotent(self):
        if __import__("os").environ.get("PPE_TEST_POSTGRES_URL"):
            self.skipTest("SQLite historical migration; PostgreSQL baseline tested separately")
        with get_connection() as conn:
            conn.execute('DROP TABLE user_zone')
            conn.execute("UPDATE app_user SET zone_name='A' WHERE user_id='worker01'")
            conn.execute('PRAGMA user_version=6')
            conn.commit()
            apply_migrations(conn)
            apply_migrations(conn)
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], LATEST_VERSION)
            self.assertEqual([tuple(row) for row in conn.execute('SELECT user_id,zone_name FROM user_zone')], [('worker01', 'A')])

    def test_education_groups_use_each_assigned_zones_latest_quarter(self):
        self.members('A', ['worker01'])
        self.members('B', ['worker01'])
        with get_connection() as conn:
            conn.execute('DELETE FROM education_recommendation')
            for quarter in ('2026-Q1', '2026-Q2'):
                conn.execute("INSERT INTO quarterly_summary(quarter,created_at) VALUES(?,CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING", (quarter,))
            for ident, zone, quarter in [('a', 'A', '2026-Q1'), ('b', 'B', '2026-Q2'), ('other', '프레스 구역', '2026-Q2')]:
                conn.execute("""INSERT INTO education_recommendation(recommendation_id,quarter,recommendation_rank,ppe_type,zone_name,education_topic,priority_score,confirmed_count,generated_at)
                    VALUES(?,?,1,'helmet',?,'교육',99,42,CURRENT_TIMESTAMP)""", (ident, quarter, zone))
        response = self.client.get('/api/worker/education?zone_name=프레스 구역', headers=login(self.client, 'worker01'))
        body = response.json()
        self.assertEqual([(row['zone_name'], row['quarter']) for row in body['groups']], [('A', '2026-Q1'), ('B', '2026-Q2')])
        self.assertEqual([item['recommendation_id'] for row in body['groups'] for item in row['items']], ['a', 'b'])
        for row in body['groups']:
            self.assertNotIn('priority_score', row['items'][0])
            self.assertNotIn('confirmed_count', row['items'][0])
