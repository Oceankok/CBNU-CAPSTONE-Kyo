"""Concurrent inference hints and runtime metadata with isolated databases."""
import os
import time
import unittest
from unittest.mock import patch

from backend.db.connection import is_postgres, schema_version
from backend.db.event_repository import get_connection
from backend.db.init_db import init_database
from backend.db.migrations import LATEST_VERSION
from backend.tests.support import isolated_api, login


class InferenceSessionTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        self.node = self.register('inference')

    def register(self, ident, enabled=True):
        token = self.client.post('/api/nodes', headers=self.admin, json={'node_id': ident, 'name': ident}).json()['token']
        if enabled:
            self.client.put('/api/processing/workers/'+ident, headers=self.admin, json={'enabled': True}).raise_for_status()
        return {'Authorization': 'Bearer '+token}

    def signal(self, session, busy, node=None):
        body = {'busy': busy, 'ttl_seconds': 30}
        if session is not None:
            body['session_id'] = session
        return self.client.put('/api/processing/inference-load', headers=node or self.node, json=body)

    def busy(self, node=None):
        return self.client.get('/api/processing/inference-load', headers=node or self.node).json()['busy']

    def test_camera_release_and_legacy_release_do_not_clear_other_camera(self):
        self.signal('a'*32, True).raise_for_status()
        self.signal('b'*32, True).raise_for_status()
        self.signal('a'*32, False).raise_for_status()
        self.assertTrue(self.busy())
        self.signal(None, False).raise_for_status()
        self.assertTrue(self.busy())
        self.signal('b'*32, False).raise_for_status()
        self.assertFalse(self.busy())

    def test_same_session_id_is_scoped_by_authenticated_node(self):
        other = self.register('other')
        self.signal('a'*32, True).raise_for_status()
        self.signal('a'*32, True, other).raise_for_status()
        self.signal('a'*32, False).raise_for_status()
        self.assertTrue(self.busy())
        self.signal('a'*32, False, other).raise_for_status()
        self.assertFalse(self.busy())

    def test_expired_sessions_are_ignored_and_removed_on_next_signal(self):
        self.signal('a'*32, True).raise_for_status()
        with get_connection() as conn:
            conn.execute('UPDATE processing_inference_session SET busy_until=?', (time.time()-1,))
        self.assertFalse(self.busy())
        self.signal('b'*32, True).raise_for_status()
        with get_connection() as conn:
            rows = conn.execute('SELECT session_id FROM processing_inference_session').fetchall()
            self.assertEqual([row[0] for row in rows], ['b'*32])

    def test_disabled_processor_is_not_counted_as_active_inference(self):
        other = self.register('observer')
        self.signal('a'*32, True).raise_for_status()
        self.client.put('/api/processing/workers/inference', headers=self.admin, json={'enabled': False}).raise_for_status()
        self.assertFalse(self.busy(other))

    def test_session_validation_and_capacity_do_not_break_refresh(self):
        self.assertEqual(self.signal('invalid', True).status_code, 422)
        for index in range(32):
            self.signal(f'{index:032x}', True).raise_for_status()
        self.assertEqual(self.signal('f'*32, True).status_code, 429)
        self.signal('0'*32, True).raise_for_status()
        self.signal('0'*32, False).raise_for_status()
        self.signal('f'*32, True).raise_for_status()
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM processing_inference_session').fetchone()[0], 32)

    def test_v15_upgrade_preserves_legacy_signal_and_accounts(self):
        self.signal(None, True).raise_for_status()
        with get_connection() as conn:
            conn.execute('DROP TABLE processing_inference_session')
            conn.execute('UPDATE app_schema_version SET version=15 WHERE singleton=1' if is_postgres() else 'PRAGMA user_version=15')
            users = conn.execute('SELECT COUNT(*) FROM app_user').fetchone()[0]
        init_database()
        init_database()
        self.assertTrue(self.busy())
        with get_connection() as conn:
            self.assertEqual(schema_version(conn), LATEST_VERSION)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM app_user').fetchone()[0], users)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM processing_inference_session').fetchone()[0], 0)

    def test_runtime_version_is_processor_only_and_reports_baked_revision(self):
        url = '/api/processing/runtime-version'
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.admin).status_code, 401)
        ordinary = self.register('field-only', enabled=False)
        self.assertEqual(self.client.get(url, headers=ordinary).status_code, 403)
        with patch.dict(os.environ, {'PPE_BUILD_REVISION': 'd'*40}):
            result = self.client.get(url, headers=self.node).json()
        self.assertEqual(result['revision'], 'd'*40)
        self.assertEqual(result['database_schema_version'], result['required_schema_version'])
        self.assertNotIn('token', result)
