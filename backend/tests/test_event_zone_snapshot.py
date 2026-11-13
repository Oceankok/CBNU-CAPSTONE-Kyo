"""Keep migration-time and registration-time zone attribution immutable."""
import unittest
from backend.db import event_repository as repository
from backend.db.connection import is_postgres, schema_version
from backend.db.init_db import init_database
from backend.db.migrations import LATEST_VERSION
from backend.tests.support import isolated_api


class EventZoneSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())

    def test_v20_upgrade_freezes_current_zone_and_never_rewrites_it(self):
        with repository.get_connection() as conn:
            conn.execute('DROP TRIGGER candidate_event_capture_zone ON candidate_event' if is_postgres()
                         else 'DROP TRIGGER candidate_event_capture_zone')
            conn.execute('DROP TABLE event_zone_snapshot')
            conn.execute('UPDATE app_schema_version SET version=20 WHERE singleton=1' if is_postgres()
                         else 'PRAGMA user_version=20')
            count = conn.execute('SELECT COUNT(*) FROM candidate_event').fetchone()[0]
            conn.execute("UPDATE camera_info SET zone_name='자재 이동 구역' WHERE camera_id='CAM_001'")
        init_database()
        with repository.get_connection() as conn:
            self.assertEqual(schema_version(conn), LATEST_VERSION)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM candidate_event').fetchone()[0], count)
            before = [dict(row) for row in conn.execute('SELECT * FROM event_zone_snapshot ORDER BY event_id')]
            row = next(row for row in before if row['event_id'] == 'EVT_0001')
            self.assertEqual(row['source'], 'migration_current')
            self.assertEqual(row['zone_name'], '자재 이동 구역')
            conn.execute("UPDATE camera_info SET zone_name='프레스 구역' WHERE camera_id='CAM_001'")
        init_database()
        with repository.get_connection() as conn:
            self.assertEqual([dict(row) for row in conn.execute('SELECT * FROM event_zone_snapshot ORDER BY event_id')], before)
        self.assertEqual(repository.get_candidate_event_by_id('EVT_0001')['zone_name'], '자재 이동 구역')

    def test_camera_move_preserves_old_event_filters_stats_and_recommendations(self):
        with repository.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET timestamp_start='2035-01-05 10:00:00',timestamp_end='2035-01-05 10:00:03',event_status='confirmed' WHERE event_id='EVT_0001'")
            conn.execute("UPDATE camera_info SET zone_name='자재 이동 구역' WHERE camera_id='CAM_001'")
        new = repository.get_candidate_event_by_id('EVT_0001')
        new.update(event_id='EVT_AFTER_MOVE', thumbnail_path='', video_clip_path='')
        repository.insert_candidate_event(new)
        self.assertEqual(repository.get_candidate_event_by_id('EVT_0001')['zone_name'], '프레스 구역')
        self.assertEqual(repository.get_candidate_event_by_id('EVT_AFTER_MOVE')['zone_name'], '자재 이동 구역')
        for zone, ident in [('프레스 구역', 'EVT_0001'), ('자재 이동 구역', 'EVT_AFTER_MOVE')]:
            total, rows = repository.get_candidate_events_page(zone=zone)
            self.assertIn(ident, [row['event_id'] for row in rows])
            other = 'EVT_AFTER_MOVE' if ident == 'EVT_0001' else 'EVT_0001'
            self.assertNotIn(other, [row['event_id'] for row in rows])
        stats = repository.generate_quarterly_stats('2035-Q1')
        self.assertEqual({row['zone_name']: row['confirmed_count'] for row in stats['by_zone']},
                         {'프레스 구역': 1, '자재 이동 구역': 1})
        recs = repository.generate_education_recommendations('2035-Q1')['items']
        self.assertEqual({row['zone_name'] for row in recs}, {'프레스 구역', '자재 이동 구역'})
        with repository.get_connection() as conn:
            row = conn.execute("SELECT source FROM event_zone_snapshot WHERE event_id='EVT_AFTER_MOVE'").fetchone()
            self.assertEqual(row[0], 'registered_current')

    def test_null_snapshot_does_not_fall_back_to_later_camera_assignment(self):
        with repository.get_connection() as conn:
            conn.execute("UPDATE camera_info SET zone_name=NULL WHERE camera_id='CAM_001'")
        new = repository.get_candidate_event_by_id('EVT_0001')
        new.update(event_id='EVT_UNASSIGNED', thumbnail_path='', video_clip_path='')
        repository.insert_candidate_event(new)
        with repository.get_connection() as conn:
            conn.execute("UPDATE camera_info SET zone_name='자재 이동 구역' WHERE camera_id='CAM_001'")
        self.assertIsNone(repository.get_candidate_event_by_id('EVT_UNASSIGNED')['zone_name'])
