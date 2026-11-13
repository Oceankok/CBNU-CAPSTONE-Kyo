import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from backend.auth.service import create_user
from backend.db.connection import begin_write

from fastapi import HTTPException
from backend.tests.support import isolated_api, login
from backend.db.event_repository import get_connection
from backend.zones.routes import replace_zone_members, ZoneMembersRequest


class ZoneMembersScopeTests(unittest.TestCase):
    def setUp(self):
        self.context = isolated_api()
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.admin = login(self.client)
        for zone in ('A', 'B'):
            self.assertEqual(self.client.put('/api/zones/' + zone, headers=self.admin,
                json={'required_ppe': ['helmet'], 'rules': []}).status_code, 200)
        self.assertEqual(self.client.post('/api/users', headers=self.admin, json={
            'user_id': 'worker02', 'password': 'test-password', 'display_name': 'Worker 2',
            'role': 'worker'}).status_code, 201)

    def put(self, ids, **kwargs):
        return self.client.put('/api/zones/A/members', headers=self.admin,
            json={'user_ids': ids, **kwargs})

    def ids(self, zone='A'):
        with get_connection() as conn:
            return sorted(row[0] for row in conn.execute(
                'SELECT user_id FROM user_zone WHERE zone_name=?', (zone,)))

    def test_stale_editor_rejected_and_legacy_request_unchanged(self):
        self.assertEqual(self.put(['worker01'], expected_user_ids=[]).status_code, 200)
        response = self.put(['worker02'], expected_user_ids=[])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail'], 'zone_members_changed')
        self.assertEqual(self.ids(), ['worker01'])
        # Existing clients remain backward compatible, not concurrency-protected.
        self.assertEqual(self.put(['worker02']).status_code, 200)
        self.assertEqual(self.ids(), ['worker02'])

    def test_user_patch_and_create_changes_are_seen_without_revision_bookkeeping(self):
        self.assertEqual(self.client.patch('/api/users/worker01', headers=self.admin,
            json={'zone_names': ['A', 'B']}).status_code, 200)
        self.assertEqual(self.put([], expected_user_ids=[]).status_code, 409)
        self.assertEqual(self.put([], expected_user_ids=['worker01']).status_code, 200)
        self.assertEqual(self.ids('B'), ['worker01'])
        self.assertEqual(self.client.post('/api/users', headers=self.admin, json={
            'user_id': 'new_worker', 'password': 'test-password', 'display_name': 'new',
            'role': 'worker', 'zone_names': ['A']}).status_code, 201)
        self.assertEqual(self.put(['worker02'], expected_user_ids=[]).status_code, 409)
        self.assertEqual(self.ids(), ['new_worker'])

    def test_order_normalization_inactive_members_and_retry(self):
        self.assertEqual(self.put(['worker01', 'worker02']).status_code, 200)
        self.assertEqual(self.client.patch('/api/users/worker02', headers=self.admin,
            json={'is_active': False}).status_code, 200)
        self.assertEqual(self.put(['worker01'], expected_user_ids=['worker01']).status_code, 409)
        self.assertEqual(self.put(['worker01'], expected_user_ids=[' worker02 ', 'worker01']).status_code, 200)
        # No implicit retry token: refetch after uncertain success, don't blindly replay.
        self.assertEqual(self.put(['worker01'], expected_user_ids=['worker01', 'worker02']).status_code, 409)
        self.assertEqual(self.put(['worker01'], expected_user_ids=['worker01']).status_code, 200)

    def test_invalid_expected_input_never_bypasses_guard(self):
        for value in (None, [''], ['worker01', ' worker01 '], ['x' * 101], ['x'] * 1001):
            response = self.put(['worker01'], expected_user_ids=value)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.ids(), [])
        self.assertEqual(self.put(['admin02'], expected_user_ids=[]).status_code, 422)
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.client.put('/api/zones/missing/members', headers=self.admin,
            json={'user_ids': [], 'expected_user_ids': []}).status_code, 404)

    def test_guard_is_admin_only(self):
        body = {'user_ids': [], 'expected_user_ids': []}
        self.assertEqual(self.client.put('/api/zones/A/members', json=body).status_code, 401)
        self.assertEqual(self.client.put('/api/zones/A/members', headers=login(self.client, 'worker01'),
            json=body).status_code, 403)

    def test_concurrent_same_snapshot_has_one_winner(self):
        barrier = Barrier(2)
        def write(user_id):
            barrier.wait(timeout=5)
            try:
                replace_zone_members('A', ZoneMembersRequest(user_ids=[user_id], expected_user_ids=[]))
                return 200
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ['worker01', 'worker02']))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(len(self.ids()), 1)

    def test_cli_create_joins_membership_write_lock(self):
        with patch('backend.auth.service.begin_write', wraps=begin_write) as lock:
            create_user('cli_worker', 'test-password', 'CLI', 'worker', 'A')
        lock.assert_called_once()
        self.assertEqual(self.put([], expected_user_ids=[]).status_code, 409)
        self.assertEqual(self.ids(), ['cli_worker'])
