"""Exercise generated targets through the API, not manually seeded recommendations."""
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.db import event_repository as repo
from backend.tests.support import isolated_api, login
from backend.zones.membership import replace_user_zones


BASE = '/api/education/bundles'
QUARTER = '2026-Q1'
CONTENT = {'title': 'Common PPE guidance', 'body': 'Check before work.', 'checklist': ['Check fit']}


class EducationBundleTargetTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        self.worker = login(self.client, 'worker01')
        with repo.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='pending'")

    def add(self, zone, ppe='helmet', count=1, *, stamp='2026-01-05T00:00:00Z'):
        camera = 'G1_CAM_' + uuid4().hex
        ids = []
        with repo.get_connection() as conn:
            conn.execute('INSERT INTO zone_rule(zone_name) VALUES(?) ON CONFLICT DO NOTHING', (zone,))
            conn.execute("INSERT INTO camera_info(camera_id,camera_location,zone_name,status) VALUES(?,?,?,'active')",
                         (camera, zone, zone))
            for _ in range(count):
                identifier = 'G1_EVT_' + uuid4().hex
                ids.append(identifier)
                conn.execute('''INSERT INTO candidate_event(event_id,camera_id,ppe_type,event_status,timestamp_start,timestamp_end)
                    VALUES(?,?,?,'confirmed',?,?)''', (identifier, camera, ppe, stamp, stamp))
        return ids

    def generate(self):
        response = self.client.post('/api/recommendations/generate', params={'quarter': QUARTER}, headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def targets(self):
        response = self.client.get(BASE + '/targets', params={'quarter': QUARTER}, headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def preview(self, rows):
        response = self.client.post(BASE + '/preview', headers=self.admin,
                                    json={'recommendation_ids': [row['recommendation_id'] for row in rows]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create(self, rows):
        preview = self.preview(rows)
        response = self.client.post(BASE, headers=self.admin, json={**CONTENT,
            'recommendation_ids': [row['recommendation_id'] for row in rows], 'expected_snapshot': preview['snapshot']})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['sources'], preview['sources'])
        return response.json()

    def publish(self, bundle):
        return self.client.post(BASE + '/' + bundle['bundle_id'] + '/publish', headers=self.admin,
                                json={'expected_version': bundle['version'], 'approved': True})

    def visible(self):
        response = self.client.get('/api/worker/education-bundles', headers=self.worker)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['items']

    def test_four_and_twenty_generated_zones_are_selectable_but_highlights_stay_three(self):
        for number in range(1, 21):
            self.add(f'G1_ZONE_{number:02}')
            if number not in (4, 20):
                continue
            highlighted = self.generate()
            targets = self.targets()
            self.assertEqual(len(highlighted['items']), 3)
            self.assertEqual(len(targets['items']), number)
            self.assertEqual(highlighted['items'], targets['items'][:3])
            fetched = self.client.get('/api/recommendations', params={'quarter': QUARTER}, headers=self.admin)
            self.assertEqual(fetched.json(), highlighted)
            self.assertEqual([r['recommendation_rank'] for r in targets['items']], list(range(1, number + 1)))
            with repo.get_connection() as conn:
                stored = conn.execute('SELECT recommendation_id FROM education_recommendation WHERE quarter=?', (QUARTER,)).fetchall()
            self.assertEqual({r[0] for r in stored}, {r['recommendation_id'] for r in targets['items']})
            preview = self.preview(targets['items'])
            self.assertEqual(preview['confirmed_count_sum'], number)
            self.assertEqual({s['recommendation_id'] for s in preview['sources']}, {r[0] for r in stored})
            self.assertEqual(self.publish(self.create(targets['items'])).status_code, 200)
        self.add('G1_ZONE_21')
        self.generate()
        rows = self.targets()['items']
        self.assertEqual(len(rows), 21)
        response = self.client.post(BASE + '/preview', headers=self.admin,
                                    json={'recommendation_ids': [r['recommendation_id'] for r in rows]})
        self.assertEqual(response.status_code, 422)

    def test_other_ppe_cannot_exhaust_same_ppe_targets_and_mixed_selection_is_rejected(self):
        for number in range(4):
            self.add(f'G1_ZONE_{number}', 'helmet')
            self.add(f'G1_ZONE_{number}', 'vest', 5)
        self.assertEqual({r['ppe_type'] for r in self.generate()['items']}, {'vest'})
        rows = self.targets()['items']
        self.assertEqual(len(rows), 8)
        for ppe, expected_count in [('helmet', 4), ('vest', 20)]:
            selected = [row for row in rows if row['ppe_type'] == ppe]
            self.assertEqual(self.preview(selected)['confirmed_count_sum'], expected_count)
            self.create(selected)
        response = self.client.post(BASE + '/preview', headers=self.admin,
            json={'recommendation_ids': [rows[0]['recommendation_id'], rows[-1]['recommendation_id']]})
        self.assertEqual(response.status_code, 422)

    def test_rank_displacement_preserves_identity_drafts_and_published_worker_content(self):
        for number in range(4):
            self.add(f'G1_ZONE_{number}')
        self.generate()
        rows = self.targets()['items']
        draft = self.create(rows)
        published = self.create(rows)
        response = self.publish(published)
        self.assertEqual(response.status_code, 200, response.text)
        published = response.json()
        with repo.get_connection() as conn:
            replace_user_zones(conn, 'worker01', ['G1_ZONE_3'])
        before = self.visible()
        self.assertEqual([r['bundle_id'] for r in before], [published['bundle_id']])
        self.assertEqual(before[0]['assigned_zones'], ['G1_ZONE_3'])
        self.assertNotIn('sources', before[0])
        single = self.client.post('/api/recommendations/' + rows[3]['recommendation_id'] + '/materials',
                                  headers=self.admin, json=CONTENT).json()
        single_publish = self.client.post('/api/education/materials/' + single['material_id'] + '/publish',
                                          headers=self.admin, json={'approved': True, 'expected_version': 0})
        self.assertEqual(single_publish.status_code, 200, single_publish.text)
        single_before = self.client.get('/api/worker/education', headers=self.worker).json()
        self.assertIn(CONTENT['body'], single_before['items'][0]['material'])
        for number in range(3):
            self.add(f'G1_OTHER_{number}', 'vest', 10)
        self.assertEqual({r['ppe_type'] for r in self.generate()['items']}, {'vest'})
        refreshed = [r for r in self.targets()['items'] if r['ppe_type'] == 'helmet']
        self.assertEqual([r['recommendation_id'] for r in refreshed], [r['recommendation_id'] for r in rows])
        self.assertTrue(all(r['recommendation_rank'] > 3 for r in refreshed))
        self.assertEqual(self.visible(), before)
        self.assertEqual(self.client.get('/api/worker/education', headers=self.worker).json(), single_before)
        for original in (draft, published):
            self.assertEqual(self.client.get(BASE + '/' + original['bundle_id'], headers=self.admin).json(), original)
        self.assertEqual(self.client.get('/api/education/materials/' + single['material_id'], headers=self.admin).json(), single_publish.json())
        # Rank alone is not part of the bundle snapshot; an unchanged draft can be published.
        self.assertEqual(self.publish(draft).status_code, 200)

    def test_stats_refresh_keeps_saved_content_but_rejects_stale_bundle_snapshot(self):
        for number in range(4):
            self.add(f'G1_ZONE_{number}')
        self.generate()
        rows = self.targets()['items']
        draft, published = self.create(rows), self.create(rows)
        self.assertEqual(self.publish(published).status_code, 200)
        with repo.get_connection() as conn:
            replace_user_zones(conn, 'worker01', ['G1_ZONE_3'])
        before = self.visible()
        self.add('G1_ZONE_3', count=5)
        response = self.client.post('/api/stats/generate', params={'quarter': QUARTER}, headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.targets()['items']), 4)
        self.assertEqual(self.visible(), before)
        response = self.publish(draft)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail'], 'education_bundle_selection_changed')
        self.assertEqual(self.client.get(BASE + '/' + draft['bundle_id'], headers=self.admin).json(), draft)

    def test_latest_quarter_and_topic_policy_use_all_targets_without_cross_zone_leak(self):
        for number in range(4):
            self.add(f'G1_ZONE_{number}')
        self.generate()
        bundle = self.create(self.targets()['items'])
        self.assertEqual(self.publish(bundle).status_code, 200)
        with repo.get_connection() as conn:
            replace_user_zones(conn, 'worker01', ['G1_ZONE_3'])
        self.assertEqual(len(self.visible()), 1)
        with patch.object(repo, '_get_education_topic', return_value='Changed guidance'):
            self.generate()
        self.assertEqual(self.visible(), [])
        self.generate()
        self.assertEqual(len(self.visible()), 1)
        for number in range(3):
            self.add(f'G1_OTHER_{number}', 'vest', 5, stamp='2026-04-05T00:00:00Z')
        self.add('G1_ZONE_3', stamp='2026-04-05T00:00:00Z')
        next_quarter = self.client.post('/api/recommendations/generate', params={'quarter': '2026-Q2'}, headers=self.admin)
        self.assertEqual(next_quarter.status_code, 200, next_quarter.text)
        self.assertTrue(all(r['zone_name'] != 'G1_ZONE_3' for r in next_quarter.json()['items']))
        # The assigned zone's real newer target advances its quarter even below rank 3.
        self.assertEqual(self.visible(), [])
        single = self.client.get('/api/worker/education', headers=self.worker).json()
        self.assertEqual(single['quarter'], '2026-Q2')
        self.assertEqual(len(single['items']), 1)
        self.assertEqual(single['items'][0]['zone_name'], 'G1_ZONE_3')
        self.assertIsNone(single['items'][0]['material'])
        self.assertNotIn('priority_score', single['items'][0])
        with repo.get_connection() as conn:
            replace_user_zones(conn, 'worker01', [])
        self.assertEqual(self.visible(), [])
        self.assertEqual(self.client.get('/api/worker/education', headers=self.worker).json()['groups'], [])

    def test_removal_keeps_bundle_record_and_hides_it_from_removed_zone_worker(self):
        removed = []
        for number in range(4):
            ids = self.add(f'G1_ZONE_{number}')
            if number == 3:
                removed = ids
        self.generate()
        bundle = self.create(self.targets()['items'])
        published = self.publish(bundle).json()
        with repo.get_connection() as conn:
            replace_user_zones(conn, 'worker01', ['G1_ZONE_3'])
            for identifier in removed:
                conn.execute("UPDATE candidate_event SET event_status='hold' WHERE event_id=?", (identifier,))
        self.generate()
        self.assertEqual(len(self.targets()['items']), 3)
        self.assertEqual(self.visible(), [])
        self.assertEqual(self.client.get(BASE + '/' + bundle['bundle_id'], headers=self.admin).json(), published)

    def test_cached_targets_read_is_admin_only_and_never_generates_or_backfills(self):
        url = BASE + '/targets'
        params = {'quarter': QUARTER}
        self.assertEqual(self.client.get(url, params=params).status_code, 401)
        self.assertEqual(self.client.get(url, params=params, headers=self.worker).status_code, 403)
        self.assertEqual(self.client.get(url, params={'quarter': 'bad'}, headers=self.admin).status_code, 400)
        for number in range(4):
            self.add(f'G1_ZONE_{number}')
        with patch.object(repo, '_replace_education_recommendations') as replace:
            self.assertEqual(self.targets(), {'quarter': QUARTER, 'generated_at': None, 'items': []})
            replace.assert_not_called()
        self.generate()
        with repo.get_connection() as conn:
            conn.execute('DELETE FROM education_recommendation WHERE quarter=? AND recommendation_rank>3', (QUARTER,))
        with patch.object(repo, '_replace_education_recommendations') as replace:
            self.assertEqual(len(self.targets()['items']), 3)
            replace.assert_not_called()
        self.generate()
        self.assertEqual(len(self.targets()['items']), 4)

    def test_unnamed_zone_is_retained_for_recommendations_but_not_bundle_selection(self):
        self.add('', count=10)
        for number in range(4):
            self.add(f'G1_ZONE_{number}')
        highlights = self.generate()['items']
        self.assertEqual(highlights[0]['zone_name'], '')
        targets = self.targets()['items']
        self.assertEqual(len(targets), 4)
        self.assertTrue(all(r['zone_name'] for r in targets))
        self.assertEqual(self.preview(targets)['confirmed_count_sum'], 4)
