import os
import sqlite3
import unittest
from unittest.mock import patch

from backend.db.connection import parameters_sql, Row, connect, begin_write, schema_version, week_expression
from backend.db.postgres import initialize
from backend.db.migrations import LATEST_VERSION
from backend.tests.support import isolated_api, login


class PostgresAdapterTests(unittest.TestCase):
    def test_placeholders_preserve_literals_and_casts(self):
        self.assertEqual(parameters_sql("SELECT :name, ?, '?' AS literal, 'it''s :name', value::timestamp"),
                         "SELECT %(name)s, %s, '?' AS literal, 'it''s :name', value::timestamp")

    def test_row_supports_named_and_positional_access(self):
        row = Row([('count', 4), ('name', 'A')])
        self.assertEqual(row[0], 4)
        self.assertEqual(row['name'], 'A')
        self.assertEqual(dict(row), {'count': 4, 'name': 'A'})


@unittest.skipUnless(os.environ.get('PPE_TEST_POSTGRES_URL'), 'Dedicated PostgreSQL test database required')
class PostgresIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.context = isolated_api()
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def test_baseline_initialization_is_repeatable_and_preserves_account(self):
        initialize()
        with connect() as conn:
            self.assertEqual(schema_version(conn), LATEST_VERSION)
        self.assertEqual(self.client.get('/api/auth/me', headers=login(self.client)).status_code, 200)

    def test_v7_processing_upgrade_preserves_account(self):
        with connect() as conn:
            # Recreate the pre-processing baseline, including removal of newer dependants.
            conn.execute('DROP TABLE host_ai_job')
            conn.execute('DROP TABLE host_ai_worker')
            conn.execute('DROP TABLE processing_inference_load')
            conn.execute('DROP TABLE processing_job')
            conn.execute('DROP TABLE processing_worker')
            conn.execute('ALTER TABLE event_media DROP COLUMN processing_generation')
            conn.execute('UPDATE app_schema_version SET version=7 WHERE singleton=1')
        initialize()
        with connect() as conn:
            self.assertEqual(schema_version(conn), LATEST_VERSION)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM processing_job').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM host_ai_job').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM host_ai_worker').fetchone()[0], 0)
        self.assertEqual(self.client.get('/api/auth/me', headers=login(self.client)).status_code, 200)

    def test_week_numbers_match_sqlite_at_year_boundaries(self):
        dates = ['2023-01-01', '2023-01-02', '2023-12-31', '2024-01-01', '2024-02-29', '2024-12-31']
        with sqlite3.connect(':memory:') as sqlite, connect() as postgres:
            for date in dates:
                expected = int(sqlite.execute("SELECT strftime('%W',?)", (date,)).fetchone()[0])
                result = postgres.execute('SELECT ' + week_expression('value') + ' FROM (SELECT ?::text AS value) v', (date,)).fetchone()[0]
                self.assertEqual(int(result), expected)

    def test_serialized_write_lock_waits_for_commit(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        started, acquired = Event(), Event()
        def contender():
            with connect() as conn:
                started.set()
                begin_write(conn)
                acquired.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with connect() as conn:
                begin_write(conn)
                task = pool.submit(contender)
                self.assertTrue(started.wait(3))
                self.assertFalse(acquired.wait(0.2))
                conn.commit()
            task.result(timeout=5)
            self.assertTrue(acquired.is_set())
