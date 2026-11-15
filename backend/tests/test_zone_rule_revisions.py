"""Real authenticated API and storage races for zone rule preconditions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier, Event, get_ident
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from backend.auth.service import create_user
from backend.db.event_repository import get_connection
from backend.db.init_db import init_database
from backend.tests.support import isolated_api, login
from backend.zones import routes
from backend.zones.revision import revision


class ZoneRuleRevisionTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        create_user('admin03', 'test-password', 'Admin B', 'admin')
        self.other = login(self.client, 'admin03')
        self.path = '/api/zones/CAS'
        self.content = {'required_ppe': ['helmet'], 'rules': ['Original'], 'risk_level': 1}
        response = self.client.put(self.path, headers=self.admin, json={**self.content, 'expected_revision': None})
        self.assertEqual(response.status_code, 200, response.text)
        self.original = response.json()

    def current(self):
        return next((z for z in self.client.get('/api/zones', headers=self.admin).json()['items'] if z['zone_name'] == 'CAS'), None)

    def put(self, expected, headers=None, **changes):
        return self.client.put(self.path, headers=headers or self.admin,
            json={**self.content, **changes, 'expected_revision': expected})

    def delete(self, expected, headers=None):
        return self.client.delete(self.path, headers=headers or self.admin, params={'expected_revision': expected})

    def test_two_admins_stale_save_preserves_all_winner_fields_and_cached_data(self):
        snapshots = [next(z for z in self.client.get('/api/zones', headers=h).json()['items'] if z['zone_name'] == 'CAS') for h in (self.admin, self.other)]
        self.assertEqual(snapshots[0], snapshots[1])
        with get_connection() as conn:
            before = {t: [dict(r) for r in conn.execute('SELECT * FROM '+t)] for t in ('candidate_event', 'education_recommendation')}
        first = self.put(snapshots[0]['revision'], required_ppe=['vest'], rules=['Admin A'], risk_level=5)
        self.assertEqual(first.status_code, 200, first.text)
        stale = self.put(snapshots[1]['revision'], self.other, required_ppe=['goggles'], rules=['Stale B'], risk_level=2)
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()['detail'], 'zone_rule_changed')
        current = self.current()
        for field in ('required_ppe', 'rules', 'risk_level', 'revision', 'updated_at'):
            self.assertEqual(current[field], first.json()[field])
        with get_connection() as conn:
            self.assertEqual(before, {t: [dict(r) for r in conn.execute('SELECT * FROM '+t)] for t in before})

    def test_missing_precondition_is_rejected_and_invalid_tokens_never_write(self):
        self.assertEqual(self.client.put(self.path, headers=self.admin, json=self.content).status_code, 428)
        self.assertEqual(self.client.delete(self.path, headers=self.admin).status_code, 428)
        for token in ('', 'x'*64, 7, True, [], {}):
            response = self.put(token, rules=['Must not save'])
            self.assertEqual(response.status_code, 422, (token, response.text))
        self.assertEqual(self.current()['revision'], self.original['revision'])
        self.assertEqual(self.put(None).status_code, 409)

    def test_schema35_without_zone_revision_column_fails_before_seed_or_reset(self):
        tables = ('zone_rule', 'user_zone', 'candidate_event', 'education_translation', 'auto_decision_settings')
        with get_connection() as conn:
            conn.execute('ALTER TABLE zone_rule DROP COLUMN revision_nonce')
            before = {t: [dict(r) for r in conn.execute('SELECT * FROM '+t)] for t in tables}
        with self.assertRaisesRegex(RuntimeError, 'Incompatible schema35 lineage.*explicit migration'):
            init_database()
        with get_connection() as conn:
            self.assertEqual(before, {t: [dict(r) for r in conn.execute('SELECT * FROM '+t)] for t in tables})

    def test_concurrent_same_revision_has_one_complete_winner(self):
        barrier = Barrier(2)
        def write(value):
            headers, ppe, rules, risk = value
            barrier.wait(timeout=5)
            response = self.put(self.original['revision'], headers, required_ppe=ppe, rules=rules, risk_level=risk)
            return response.status_code, (ppe, rules, risk)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(write, [(self.admin, ['vest'], ['A'], 5), (self.other, ['goggles'], ['B'], 2)]))
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        winner = next(value for status, value in results if status == 200)
        current = self.current()
        self.assertEqual((current['required_ppe'], current['rules'], current['risk_level']), winner)

    def test_concurrent_create_only_has_one_winner(self):
        barrier = Barrier(2)
        def create(value):
            barrier.wait(timeout=5)
            response = self.client.put('/api/zones/NEW_CAS', headers=self.admin,
                json={'required_ppe': [], 'rules': [value], 'expected_revision': None})
            return response.status_code
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(create, ['A', 'B'])), [200, 409])

    def test_stale_delete_and_save_after_delete_or_recreation_do_not_erase(self):
        changed = self.put(self.original['revision'], rules=['New value']).json()
        self.assertEqual(self.delete(self.original['revision']).status_code, 409)
        self.assertEqual(self.current()['rules'], ['New value'])
        self.assertEqual(self.delete(changed['revision']).status_code, 200)
        self.assertEqual(self.put(changed['revision']).status_code, 409)
        self.assertIsNone(self.current())
        recreated = self.put(None).json()
        self.assertNotEqual(recreated['revision'], self.original['revision'])
        self.assertEqual(self.put(self.original['revision']).status_code, 409)
        self.assertEqual(self.delete(changed['revision']).status_code, 409)

    def test_concurrent_edit_delete_has_one_winner(self):
        barrier = Barrier(2)
        def mutate(delete):
            barrier.wait(timeout=5)
            return (self.delete(self.original['revision']) if delete else self.put(self.original['revision'], rules=['Changed'])).status_code
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(mutate, [True, False])), [200, 409])

    def test_concurrent_membership_and_delete_share_transaction_lock(self):
        barrier = Barrier(2)
        def mutate(member):
            barrier.wait(timeout=5)
            response = self.client.put(self.path+'/members', headers=self.admin,
                json={'user_ids': ['worker01'], 'expected_user_ids': []}) if member else self.delete(self.original['revision'])
            return member, response.status_code
        with ThreadPoolExecutor(2) as pool:
            results = dict(pool.map(mutate, [True, False]))
        self.assertIn(results, ({True: 200, False: 409}, {True: 404, False: 200}))
        with get_connection() as conn:
            members = conn.execute("SELECT user_id FROM user_zone WHERE zone_name='CAS'").fetchall()
            exists = conn.execute("SELECT 1 FROM zone_rule WHERE zone_name='CAS'").fetchone() is not None
        self.assertEqual(exists, results[True] == 200)
        self.assertEqual(len(members), 1 if exists else 0)

    def test_api_aba_with_identical_second_timestamp_has_distinct_revisions(self):
        with patch.object(routes, 'datetime') as clock:
            clock.now.return_value = datetime.fromisoformat(self.original['updated_at'])
            away = self.put(self.original['revision'], required_ppe=['vest'], rules=['Away'], risk_level=5).json()
            back = self.put(away['revision']).json()
        self.assertEqual(back['updated_at'], self.original['updated_at'])
        self.assertEqual(back['rules'], self.original['rules'])
        self.assertEqual(len({self.original['revision'], away['revision'], back['revision']}), 3)
        self.assertEqual(self.put(self.original['revision']).status_code, 409)

    def test_direct_writer_field_changes_invalidate_revision_without_nonce_bookkeeping(self):
        with get_connection() as conn:
            conn.execute("UPDATE zone_rule SET required_ppe='[\"vest\"]',rules='[\"External\"]',risk_level=4 WHERE zone_name='CAS'")
        self.assertNotEqual(self.current()['revision'], self.original['revision'])
        self.assertEqual(self.put(self.original['revision']).status_code, 409)
        self.assertEqual(self.delete(self.original['revision']).status_code, 409)

    def test_omitted_risk_preserved_on_current_revision_and_stale_omission_rejected(self):
        changed = self.put(self.original['revision'], risk_level=5).json()
        body = {'required_ppe': ['vest'], 'rules': ['Rules only'], 'expected_revision': changed['revision']}
        response = self.client.put(self.path, headers=self.admin, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['risk_level'], 5)
        body['expected_revision'] = self.original['revision']
        self.assertEqual(self.client.put(self.path, headers=self.other, json=body).status_code, 409)

    def test_unset_risk_snapshot_can_be_updated_and_deleted(self):
        path = '/api/zones/NULL_CAS'
        body = {'required_ppe': [], 'rules': [], 'expected_revision': None}
        created = self.client.put(path, headers=self.admin, json=body)
        self.assertEqual(created.status_code, 200, created.text)
        self.assertIsNone(created.json()['risk_level'])
        body.update(expected_revision=created.json()['revision'], rules=['Still unset'])
        updated = self.client.put(path, headers=self.admin, json=body)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertIsNone(updated.json()['risk_level'])
        self.assertEqual(self.client.delete(path, headers=self.admin,
            params={'expected_revision': updated.json()['revision']}).status_code, 200)

    def test_delete_refusals_and_worker_projection_are_preserved(self):
        self.client.put(self.path+'/members', headers=self.admin, json={'user_ids': ['worker01'], 'expected_user_ids': []}).raise_for_status()
        response = self.delete(self.original['revision'])
        self.assertEqual(response.status_code, 409)
        self.assertIn('assigned', response.json()['detail'])
        worker = login(self.client, 'worker01')
        result = self.client.get('/api/worker/zones', headers=worker).json()['items'][0]
        self.assertNotIn('revision', result)
        for headers, status in (({}, 401), (worker, 403)):
            self.assertEqual(self.client.put(self.path, headers=headers, json={**self.content, 'expected_revision': self.original['revision']}).status_code, status)
            self.assertEqual(self.delete(self.original['revision'], headers).status_code if headers else self.client.delete(self.path).status_code, status)

    @unittest.skipUnless(os.environ.get('PPE_TEST_POSTGRES_URL'), 'Requires isolated PostgreSQL')
    def test_uncoordinated_postgres_writer_between_read_and_mutation_is_rejected(self):
        for deleting in (False, True):
            with self.subTest(delete=deleting):
                with get_connection() as conn:
                    snapshot = conn.execute("SELECT * FROM zone_rule WHERE zone_name='CAS'").fetchone()
                expected = revision(snapshot)
                read, changed = Event(), Event()
                original_find = routes._find
                owner = []
                def find(conn, name):
                    row = original_find(conn, name)
                    if name == 'CAS' and get_ident() == owner[0] and not read.is_set():
                        read.set()
                        self.assertTrue(changed.wait(5))
                    return row
                def mutate():
                    owner.append(get_ident())
                    try:
                        if deleting:
                            routes.remove_zone_rule('CAS', expected)
                        else:
                            routes.save_zone_rule('CAS', routes.ZoneRuleRequest(**self.content, expected_revision=expected), {'user_id': 'admin02'})
                        return 200
                    except HTTPException as exc:
                        return exc.status_code
                with patch.object(routes, '_find', side_effect=find), ThreadPoolExecutor(1) as pool:
                    future = pool.submit(mutate)
                    self.assertTrue(read.wait(5))
                    with get_connection() as conn:
                        conn.execute("UPDATE zone_rule SET rules=? WHERE zone_name='CAS'", ('["External '+str(deleting)+'"]',))
                    changed.set()
                    self.assertEqual(future.result(timeout=10), 409)
                self.assertEqual(self.current()['rules'], ['External '+str(deleting)])
