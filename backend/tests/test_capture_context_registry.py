import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from backend.capture_context.registry import POLICY, prepare_registry, issue, retire, resolve_registered_context


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE camera_info(camera_id TEXT PRIMARY KEY,zone_name TEXT,status TEXT)')
        self.db.execute("INSERT INTO camera_info VALUES('cam','A','active')")
        self.now = datetime(2026,11,13,tzinfo=timezone.utc)
        prepare_registry(self.db, policy=POLICY)

    def tearDown(self): self.db.close()

    def start(self, session='s1', when=None):
        return issue(self.db,camera_id='cam',session_id=session,server_now=when or self.now,policy=POLICY)

    def stop(self,c,when=None,ack=True):
        return retire(self.db,context_id=c.context_id,camera_id='cam',session_id=c.session_id,
                      server_now=when or self.now+timedelta(seconds=10),trusted_stop_acknowledged=ack,policy=POLICY)

    def test_explicit_policy_and_schema_repeatability(self):
        with self.assertRaises(ValueError): prepare_registry(self.db,policy=None)
        c=self.start();prepare_registry(self.db,policy=POLICY)
        self.assertEqual(c,self.start())
        with self.assertRaises(ValueError): issue(self.db,camera_id='cam',session_id='s2',server_now=self.now,policy='other')

    def test_active_session_blocks_another(self):
        self.start()
        with self.assertRaisesRegex(ValueError,'must_be_retired'): self.start('s2')

    def test_reassignment_requires_retirement_and_new_context(self):
        c=self.start();self.db.execute("UPDATE camera_info SET zone_name='B'")
        with self.assertRaisesRegex(ValueError,'assignment_conflict'):self.start()
        self.stop(c);new=self.start('s2',self.now+timedelta(seconds=10))
        self.assertEqual((new.zone_name,new.assignment_version),('B',2))
        with self.assertRaisesRegex(ValueError,'cannot_restart'): self.start()

    def test_stop_requires_trusted_ack_and_binding(self):
        c=self.start()
        for ack in [False,None,1]:
            with self.assertRaises(ValueError):self.stop(c,ack=ack)
        with self.assertRaisesRegex(ValueError,'binding'):
            retire(self.db,context_id=c.context_id,camera_id='cam',session_id='wrong',server_now=self.now,
                   trusted_stop_acknowledged=True,policy=POLICY)
        self.assertIsNone(self.start().valid_until)

    def test_stop_retry_preserves_boundary(self):
        c=self.start();first=self.stop(c)
        self.assertEqual(first,self.stop(c,self.now+timedelta(hours=1)))
        with self.assertRaisesRegex(ValueError,'precedes'):self.start('s2',self.now+timedelta(seconds=9))

    def test_delayed_evidence_keeps_original_zone(self):
        c=self.start();self.stop(c);self.db.execute("UPDATE camera_info SET zone_name='B'")
        self.start('s2',self.now+timedelta(seconds=10))
        args=dict(context_id=c.context_id,camera_id='cam',session_id='s1',assignment_version=1,
                  capture_start=self.now+timedelta(seconds=2),capture_end=self.now+timedelta(seconds=8),
                  clock_verified=True,clock_error_seconds=1)
        result=resolve_registered_context(self.db,**args)
        self.assertEqual((result.status,result.zone_name),('supported','A'))
        self.assertEqual(resolve_registered_context(self.db,**(args|{'capture_end':self.now+timedelta(seconds=10)})).status,'unknown')
        self.assertEqual(resolve_registered_context(self.db,**(args|{'clock_verified':False})).status,'unknown')

    def test_invalid_camera_clock_and_zone(self):
        with self.assertRaises(ValueError): self.start(when=self.now.replace(tzinfo=None))
        self.db.execute("UPDATE camera_info SET zone_name=NULL")
        with self.assertRaises(ValueError):self.start()
        self.db.execute("UPDATE camera_info SET zone_name='A', status='inactive'")
        with self.assertRaises(ValueError):self.start()

    def test_foreign_session_and_missing_history_never_guess(self):
        c=self.start()
        args=dict(context_id=c.context_id,camera_id='cam',session_id='other',assignment_version=1,
                  capture_start=self.now,capture_end=self.now+timedelta(seconds=1),clock_verified=True,clock_error_seconds=0)
        self.assertEqual(resolve_registered_context(self.db,**args).status,'unknown')
        self.assertEqual(resolve_registered_context(self.db,**(args|{'context_id':'missing'})).status,'unknown')
