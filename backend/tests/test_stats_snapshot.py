"""One snapshot per statistics result, and bounded PostgreSQL transaction retry."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
import os
import unittest
from unittest.mock import Mock, patch

from backend.db.connection import is_postgres, run_stats_transaction
from backend.db import event_repository as repository
from backend.tests.support import isolated_api


class SerializationFailure(Exception):
    sqlstate = '40001'


class UniqueViolation(Exception):
    sqlstate = '23505'


class FakePostgres:
    dialect = 'postgresql'

    def __init__(self, commit_error=None):
        self.statements = []
        self.commit_error = commit_error
        self.exited = False
        self.failed = False

    def execute(self, sql, parameters=None):
        self.statements.append(sql)

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.exited = True
        self.failed = kind is not None
        if kind is None and self.commit_error:
            raise self.commit_error


class StatsTransactionTests(unittest.TestCase):
    def test_postgres_isolation_precedes_lock_and_operation(self):
        connection = FakePostgres()
        def operation(conn):
            self.assertEqual(conn.statements[0], 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            self.assertIn('pg_advisory_xact_lock', conn.statements[1])
            return 'result'
        self.assertEqual(run_stats_transaction(operation, write=True,
                                             connection_factory=lambda: connection), 'result')
        self.assertTrue(connection.exited)

    def test_postgres_reader_is_read_only_without_write_lock(self):
        connection = FakePostgres()
        run_stats_transaction(lambda conn: None, connection_factory=lambda: connection)
        self.assertEqual(connection.statements, [
            'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'])

    def test_body_serialization_failure_retries_with_new_connection(self):
        connections = [FakePostgres(), FakePostgres()]
        factory = Mock(side_effect=connections)
        operation = Mock(side_effect=[SerializationFailure(), 'fresh result'])
        with patch('backend.db.connection.time.sleep'):
            result = run_stats_transaction(operation, write=True, connection_factory=factory)
        self.assertEqual(result, 'fresh result')
        self.assertEqual(factory.call_count, 2)
        self.assertTrue(connections[0].failed)
        self.assertEqual([call.args[0] for call in operation.call_args_list], connections)

    def test_commit_serialization_failure_retries_whole_operation(self):
        connections = [FakePostgres(SerializationFailure()), FakePostgres()]
        operation = Mock(side_effect=['discarded', 'committed'])
        with patch('backend.db.connection.time.sleep'):
            result = run_stats_transaction(operation, write=True,
                                           connection_factory=Mock(side_effect=connections))
        self.assertEqual(result, 'committed')
        self.assertEqual(operation.call_count, 2)
        self.assertTrue(all(conn.exited for conn in connections))

    def test_retry_is_bounded_and_other_errors_are_not_retried(self):
        operation = Mock(side_effect=SerializationFailure())
        factory = Mock(side_effect=lambda: FakePostgres())
        with patch('backend.db.connection.time.sleep'):
            with self.assertRaises(SerializationFailure):
                run_stats_transaction(operation, write=True, connection_factory=factory)
        self.assertEqual(factory.call_count, 3)
        factory.reset_mock()
        with self.assertRaisesRegex(ValueError, 'not retryable'):
            run_stats_transaction(Mock(side_effect=ValueError('not retryable')),
                                  write=True, connection_factory=factory)
        self.assertEqual(factory.call_count, 1)

    def test_unique_violation_is_not_blanket_retried(self):
        factory = Mock(side_effect=lambda: FakePostgres())
        with self.assertRaises(UniqueViolation):
            run_stats_transaction(Mock(side_effect=UniqueViolation()), write=True,
                                  connection_factory=factory)
        self.assertEqual(factory.call_count, 1)

    def test_recommendation_parent_gate_precedes_source_reads_and_retries(self):
        class StaleParent(FakePostgres):
            def execute(self, sql, parameters=None):
                super().execute(sql, parameters)
                if sql.startswith('UPDATE quarterly_summary'):
                    raise SerializationFailure()

        class ExistingParent(FakePostgres):
            def execute(self, sql, parameters=None):
                super().execute(sql, parameters)
                return Mock(fetchone=Mock(return_value=(1,)))
        connections = [StaleParent(), ExistingParent()]
        def replace(conn, quarter):
            self.assertEqual(quarter, '2026-Q2')
            self.assertTrue(conn.statements[-2].startswith('UPDATE quarterly_summary'))
        with patch.object(repository, 'get_connection', side_effect=connections), \
                patch.object(repository, '_replace_education_recommendations', side_effect=replace) as rebuild, \
                patch.object(repository, '_read_education_recommendations', return_value={'items': []}), \
                patch('backend.db.connection.time.sleep'):
            self.assertEqual(repository.generate_education_recommendations('2026-Q2'), {'items': []})
        self.assertTrue(connections[0].failed)
        self.assertEqual(rebuild.call_count, 1)


class StatsSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        with repository.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='pending'")

    def interleave(self, operation, match_sql, change):
        """Commit a writer between reads on PG; SQLite must delay its commit."""
        original = repository.get_connection
        trigger, started, committed = Event(), Event(), Event()
        postgres = is_postgres()
        test = self
        observed = []

        def writer():
            if not trigger.wait(5):
                raise AssertionError('reader did not reach interleave point')
            started.set()
            # Deliberately do not take the application's advisory lock.
            with original() as conn:
                change(conn)
            committed.set()

        class ConnectionProbe:
            def __init__(self):
                self.connection = original()

            def __enter__(self):
                self.connection.__enter__()
                return self

            def __exit__(self, *args):
                return self.connection.__exit__(*args)

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, sql, parameters=None):
                cursor = (self.connection.execute(sql) if parameters is None
                          else self.connection.execute(sql, parameters))
                if not observed and match_sql(sql):
                    observed.append(sql)
                    trigger.set()
                    test.assertTrue(started.wait(5))
                    if postgres:
                        test.assertTrue(committed.wait(5), 'non-cooperating writer must be able to commit')
                    else:
                        test.assertFalse(committed.wait(0.15), 'SQLite snapshot must block the writer commit')
                return cursor

        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(writer)
            try:
                with patch.object(repository, 'get_connection', ConnectionProbe):
                    result = operation()
            finally:
                # Allow the worker to finish even if an earlier assertion fails.
                trigger.set()
            task.result(timeout=5)
        self.assertEqual(len(observed), 1)
        self.assertTrue(committed.is_set())
        return result

    def test_generation_does_not_mix_review_change_between_aggregates(self):
        def confirm(conn):
            conn.execute("UPDATE candidate_event SET event_status='confirmed' WHERE event_id='EVT_0001'")
        stats = self.interleave(
            lambda: repository.generate_quarterly_stats('2026-Q2'),
            lambda sql: "SELECT COUNT(*) AS count" in sql and "event_status = 'confirmed'" in sql,
            confirm,
        )
        self.assertEqual(stats['summary']['confirmed_count'], 0)
        self.assertEqual(sum(row['confirmed_count'] for row in stats['by_ppe_type']), 0)
        self.assertEqual(sum(row['confirmed_count'] for row in stats['by_zone']), 0)
        latest = repository.generate_quarterly_stats('2026-Q2')
        self.assertEqual(latest['summary']['confirmed_count'], 1)
        self.assertEqual(sum(row['confirmed_count'] for row in latest['by_ppe_type']), 1)

    def test_get_reads_summary_and_details_from_same_snapshot(self):
        with repository.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='confirmed' WHERE event_id='EVT_0001'")
        before = repository.generate_quarterly_stats('2026-Q2')
        def replace_cache(conn):
            conn.execute("UPDATE quarterly_summary SET confirmed_count=2 WHERE quarter='2026-Q2'")
            conn.execute("UPDATE quarterly_ppe_stats SET confirmed_count=2 WHERE quarter='2026-Q2'")
            conn.execute("UPDATE quarterly_zone_stats SET confirmed_count=2 WHERE quarter='2026-Q2'")
        result = self.interleave(
            lambda: repository.get_quarterly_stats('2026-Q2'),
            lambda sql: 'FROM quarterly_summary' in sql,
            replace_cache,
        )
        self.assertEqual(result, before)
        latest = repository.get_quarterly_stats('2026-Q2')
        self.assertEqual(latest['summary']['confirmed_count'], 2)
        self.assertEqual(sum(row['confirmed_count'] for row in latest['by_ppe_type']), 2)

    def test_generation_returns_without_public_post_commit_requery(self):
        with patch.object(repository, 'get_quarterly_stats', side_effect=AssertionError('separate stats read')):
            result = repository.generate_quarterly_stats('2026-Q2')
        self.assertEqual(result['summary']['confirmed_count'], 0)
        with patch.object(repository, 'get_education_recommendations', side_effect=AssertionError('separate recommendation read')):
            recommendations = repository.generate_education_recommendations('2026-Q2')
        self.assertEqual(recommendations['items'], [])

    def test_recommendation_totals_and_groups_share_snapshot(self):
        repository.generate_quarterly_stats('2026-Q2')
        def confirm(conn):
            conn.execute("UPDATE candidate_event SET event_status='confirmed' WHERE event_id='EVT_0001'")
        # Denominator and numerator now share the grouped source rows. Commit
        # the non-cooperating writer after that SELECT executes, before rows
        # are materialized into the recommendation cache.
        result = self.interleave(
            lambda: repository.generate_education_recommendations('2026-Q2'),
            lambda sql: "GROUP BY ce.ppe_type, COALESCE(ez.zone_name" in sql,
            confirm,
        )
        self.assertEqual(result['items'], [])
        latest = repository.generate_education_recommendations('2026-Q2')
        self.assertEqual(len(latest['items']), 1)
        self.assertEqual(latest['items'][0]['score_breakdown']['confirmed_count'], 1)
        self.assertEqual(latest['items'][0]['score_breakdown']['zone_concentration'], 1)

    def test_concurrent_generators_leave_one_complete_cache(self):
        with repository.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='confirmed' WHERE event_id='EVT_0001'")
        barrier = Barrier(2)
        def generate():
            barrier.wait(timeout=5)
            return repository.generate_quarterly_stats('2026-Q2')
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(generate) for _ in range(2)]
            results = [task.result(timeout=10) for task in tasks]
        for result in results:
            self.assertEqual(result['summary']['confirmed_count'], 1)
            self.assertEqual(sum(row['confirmed_count'] for row in result['by_ppe_type']), 1)
        self.assertEqual(repository.get_quarterly_stats('2026-Q2'), results[-1])
        with repository.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM quarterly_summary WHERE quarter='2026-Q2'").fetchone()[0], 1)

    def test_concurrent_first_recommendation_generators_preserve_parent(self):
        with repository.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='confirmed' WHERE event_id='EVT_0001'")
        repository.generate_quarterly_stats('2026-Q2')
        with repository.get_connection() as conn:
            conn.execute("DELETE FROM education_recommendation WHERE quarter='2026-Q2'")
            parent = dict(conn.execute("SELECT * FROM quarterly_summary WHERE quarter='2026-Q2'").fetchone())
        barrier = Barrier(2)
        def generate():
            barrier.wait(timeout=5)
            return repository.generate_education_recommendations('2026-Q2')
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(generate) for _ in range(2)]
            results = [task.result(timeout=10) for task in tasks]
        for result in results:
            self.assertEqual(len(result['items']), 1)
            self.assertEqual(result['items'][0]['score_breakdown']['confirmed_count'], 1)
        with repository.get_connection() as conn:
            self.assertEqual(dict(conn.execute(
                "SELECT * FROM quarterly_summary WHERE quarter='2026-Q2'").fetchone()), parent)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM education_recommendation WHERE quarter='2026-Q2'").fetchone()[0], 1)

    def test_new_empty_quarter_creates_consistent_summary(self):
        self.assertEqual(repository.generate_education_recommendations('2099-Q1')['items'], [])
        with repository.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM quarterly_summary WHERE quarter='2099-Q1'").fetchone()[0], 1)

    def test_concurrent_new_quarter_generation_has_one_consistent_parent(self):
        with repository.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='confirmed', timestamp_start='2099-01-02 00:00:00' WHERE event_id='EVT_0001'")
        barrier = Barrier(2)
        def generate():
            barrier.wait(timeout=5)
            return repository.generate_education_recommendations('2099-Q1')
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(generate), pool.submit(generate)
            for result in (a.result(timeout=10), b.result(timeout=10)):
                self.assertEqual(len(result['items']), 1)
        with repository.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM quarterly_summary WHERE quarter='2099-Q1'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM education_recommendation WHERE quarter='2099-Q1'").fetchone()[0], 1)

    def test_new_quarter_recommendations_build_fk_parent_and_rollback_on_error(self):
        with repository.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='confirmed', timestamp_start='2099-01-02 00:00:00' WHERE event_id='EVT_0001'")
        original = repository._replace_education_recommendations
        def fail(conn, quarter):
            original(conn, quarter)
            raise RuntimeError('injected')
        with patch.object(repository, '_replace_education_recommendations', fail):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                repository.generate_education_recommendations('2099-Q1')
        with repository.get_connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM quarterly_summary WHERE quarter='2099-Q1'").fetchone())
        result = repository.generate_education_recommendations('2099-Q1')
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(repository.get_quarterly_stats('2099-Q1')['summary']['confirmed_count'], 1)
        # Regeneration legitimately refreshes generated_at across a second boundary.
        rerun = repository.generate_education_recommendations('2099-Q1')['items']
        stable = lambda items: [{k:v for k,v in item.items() if k != 'generated_at'} for item in items]
        self.assertEqual(stable(rerun), stable(result['items']))

    @unittest.skipUnless(os.environ.get('PPE_TEST_POSTGRES_URL'), 'Dedicated PostgreSQL test database required')
    def test_postgres_stale_first_recommendation_generator_retries_parent_gate(self):
        with repository.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='confirmed' WHERE event_id='EVT_0001'")
        repository.generate_quarterly_stats('2026-Q2')
        with repository.get_connection() as conn:
            conn.execute("DELETE FROM education_recommendation WHERE quarter='2026-Q2'")
        original = repository.get_connection
        first_locked, stale_snapshot = Event(), Event()
        count_lock = Lock()
        attempts = []
        test = self

        class ConnectionProbe:
            def __init__(self):
                self.connection = original()
                with count_lock:
                    self.index = len(attempts)
                    attempts.append(self)

            def __enter__(self):
                self.connection.__enter__()
                return self

            def __exit__(self, *args):
                return self.connection.__exit__(*args)

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, sql, parameters=None):
                if 'pg_advisory_xact_lock' in sql and self.index == 1:
                    # Deterministically establish the waiter's old RR snapshot
                    # before the first transaction populates the empty cache.
                    self.connection.execute("SELECT COUNT(*) FROM education_recommendation WHERE quarter='2026-Q2'").fetchone()
                    stale_snapshot.set()
                cursor = (self.connection.execute(sql) if parameters is None
                          else self.connection.execute(sql, parameters))
                if 'pg_advisory_xact_lock' in sql and self.index == 0:
                    first_locked.set()
                    test.assertTrue(stale_snapshot.wait(5))
                return cursor

        with ThreadPoolExecutor(max_workers=2) as pool, \
                patch.object(repository, 'get_connection', ConnectionProbe):
            first = pool.submit(repository.generate_education_recommendations, '2026-Q2')
            try:
                self.assertTrue(first_locked.wait(5))
                second = pool.submit(repository.generate_education_recommendations, '2026-Q2')
                results = [first.result(timeout=10), second.result(timeout=10)]
            finally:
                stale_snapshot.set()
        # Without the parent gate the second transaction sees no ranks and its
        # plain INSERT raises 23505 instead of entering the 40001 retry path.
        self.assertEqual(len(attempts), 3)
        for result in results:
            self.assertEqual(len(result['items']), 1)
        self.assertEqual(len(repository.get_education_recommendations('2026-Q2')['items']), 1)


if __name__ == '__main__':
    unittest.main()
