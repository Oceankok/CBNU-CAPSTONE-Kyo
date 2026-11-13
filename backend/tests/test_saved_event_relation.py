import unittest
from backend.tests import test_event_relation_preview as fixtures
from backend.db.event_repository import get_connection
from backend.db.connection import is_postgres
from backend.db.migrations import apply_migrations, LATEST_VERSION


class SavedEventRelationTests(unittest.TestCase):
    setUp = fixtures.EventRelationPreviewTests.setUp
    event = fixtures.EventRelationPreviewTests.event
    preview = fixtures.EventRelationPreviewTests.preview

    def pair(self, a, b):
        return self.client.get(f'/api/events/{a}/suspected-duplicates/evidence/{b}', headers=self.admin)

    def save(self, a, b, fp):
        return self.client.post(f'/api/events/{a}/saved-suspected-relations/{b}', headers=self.admin,
            json={'expected_fingerprint': fp})

    def release(self, ident, fp):
        return self.client.post(f'/api/events/saved-suspected-relations/{ident}/release', headers=self.admin,
            json={'expected_fingerprint': fp})

    def records(self, a):
        return self.client.get(f'/api/events/{a}/saved-suspected-relations', headers=self.admin)

    def test_save_release_idempotent_symmetric_and_preview_unaffected(self):
        a = self.event(); b = self.event('CAM_002')
        evidence = self.pair(a,b).json()
        self.assertEqual(evidence, self.pair(b,a).json())
        fp = evidence['fingerprint']
        before = self.preview(a).json()
        saved = self.save(a,b,fp).json()
        self.assertEqual(saved['state'], 'saved'); self.assertFalse(saved['stale'])
        self.assertEqual(saved, self.save(b,a,fp).json())
        self.assertEqual(self.records(a).json()['total'],1)
        released = self.release(saved['relation_id'],fp).json()
        self.assertEqual(released['state'],'released')
        self.assertEqual(released,self.release(saved['relation_id'],fp).json())
        self.assertEqual(released,self.save(a,b,fp).json()) # no implicit reactivation
        self.assertEqual(before,self.preview(a).json())
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_merge').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT event_status FROM candidate_event WHERE event_id=?',(a,)).fetchone()[0],'pending')

    def test_evidence_change_guard_stale_and_new_record(self):
        a=self.event(); b=self.event('CAM_002'); fp=self.pair(a,b).json()['fingerprint']
        old=self.save(a,b,fp).json()
        with get_connection() as conn:
            conn.execute("UPDATE candidate_event SET timestamp_end='2026-11-12T10:00:11+00:00' WHERE event_id=?",(a,))
        self.assertEqual(self.save(a,b,fp).status_code,409)
        self.assertTrue(self.records(a).json()['items'][0]['stale'])
        newer=self.pair(a,b).json()['fingerprint']; self.assertNotEqual(newer,fp)
        new=self.save(a,b,newer).json(); self.assertNotEqual(new['relation_id'],old['relation_id'])
        self.assertEqual(self.release(old['relation_id'],newer).status_code,409)
        self.assertEqual(self.release(old['relation_id'],fp).status_code,200)
        self.assertEqual(self.records(a).json()['total'],2)

    def test_merged_becomes_stale_no_auto_release(self):
        a=self.event(); b=self.event('CAM_002'); fp=self.pair(a,b).json()['fingerprint']
        self.save(a,b,fp).raise_for_status()
        with get_connection() as conn:
            conn.execute("INSERT INTO event_merge VALUES(?,?,'admin02','now','test','[]','[]')",(b,a))
        row=self.records(a).json()['items'][0]
        self.assertTrue(row['stale']); self.assertEqual(row['state'],'saved')
        self.assertEqual(self.save(a,b,fp).status_code,409)

    def test_event_deletion_cascades_relation_for_either_side(self):
        for delete_first in (True,False):
            a=self.event(); b=self.event('CAM_002'); fp=self.pair(a,b).json()['fingerprint']
            self.save(a,b,fp).raise_for_status()
            removed,remaining=sorted((a,b)) if delete_first else sorted((a,b),reverse=True)
            with get_connection() as conn:
                conn.execute('DELETE FROM candidate_event WHERE event_id=?',(removed,))
            self.assertEqual(self.records(remaining).json()['total'],0)

    def test_auth_validation_ineligible_and_pagination(self):
        from backend.tests.support import login
        a=self.event(); b=self.event('CAM_002'); fp=self.pair(a,b).json()['fingerprint']
        saved=self.save(a,b,fp).json()
        urls=[('get',f'/api/events/{a}/suspected-duplicates/evidence/{b}'),
              ('get',f'/api/events/{a}/saved-suspected-relations'),
              ('post',f'/api/events/{a}/saved-suspected-relations/{b}'),
              ('post',f"/api/events/saved-suspected-relations/{saved['relation_id']}/release")]
        for method,url in urls:
            kwargs={'json':{'expected_fingerprint':fp}} if method=='post' else {}
            self.assertEqual(getattr(self.client,method)(url,**kwargs).status_code,401)
            self.assertEqual(getattr(self.client,method)(url,headers=login(self.client,'worker01'),**kwargs).status_code,403)
        self.assertEqual(self.save(a,b,'bad').status_code,422)
        self.assertEqual(self.pair(a,a).status_code,422)
        self.assertEqual(self.pair(a,'missing').status_code,404)
        self.assertEqual(self.release('missing',fp).status_code,404)
        c=self.event('CAM_002',ppe='vest')
        self.assertFalse(self.pair(a,c).json()['eligible'])
        self.assertEqual(self.save(a,c,fp).status_code,409)
        self.assertEqual(self.client.get(f'/api/events/{a}/saved-suspected-relations?offset=10001',headers=self.admin).status_code,422)

    def test_migration_from_27_is_repeatable_no_implicit_saved_relations(self):
        a=self.event(); b=self.event('CAM_002')
        with get_connection() as conn:
            conn.execute('DROP TABLE saved_event_relation')
            conn.execute('UPDATE app_schema_version SET version=27 WHERE singleton=1' if is_postgres() else 'PRAGMA user_version=27')
            conn.commit()
            if not is_postgres():
                apply_migrations(conn); apply_migrations(conn)
        if is_postgres():
            from backend.db.postgres import initialize
            initialize(); initialize()
        self.assertEqual(self.records(a).json()['total'],0)
        self.assertTrue(self.pair(a,b).json()['eligible'])

    def test_save_rollback_and_group_change_not_live_reinterpreted(self):
        from backend.event_relations import persistent
        from backend.db.connection import run_stats_transaction
        a=self.event(); b=self.event('CAM_002'); fp=self.pair(a,b).json()['fingerprint']
        def fail(conn):
            persistent.save(conn,a,b,fp,'admin02')
            raise RuntimeError('test rollback')
        with self.assertRaises(RuntimeError): persistent.run_write(fail)
        self.assertEqual(self.records(a).json()['total'],0)
        self.save(a,b,fp).raise_for_status()
        self.client.delete('/api/camera-groups/'+self.group['group_id']+'?expected_version=0',headers=self.admin).raise_for_status()
        self.assertFalse(self.records(a).json()['items'][0]['stale'])

    def test_postgres_write_helper_locks_before_reading(self):
        from unittest.mock import patch
        from backend.event_relations.persistent import run_write
        calls=[]
        class Fake:
            dialect='postgresql'
            def __enter__(self): return self
            def __exit__(self,*args): calls.append('commit')
            def execute(self,sql): calls.append(sql)
        with patch('backend.db.connection.connect',return_value=Fake()):
            result=run_write(lambda conn: calls.append('evidence') or 42)
        self.assertEqual(result,42)
        self.assertEqual(calls[0],'SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        self.assertIn('pg_advisory_xact_lock',calls[1])
        self.assertEqual(calls[2:],['evidence','commit'])

    @unittest.skipUnless(__import__('os').environ.get('PPE_TEST_POSTGRES_URL'), 'Dedicated PostgreSQL test database required')
    def test_postgres_waiting_save_checks_committed_evidence(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from unittest.mock import patch
        from backend.db.connection import begin_write
        from backend.event_relations import persistent
        from fastapi import HTTPException
        a=self.event(); b=self.event('CAM_002'); fp=self.pair(a,b).json()['fingerprint']
        attempted=Event()
        def signal(conn):
            attempted.set()
            begin_write(conn)
        def save_waiting():
            return persistent.run_write(lambda conn:persistent.save(conn,a,b,fp,'admin02'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            with get_connection() as holder:
                begin_write(holder)
                holder.execute("UPDATE candidate_event SET timestamp_end='2026-11-12T10:00:11+00:00' WHERE event_id=?",(a,))
                with patch('backend.db.connection.begin_write',side_effect=signal):
                    future=pool.submit(save_waiting)
                    self.assertTrue(attempted.wait(5))
            with self.assertRaises(HTTPException) as raised:
                future.result(timeout=10)
            self.assertEqual(raised.exception.status_code,409)
        self.assertEqual(self.records(a).json()['total'],0)
