import os
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.db import upgrade
from backend.db.connection import DatabaseError, connect, schema_version
from backend.db.migrations import LATEST_VERSION
from backend.tests.support import isolated_api, login


class UpgradeGuards(unittest.TestCase):
    def test_sqlite_and_unpaused_apply_are_rejected(self):
        with patch.dict(os.environ, {'PPE_DATABASE_URL': ''}):
            with self.assertRaisesRegex(ValueError, 'PostgreSQL'):
                upgrade.inspect(10, LATEST_VERSION)
        with self.assertRaisesRegex(ValueError, 'writers-paused'):
            upgrade.apply(10, LATEST_VERSION, writers_paused=False)


@unittest.skipUnless(os.environ.get('PPE_TEST_POSTGRES_URL'), 'Dedicated PostgreSQL upgrade test required')
class PostgresUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        with connect() as conn:
            for table in ('event_merge', 'education_material', 'event_consent', 'consent_record', 'camera_group_member', 'camera_group'):
                conn.execute(f'DROP TABLE {table}')
            conn.execute('ALTER TABLE event_clip_request DROP COLUMN role')
            conn.execute('UPDATE app_schema_version SET version=10 WHERE singleton=1')

    def test_preflight_read_only_and_upgrade_preserves_existing_rows(self):
        plan = upgrade.inspect(10, LATEST_VERSION)
        with connect() as conn:
            self.assertEqual(schema_version(conn), 10)
            self.assertIsNone(conn.execute("SELECT to_regclass('consent_record')").fetchone()[0])
        result = upgrade.apply(10, LATEST_VERSION, writers_paused=True)
        self.assertTrue(result['rows_preserved'])
        self.assertEqual(result['table_counts'], plan['table_counts'])
        self.assertEqual(self.client.get('/api/auth/me', headers=self.admin).status_code, 200)
        with connect() as conn:
            self.assertEqual(schema_version(conn), LATEST_VERSION)

    def test_wrong_versions_and_pending_worker_block_before_mutation(self):
        for source, target in ((9, LATEST_VERSION), (11, LATEST_VERSION), (10, LATEST_VERSION+1)):
            with self.assertRaises(ValueError):
                upgrade.inspect(source, target)
        with connect() as conn:
            conn.execute("INSERT INTO event_media(media_id,event_id,camera_id,kind,role,status,created_at) VALUES('upgrade-media','EVT_0001','CAM_001','image','reference','processing',CURRENT_TIMESTAMP)")
            conn.execute("INSERT INTO processing_job(job_id,media_id,generation,status,redaction_mode,kind,created_at,updated_at) VALUES('upgrade-job','upgrade-media',1,'queued','scrfd','image',0,0)")
        with self.assertRaisesRegex(ValueError, 'Drain processing'):
            upgrade.apply(10, LATEST_VERSION, writers_paused=True)
        with connect() as conn:
            self.assertEqual(schema_version(conn), 10)

    def test_schema_failure_rolls_back_whole_postgres_upgrade(self):
        original = Path.read_text

        def read(path, *args, **kwargs):
            return 'INTENTIONALLY INVALID SQL' if path.parent.name == 'event_merge' else original(path, *args, **kwargs)

        with patch.object(Path, 'read_text', new=read):
            with self.assertRaises(DatabaseError):
                upgrade.apply(10, LATEST_VERSION, writers_paused=True)
        with connect() as conn:
            self.assertEqual(schema_version(conn), 10)
            self.assertIsNone(conn.execute("SELECT to_regclass('consent_record')").fetchone()[0])
        self.assertEqual(self.client.get('/api/auth/me', headers=self.admin).status_code, 200)
