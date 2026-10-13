import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.db.event_repository import get_connection
from backend.db.seed_demo import seed_demo
from backend.tests.support import isolated_api, login


class DemoSeedTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for name, value in [('backend.media.paths.PROJECT_ROOT',root), ('backend.media.paths.STORAGE_ROOT',root/'storage'),
                            ('backend.db.seed_demo.PROCESSED_ROOT', root/'storage/candidate_events/processed'),
                            ('backend.api.main._STORAGE_DIR',root/'storage')]:
            scope = patch(name, value)
            scope.start()
            self.addCleanup(scope.stop)
        context = isolated_api()
        self.client = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def test_default_development_target_is_rejected(self):
        with patch.dict(os.environ, {'PPE_DATABASE_URL':'', 'PPE_DB_PATH':'backend/db/ppe_system.db'}):
            with self.assertRaises(ValueError):
                seed_demo('test-password')

    def test_demo_states_media_workers_and_reviews_without_gpu(self):
        self.assertEqual(seed_demo('test-password',trusted_fixture=True)['events'], 12)
        admin = login(self.client,'DEMO_admin')
        rows = self.client.get('/api/events',headers=admin).json()['items']
        demos = {row['event_id']:row for row in rows if row['event_id'].startswith('DEMO_')}
        self.assertEqual(len(demos),12)
        body = {'review_result':'confirmed','review_reason_code':'demo_confirmed','review_comment':'DEMO 검증'}
        self.assertEqual(self.client.post('/api/events/DEMO_EVT_01/review',headers=admin,json=body).status_code,200)
        body['review_result']='false_positive'
        body['review_reason_code']='demo_false_positive'
        self.assertEqual(self.client.post('/api/events/DEMO_EVT_02/review',headers=admin,json=body).status_code,200)
        media = self.client.get('/api/events/DEMO_EVT_03/media',headers=admin).json()['items']
        self.assertEqual(len({row['camera_id'] for row in media}),2)
        worker = login(self.client,'DEMO_worker01')
        self.assertEqual(len(self.client.get('/api/worker/zones',headers=worker).json()['items']),2)
        self.assertEqual(demos['DEMO_EVT_08']['event_status'],'unreviewable')

    def test_reset_restores_demo_without_removing_other_events(self):
        seed_demo('test-password',trusted_fixture=True)
        old_login = login(self.client,'DEMO_admin')
        seed_demo('test-password',reset=True,trusted_fixture=True)
        self.assertEqual(self.client.get('/api/auth/me',headers=old_login).status_code,401)
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidate_event WHERE SUBSTR(event_id,1,5)='DEMO_'").fetchone()[0],12)
            self.assertIsNotNone(conn.execute("SELECT 1 FROM candidate_event WHERE event_id='EVT_0001'").fetchone())
