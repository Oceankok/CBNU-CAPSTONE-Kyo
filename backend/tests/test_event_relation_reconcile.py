import unittest
from uuid import uuid4
from unittest.mock import patch
from backend.tests.support import isolated_api, login
from backend.db.event_repository import get_connection, insert_candidate_event


class RelationReconcileTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        self.client.post('/api/camera-groups', headers=self.admin,
            json={'name':'Overlap','camera_ids':['CAM_001','CAM_002']}).raise_for_status()

    def event(self, camera='CAM_001', ppe='helmet'):
        ident='EVT_'+uuid4().hex
        insert_candidate_event(dict(event_id=ident,camera_id=camera,tracking_id=None,ppe_type=ppe,
            timestamp_start='2026-11-12T10:00:00Z',timestamp_end='2026-11-12T10:00:10Z',
            duration_sec=10,frame_sample_count=1,thumbnail_path=None,video_clip_path=None,
            ai_confidence=.9,person_detected=1,ppe_detected=0,model_version='synthetic',event_status='pending'))
        return ident

    def scan(self, event, query=''):
        return self.client.post('/api/events/'+event+'/suspected-duplicates/reconcile-current'+query,headers=self.admin)

    def test_scan_creates_current_matches_once_without_merging_or_status_changes(self):
        a=self.event(); b=self.event('CAM_002'); self.event('CAM_002','vest')
        with get_connection() as conn:
            before=[dict(r) for r in conn.execute('SELECT * FROM candidate_event ORDER BY event_id')]
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM saved_event_relation').fetchone()[0],0)
        result=self.scan(a); result.raise_for_status(); data=result.json()
        self.assertEqual(data['created_count'],1); self.assertEqual(data['items'][0]['peer_event_id'],b)
        self.assertEqual(self.scan(a).json()['created_count'],0)
        self.assertEqual(self.scan(b).json()['created_count'],0)
        with get_connection() as conn:
            self.assertEqual(before,[dict(r) for r in conn.execute('SELECT * FROM candidate_event ORDER BY event_id')])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_merge').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM saved_event_relation').fetchone()[0],1)

    def test_released_pair_is_not_resaved_even_after_evidence_changes(self):
        a=self.event(); b=self.event('CAM_002'); self.scan(a).raise_for_status()
        row=self.client.get('/api/events/'+a+'/saved-suspected-relations',headers=self.admin).json()['items'][0]
        self.client.post('/api/events/saved-suspected-relations/'+row['relation_id']+'/release',headers=self.admin,
            json={'expected_fingerprint':row['fingerprint']}).raise_for_status()
        with get_connection() as conn:
            conn.execute("UPDATE candidate_event SET timestamp_end='2026-11-12T10:00:11+00:00' WHERE event_id=?",(b,))
        result=self.scan(a).json()
        self.assertEqual(result['created_count'],0)
        self.assertEqual(result['items'][0]['existing_state'],'released')
        listing=self.client.get('/api/events/'+a+'/saved-suspected-relations',headers=self.admin).json()['items']
        self.assertEqual(len(listing),1); self.assertTrue(listing[0]['stale']); self.assertEqual(listing[0]['state'],'released')
        # Existing explicit per-pair review can save changed evidence; the scan cannot.
        current=self.client.get('/api/events/'+a+'/suspected-duplicates/evidence/'+b,headers=self.admin).json()
        self.client.post('/api/events/'+a+'/saved-suspected-relations/'+b,headers=self.admin,
            json={'expected_fingerprint':current['fingerprint']}).raise_for_status()
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM saved_event_relation').fetchone()[0],2)

    def test_saved_stale_pair_stays_immutable(self):
        a=self.event(); b=self.event('CAM_002'); self.scan(a).raise_for_status()
        with get_connection() as conn:
            original=dict(conn.execute('SELECT * FROM saved_event_relation').fetchone())
            conn.execute("UPDATE candidate_event SET timestamp_end='2026-11-12T10:00:11+00:00' WHERE event_id=?",(b,))
        self.assertEqual(self.scan(a).json()['created_count'],0)
        with get_connection() as conn:
            self.assertEqual(dict(conn.execute('SELECT * FROM saved_event_relation').fetchone()),original)

    def test_page_is_bounded_and_retry_does_not_skip_unsaved_matches(self):
        a=self.event(); [self.event('CAM_002') for _ in range(3)]
        first=self.scan(a,'?limit=2').json(); self.assertEqual(first['created_count'],2); self.assertEqual(first['next_offset'],2)
        self.assertEqual(self.scan(a,'?limit=2').json()['created_count'],0)
        second=self.scan(a,'?limit=2&offset=2').json(); self.assertEqual(second['created_count'],1); self.assertFalse(second['has_more'])

    def test_transaction_rolls_back_partial_page(self):
        from backend.event_relations import persistent
        a=self.event(); self.event('CAM_002'); self.event('CAM_002')
        original=persistent.save; calls=[]
        def fail_second(*args,**kwargs):
            calls.append(1)
            if len(calls)==2: raise RuntimeError('test failure')
            return original(*args,**kwargs)
        with patch.object(persistent,'save',side_effect=fail_second):
            with self.assertRaises(RuntimeError): self.scan(a)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM saved_event_relation').fetchone()[0],0)

    def test_auth_limits_missing_and_no_snapshot(self):
        a=self.event(); path='/api/events/'+a+'/suspected-duplicates/reconcile-current'
        self.assertEqual(self.client.post(path).status_code,401)
        self.assertEqual(self.client.post(path,headers=login(self.client,'worker01')).status_code,403)
        for query in ('?limit=0','?limit=101','?offset=-1','?offset=10001'):
            self.assertEqual(self.scan(a,query).status_code,422)
        self.assertEqual(self.scan('missing').status_code,404)
        result=self.scan('EVT_0001').json()
        self.assertFalse(result['eligible']); self.assertEqual(result['created_count'],0)

    def test_concurrent_opposite_scans_create_one_pair(self):
        from concurrent.futures import ThreadPoolExecutor
        from backend.event_relations import persistent
        a=self.event(); b=self.event('CAM_002')
        def scan(ident):
            return persistent.run_write(lambda conn: persistent.reconcile(conn,ident,'admin02'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(scan,[a,b]))
        self.assertEqual(sum(r['created_count'] for r in results),1)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM saved_event_relation').fetchone()[0],1)
