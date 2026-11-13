"""The approved four-quarter window ends at the selected quarter, never the latest data."""
import sqlite3
import unittest

from backend.db.quarterly_trends import normalize_quarterly_trends


def row(quarter, helmet=0, vest=0):
    return {'quarter': quarter, 'helmet': helmet, 'vest': vest}


class QuarterlyTrendsTests(unittest.TestCase):
    def normalize(self, rows, target='2026-Q2'):
        return normalize_quarterly_trends(rows, target)

    def zeros(self):
        return [row('2025-Q3'), row('2025-Q4'), row('2026-Q1'), row('2026-Q2')]

    def test_empty_history_includes_previous_and_selected_quarters(self):
        self.assertEqual(self.normalize([]), self.zeros())

    def test_year_rollover_uses_previous_calendar_quarter(self):
        self.assertEqual(self.normalize([], '2026-Q1'),
                         [row('2025-Q2'), row('2025-Q3'), row('2025-Q4'), row('2026-Q1')])

    def test_selected_quarter_only_adds_missing_previous_zero(self):
        self.assertEqual(self.normalize([row('2026-Q2', 3, 2)]),
                         self.zeros()[:-1] + [row('2026-Q2', 3, 2)])

    def test_previous_quarter_only_keeps_selected_zero(self):
        self.assertEqual(self.normalize([row('2026-Q1', 3, 2)]),
                         self.zeros()[:2] + [row('2026-Q1', 3, 2), row('2026-Q2')])

    def test_future_data_is_excluded_from_historical_request(self):
        source = [row('2026-Q4', 999, 999), row('2026-Q1', 2, 3), row('2026-Q2', 4, 5)]
        self.assertEqual(self.normalize(source),
                         self.zeros()[:2] + [row('2026-Q1', 2, 3), row('2026-Q2', 4, 5)])

    def test_only_future_data_behaves_like_empty_history(self):
        self.assertEqual(self.normalize([row('2027-Q1', 7, 8)]), self.zeros())

    def test_older_history_is_excluded_and_gaps_are_zero_filled(self):
        result = self.normalize([row('2024-Q4', 2, 1), row('2026-Q2', 3, 4)])
        self.assertEqual(result, self.zeros()[:-1] + [row('2026-Q2', 3, 4)])

    def test_old_history_and_empty_selected_quarter_keep_contiguous_tail(self):
        self.assertEqual(self.normalize([row('2025-Q4', 1, 2)]),
                         [row('2025-Q3'), row('2025-Q4', 1, 2), row('2026-Q1'), row('2026-Q2')])

    def test_input_order_does_not_change_output_and_input_is_unchanged(self):
        source = [row('2026-Q2', 4, 3), row('2025-Q4', 2, 1)]
        before = [dict(item) for item in source]
        result = self.normalize(iter(source))
        self.assertEqual(result, [row('2025-Q3'), row('2025-Q4', 2, 1), row('2026-Q1'), row('2026-Q2', 4, 3)])
        self.assertEqual(source, before)

    def test_sqlite_rows_and_null_counts_follow_existing_response_contract(self):
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT '2026-Q2' AS quarter,NULL AS helmet,2 AS vest").fetchall()
        self.assertEqual(self.normalize(rows), self.zeros()[:-1] + [row('2026-Q2', 0, 2)])

    def test_invalid_quarter_does_not_silently_produce_wrong_calendar(self):
        for quarter in ('2026-Q0', '2026-Q5', '2026Q2', None):
            with self.subTest(quarter=quarter), self.assertRaises(ValueError):
                self.normalize([], quarter)


class QuarterlyTrendsIntegrationTests(unittest.TestCase):
    def test_generation_and_old_cache_reads_keep_four_calendar_quarters(self):
        from backend.db import event_repository as repo
        from backend.tests.support import isolated_api
        with isolated_api():
            with repo.get_connection() as conn:
                conn.execute("UPDATE candidate_event SET event_status='pending'")
                conn.execute("UPDATE candidate_event SET event_status='confirmed', ppe_type='helmet', timestamp_start='2030-01-01 12:00:00' WHERE event_id='EVT_0001'")
            result = repo.generate_quarterly_stats('2030-Q2')
            expected = [row('2029-Q3'), row('2029-Q4'), row('2030-Q1', 1), row('2030-Q2')]
            self.assertEqual(result['trend'], expected)
            with repo.get_connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM quarterly_trend_stats WHERE target_quarter='2030-Q2'").fetchone()[0], 4)
                conn.execute("DELETE FROM quarterly_trend_stats WHERE target_quarter='2030-Q2' AND helmet=0")
                conn.execute("INSERT INTO quarterly_trend_stats(trend_id,target_quarter,quarter,helmet,vest) VALUES('legacy-future','2030-Q2','2031-Q1',999,999)")
            self.assertEqual(repo.get_quarterly_stats('2030-Q2')['trend'], expected)
            with repo.get_connection() as conn:
                # GET repairs only response shape, not historical cache storage.
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM quarterly_trend_stats WHERE trend_id='legacy-future'").fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
