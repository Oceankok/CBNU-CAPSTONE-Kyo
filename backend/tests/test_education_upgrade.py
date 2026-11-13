"""Synthetic v31/v32 upgrades through the real initializer, SQLite or isolated PG."""
from tempfile import TemporaryDirectory
import unittest

from backend.db.connection import is_postgres, schema_version
from backend.db.event_repository import get_connection
from backend.db.init_db import init_database
from backend.tests.support import database_environment


class EducationUpgradeTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory(prefix="education-upgrade-"))
        self.enterContext(database_environment(directory))
        init_database()
        with get_connection() as conn:
            conn.execute("INSERT INTO app_user(user_id,password_hash,display_name,role) VALUES('UPGRADE_USER','synthetic-not-a-login','Synthetic upgrade user','worker')")
            conn.execute("""INSERT INTO consent_record(record_id,site_code,scope_description,form_version,
                file_key,sha256,byte_size,page_count,confirmed_by,created_at)
                VALUES('UPGRADE_CONSENT','synthetic-site','Synthetic fixture','test-v1',
                'synthetic/no-file.pdf','synthetic-hash',1,1,'test','2026-01-01')""")
            conn.execute("INSERT INTO quarterly_summary(quarter,created_at) VALUES('2099-Q1','2026-01-01') ON CONFLICT DO NOTHING")
            conn.execute("""INSERT INTO education_material(material_id,recommendation_id,quarter,zone_name,
                ppe_type,education_topic,title,body,checklist_json,status,created_by,created_at,
                updated_by,updated_at,published_by,published_at,version)
                VALUES('UPGRADE_MATERIAL','synthetic-rec','2099-Q1','synthetic-zone','helmet',
                'Synthetic topic','Preserve original title','Preserve original body','["Check"]',
                'published','test','2026-01-01','test','2026-01-02','test','2026-01-02',7)""")

    @staticmethod
    def _snapshot(conn, tables):
        # Tables are fixed test constants, never external input.
        return {table: sorted((dict(row) for row in conn.execute('SELECT * FROM ' + table)),
                              key=lambda row: repr(sorted(row.items()))) for table in tables}

    @staticmethod
    def _version(conn, version):
        if is_postgres():
            conn.execute('UPDATE app_schema_version SET version=? WHERE singleton=1', (version,))
        else:
            conn.execute('PRAGMA user_version=' + str(version))

    def _assert_repeated_upgrade(self, before):
        for _ in range(2):
            init_database()
            with get_connection() as conn:
                self.assertEqual(schema_version(conn), 33)
                self.assertEqual(self._snapshot(conn, before), before)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM education_translation').fetchone()[0], 0)
                # A SELECT also proves the v32 table was created on the v31 path.
                conn.execute('SELECT bundle_id FROM education_bundle').fetchall()

    def test_version31_runs_bundle_and_translation_migrations_without_data_loss(self):
        with get_connection() as conn:
            # Only new, empty tables are removed to model a populated v31 DB.
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM education_translation').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM education_bundle').fetchone()[0], 0)
            conn.execute('DROP TABLE education_translation')
            conn.execute('DROP TABLE education_bundle')
            self._version(conn, 31)
            self.assertEqual(schema_version(conn), 31)
            before = self._snapshot(conn, ('app_user', 'consent_record', 'education_material'))
        # No manual execution of v32 SQL: real init must perform both migrations.
        self._assert_repeated_upgrade(before)

    def test_version32_preserves_existing_bundle_and_material_on_translation_upgrade(self):
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM education_translation').fetchone()[0], 0)
            conn.execute('DROP TABLE education_translation')
            conn.execute("""INSERT INTO education_bundle(bundle_id,quarter,ppe_type,scope_key,sources_json,
                title,body,checklist_json,status,created_by,created_at,updated_by,updated_at,
                published_by,published_at,version)
                VALUES('UPGRADE_BUNDLE','2099-Q1','helmet','synthetic-scope','[]',
                'Preserve bundle','Synthetic body','["Check"]','published','test','2026-01-01',
                'test','2026-01-02','test','2026-01-02',4)""")
            self._version(conn, 32)
            self.assertEqual(schema_version(conn), 32)
            before = self._snapshot(conn, ('app_user', 'consent_record', 'education_material', 'education_bundle'))
        self._assert_repeated_upgrade(before)


if __name__ == '__main__':
    unittest.main()
