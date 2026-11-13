"""Optional W15 filter contract, independent of frontend adoption."""
from backend.tests import test_event_pagination as fixture_module
from backend.db.event_repository import get_connection
import unittest


class MultiZoneEventTests(unittest.TestCase):
    setUp = fixture_module.EventPaginationApiTests.setUp

    def zones_fixture(self):
        with get_connection() as c:
            for ident, zone in [('PAGE_A', 'A'), ('PAGE_B', 'B'), ('PAGE_C', 'C')]:
                c.execute('UPDATE event_zone_snapshot SET zone_name=? WHERE event_id=?',
                          (zone, self.event_ids[ident]))
            # Current camera attribution is deliberately different from history.
            c.execute("UPDATE camera_info SET zone_name='Current' WHERE camera_id='CAM_001'")

    def get(self, path='/api/events', params=None):
        return self.client.get(path, params=params, headers=self.admin)

    def test_union_pagination_and_clear_preserve_snapshot(self):
        self.zones_fixture()
        selected = [('zones', 'A'), ('zones', 'B'), ('limit', '1')]
        first = self.get(params=selected).json()
        second = self.get(params=selected + [('offset', '1')]).json()
        self.assertEqual((first['total'], first['has_more']), (2, True))
        self.assertEqual((second['total'], second['has_more']), (2, False))
        self.assertEqual({row['event_id'] for page in (first, second) for row in page['items']},
                         {self.event_ids['PAGE_A'], self.event_ids['PAGE_B']})
        self.assertEqual(self.get(params={'zone': 'A'}).json()['total'], 1)
        self.assertEqual(self.get().json()['total'], 3)
        self.assertEqual(self.get(params={'zones': 'Current'}).json()['total'], 0)

    def test_summary_union_counts_and_pending_use_same_filter(self):
        self.zones_fixture()
        summary = self.get('/api/events/summary', [('zones', 'A'), ('zones', 'B')]).json()
        self.assertEqual(summary['total'], 2)
        self.assertEqual(summary['by_zone'], {'A': 1, 'B': 1})
        self.assertEqual(summary['by_status'], {'pending': 1, 'confirmed': 1})
        self.assertEqual([r['event_id'] for r in summary['recent_pending']], [self.event_ids['PAGE_A']])

    def test_duplicates_and_unknown_names_do_not_duplicate_or_expand(self):
        self.zones_fixture()
        for path in ['/api/events', '/api/events/summary']:
            payload = self.get(path, [('zones', 'A'), ('zones', 'A'), ('zones', 'unknown')]).json()
            self.assertEqual(payload['total'], 1)
            self.assertEqual(self.get(path, {'zones': 'unknown'}).json()['total'], 0)

    def test_other_filters_are_intersections_with_union(self):
        self.zones_fixture()
        params = [('zones', 'A'), ('zones', 'B'), ('status', 'confirmed'), ('ppe_type', 'vest')]
        for path in ['/api/events', '/api/events/summary']:
            self.assertEqual(self.get(path, params).json()['total'], 1)

    def test_injection_is_bound_literal(self):
        self.zones_fixture()
        for path in ['/api/events', '/api/events/summary']:
            response = self.get(path, {'zones': "A') OR 1=1 --"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['total'], 0)
        self.assertEqual(self.get().json()['total'], 3)

    def test_conflicting_and_invalid_parameters_are_rejected(self):
        for path in ['/api/events', '/api/events/summary']:
            self.assertEqual(self.get(path, [('zone', 'A'), ('zones', 'B')]).status_code, 400)
            for params in [[('zones', '')], [('zones', 'x' * 101)], [('zones', 'A')] * 51]:
                response = self.get(path, params)
                self.assertEqual(response.status_code, 422, response.text)

    def test_allowed_size_boundaries_and_existing_auth_remain(self):
        for path in ['/api/events', '/api/events/summary']:
            self.assertEqual(self.get(path, [('zones', 'x' * 100)] * 50).status_code, 200)
            response = self.client.get(path, params={'zones': 'A'})
            self.assertIn(response.status_code, (401, 403))
