"""Initialization ordering controls; real PostgreSQL assertions require a test DB."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.db import postgres
from backend.db.connection import connect, schema_version
from backend.db.migrations import LATEST_VERSION
from backend.tests.support import database_environment


SEED_SQL = (Path(postgres.__file__).parent / 'postgres_seed.sql').read_text(encoding='utf-8')


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class InitializationConnection:
    dialect = 'postgresql'

    def __init__(self, version):
        self.version = version
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.calls.append(('rollback' if kind else 'commit',))

    def execute(self, sql, parameters=None):
        self.calls.append(('sql', sql, parameters))
        if sql == 'SELECT version FROM app_schema_version WHERE singleton=1':
            return Cursor(None if self.version is None else (self.version,))
        if sql == "SELECT to_regclass('camera_info')":
            return Cursor((None,))
        if sql == "SELECT to_regclass('auto_decision_settings')":
            return Cursor(('auto_decision_settings',))
        return Cursor()


class PostgresInitializationOrderTests(unittest.TestCase):
    def run_initialization(self, version, *, seed, rebuild_error=None):
        conn = InitializationConnection(version)
        def migrate(connection, *, postgres):
            self.assertIs(connection, conn)
            self.assertTrue(postgres)
            conn.calls.append(('zones',))
        def rebuild(connection):
            self.assertIs(connection, conn)
            conn.calls.append(('rebuild',))
            if rebuild_error:
                raise rebuild_error
        with patch.object(postgres, 'connect', return_value=conn), \
                patch('backend.db.event_zones.migrate_event_zones', side_effect=migrate), \
                patch('backend.db.false_positives.rebuild_false_positive_aggregates', side_effect=rebuild):
            if rebuild_error:
                with self.assertRaisesRegex(RuntimeError, 'rebuild failed'):
                    postgres.initialize(seed=seed)
            else:
                postgres.initialize(seed=seed)
        return conn.calls

    def test_fresh_and_v21_seed_precede_rebuild_and_final_version(self):
        for version in (None, 21):
            with self.subTest(version=version):
                calls = self.run_initialization(version, seed=True)
                seed_index = calls.index(('sql', SEED_SQL, None))
                rebuild_index = calls.index(('rebuild',))
                self.assertLess(seed_index, rebuild_index)
                self.assertEqual(calls.count(('rebuild',)), 1)
                self.assertEqual(calls[rebuild_index + 1], (
                    'sql', 'UPDATE app_schema_version SET version=? WHERE singleton=1', (LATEST_VERSION,)))
                if version is None:
                    self.assertLess(calls.index(('zones',)), seed_index)
                self.assertEqual(calls[-1], ('commit',))

    def test_current_schema_reinitialization_never_repeats_rebuild(self):
        for seed in (False, True):
            with self.subTest(seed=seed):
                calls = self.run_initialization(LATEST_VERSION, seed=seed)
                self.assertNotIn(('rebuild',), calls)
                self.assertNotIn(('zones',), calls)
                self.assertEqual(('sql', SEED_SQL, None) in calls, seed)
                self.assertEqual(calls[-1], ('commit',))

    def test_upgrade_without_seed_still_reconciles_once(self):
        calls = self.run_initialization(21, seed=False)
        self.assertNotIn(('sql', SEED_SQL, None), calls)
        self.assertEqual(calls.count(('rebuild',)), 1)
        self.assertEqual(calls[-1], ('commit',))

    def test_rebuild_failure_rolls_back_and_does_not_advance_v21(self):
        calls = self.run_initialization(21, seed=True, rebuild_error=RuntimeError('rebuild failed'))
        self.assertEqual(calls[-1], ('rollback',))
        self.assertFalse(any(call[0] == 'sql' and call[1].startswith('UPDATE app_schema_version')
                             for call in calls))


@unittest.skipUnless(os.environ.get('PPE_TEST_POSTGRES_URL'), 'Dedicated PostgreSQL test database required')
class PostgresFreshSeedIntegrationTests(unittest.TestCase):
    def test_fresh_seed_has_snapshots_and_reconciled_false_positive_summary(self):
        # database_environment creates and removes a unique schema only in a
        # dedicated kyo_test database. Never point this at a production database.
        with TemporaryDirectory() as directory, database_environment(directory):
            postgres.initialize(seed=True)
            with connect() as conn:
                self.assertEqual(schema_version(conn), LATEST_VERSION)
                events = conn.execute('SELECT COUNT(*) FROM candidate_event').fetchone()[0]
                self.assertEqual(events, 3)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_zone_snapshot').fetchone()[0], events)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidate_event WHERE event_status='false_positive'").fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT COALESCE(SUM(false_positive_count),0) FROM false_positive_aggregate').fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT false_positive_count FROM quarterly_summary WHERE quarter='2026-Q2'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
