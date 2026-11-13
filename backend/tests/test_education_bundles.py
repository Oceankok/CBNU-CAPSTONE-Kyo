import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from backend.tests.support import isolated_api, login
from backend.db.event_repository import get_connection
from backend.zones.membership import replace_user_zones
from backend.education.models import MaterialContent

BASE = '/api/education/bundles'
CONTENT = {'title': '통합 안전모 교육', 'body': '작업 시작 전 안전모를 확인합니다.', 'checklist': ['턱끈 확인']}


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin, self.worker = login(self.client), login(self.client, 'worker01')
        with get_connection() as conn:
            conn.execute('DELETE FROM education_recommendation')
            conn.execute("INSERT INTO zone_rule(zone_name) VALUES('조립 구역') ON CONFLICT DO NOTHING")
            conn.execute("INSERT INTO quarterly_summary(quarter,created_at) VALUES('2026-Q3',CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING")
            for identifier, zone, count in [('A', '프레스 구역', 10), ('B', '자재 이동 구역', 3), ('C', '조립 구역', 7)]:
                conn.execute('''INSERT INTO education_recommendation(recommendation_id,quarter,recommendation_rank,ppe_type,
                    zone_name,education_topic,priority_score,confirmed_count,generated_at)
                    VALUES(?,'2026-Q3',1,'helmet',?,'안전모 착용 교육',99,?,CURRENT_TIMESTAMP)''', (identifier,zone,count))
            replace_user_zones(conn, 'worker01', ['프레스 구역'])

    def preview(self, ids=None):
        response = self.client.post(BASE + '/preview', headers=self.admin, json={'recommendation_ids': ids or ['A', 'B']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create(self, ids=None):
        ids = ids or ['A', 'B']
        response = self.client.post(BASE, headers=self.admin, json={**CONTENT, 'recommendation_ids': ids,
                                   'expected_snapshot': self.preview(ids)['snapshot']})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def publish(self, row):
        return self.client.post(BASE + '/' + row['bundle_id'] + '/publish', headers=self.admin,
                                json={'expected_version': row['version'], 'approved': True})

    def test_lifecycle_worker_scope_and_legacy_compatibility(self):
        row = self.create()
        self.assertEqual(self.client.get('/api/worker/education-bundles', headers=self.worker).json(), {'items': []})
        self.assertEqual(self.publish(row).status_code, 200)
        response = self.client.get('/api/worker/education-bundles', headers=self.worker)
        item = response.json()['items'][0]
        self.assertEqual(item['assigned_zones'], ['프레스 구역'])
        for private in ('confirmed_count', 'priority_score', 'sources', 'snapshot', 'created_by', '자재 이동 구역'):
            self.assertNotIn(private, response.text)
        self.assertEqual(item['body'], CONTENT['body'])
        self.assertEqual(self.client.get('/api/worker/education', headers=self.worker).status_code, 200)
        with get_connection() as conn:
            replace_user_zones(conn, 'worker01', ['조립 구역'])
        self.assertEqual(self.client.get('/api/worker/education-bundles', headers=self.worker).json()['items'], [])

    def test_selection_validation_and_admin_only(self):
        for ids in [['A'], ['A', 'A'], ['A', 'missing']]:
            self.assertIn(self.client.post(BASE + '/preview', headers=self.admin, json={'recommendation_ids': ids}).status_code, [409,422])
        self.assertEqual(self.client.post(BASE + '/preview', headers=self.worker, json={'recommendation_ids':['A','B']}).status_code,403)
        with get_connection() as conn:
            conn.execute("UPDATE education_recommendation SET ppe_type='gloves' WHERE recommendation_id='B'")
        self.assertEqual(self.client.post(BASE + '/preview', headers=self.admin, json={'recommendation_ids':['A','B']}).status_code,422)

    def test_snapshot_order_and_selected_statistics(self):
        first = self.preview()
        self.assertEqual(first['confirmed_count_sum'], 13)
        self.assertEqual(first['snapshot'], self.preview(['B','A'])['snapshot'])
        with get_connection() as conn:
            conn.execute("UPDATE education_recommendation SET confirmed_count=900 WHERE recommendation_id='C'")
        self.assertEqual(first['snapshot'], self.preview()['snapshot'])
        with get_connection() as conn:
            conn.execute("UPDATE education_recommendation SET confirmed_count=11 WHERE recommendation_id='A'")
        self.assertEqual(self.client.post(BASE, headers=self.admin, json={**CONTENT,'recommendation_ids':['A','B'],'expected_snapshot':first['snapshot']}).status_code,409)

    def test_refresh_preserves_snapshot_but_blocks_stale_publish(self):
        row = self.create()
        with get_connection() as conn:
            conn.execute("DELETE FROM education_recommendation WHERE recommendation_id='A'")
        self.assertEqual(self.publish(row).status_code,409)
        stored = self.client.get(BASE + '/' + row['bundle_id'], headers=self.admin).json()
        self.assertEqual(stored['sources'], row['sources'])

    def test_review_versions_and_exact_scope_supersession(self):
        a, b = self.create(), self.create()
        self.assertEqual(self.client.post(BASE+'/'+a['bundle_id']+'/publish',headers=self.admin,json={'expected_version':0,'approved':False}).status_code,400)
        self.assertEqual(self.publish(a).status_code,200)
        self.assertEqual(self.publish(b).status_code,200)
        self.assertEqual(self.client.get(BASE+'/'+a['bundle_id'],headers=self.admin).json()['status'],'superseded')
        self.assertEqual(self.client.patch(BASE+'/'+b['bundle_id'],headers=self.admin,json={**CONTENT,'expected_version':0}).status_code,409)
        self.assertEqual(self.client.post(BASE+'/'+b['bundle_id']+'/unpublish',headers=self.admin,json={'expected_version':1}).status_code,200)
        self.assertEqual(self.client.get('/api/worker/education-bundles',headers=self.worker).json()['items'],[])

    def test_generation_minimizes_payload_and_revalidates_after_call(self):
        preview = self.preview()
        with patch('backend.education.provider.settings',return_value=SimpleNamespace(provider='openai')), patch(
                'backend.education.provider.generate',return_value=(MaterialContent(**CONTENT),'test-model')) as generate:
            response = self.client.post(BASE+'/generate',headers=self.admin,json={'recommendation_ids':['A','B'],'expected_snapshot':preview['snapshot']})
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()['status'],'draft')
        self.assertEqual(generate.call_args.args[0],{'ppe_type':'helmet','education_topic':'안전모 착용 기준 및 착용 전 점검 절차 교육'})
        def changed(_):
            with get_connection() as conn:
                conn.execute("UPDATE education_recommendation SET confirmed_count=100 WHERE recommendation_id='A'")
            return MaterialContent(**CONTENT),'test-model'
        with patch('backend.education.provider.settings',return_value=SimpleNamespace(provider='openai')), patch('backend.education.provider.generate',side_effect=changed):
            response = self.client.post(BASE+'/generate',headers=self.admin,json={'recommendation_ids':['A','B'],'expected_snapshot':preview['snapshot']})
        self.assertEqual(response.status_code,409)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM education_bundle').fetchone()[0],1)

    def test_generated_recommendation_topics_with_zones_never_leave(self):
        from backend.db.event_repository import _get_education_topic
        with get_connection() as conn:
            for identifier, zone in [('A','프레스 구역'), ('B','자재 이동 구역')]:
                topic = _get_education_topic('helmet', zone)
                self.assertIn(zone, topic)
                conn.execute('UPDATE education_recommendation SET education_topic=? WHERE recommendation_id=?', (topic,identifier))
        selected = self.preview()
        with patch('backend.education.provider.settings',return_value=SimpleNamespace(provider='openai')), patch(
                'backend.education.provider.generate',return_value=(MaterialContent(**CONTENT),'test-model')) as generate:
            response = self.client.post(BASE+'/generate',headers=self.admin,
                json={'recommendation_ids':['A','B'],'expected_snapshot':selected['snapshot']})
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(generate.call_args.args[0], {'ppe_type':'helmet',
            'education_topic':'안전모 착용 기준 및 착용 전 점검 절차 교육'})
        self.assertEqual(response.json()['sources'], selected['sources'])

    def test_arbitrary_topic_and_ppe_cannot_leak_to_provider(self):
        with get_connection() as conn:
            conn.execute("UPDATE education_recommendation SET education_topic='비공개 사업장과 내부 수치 999' WHERE recommendation_id='A'")
        with patch('backend.education.provider.settings',return_value=SimpleNamespace(provider='openai')), patch(
                'backend.education.provider.generate',return_value=(MaterialContent(**CONTENT),'test-model')) as generate:
            response = self.client.post(BASE+'/generate',headers=self.admin,
                json={'recommendation_ids':['A','B'],'expected_snapshot':self.preview()['snapshot']})
        self.assertEqual(response.status_code,201)
        self.assertNotIn('비공개',str(generate.call_args))
        with get_connection() as conn:
            conn.execute("UPDATE education_recommendation SET ppe_type='private-site' WHERE recommendation_id IN ('A','B')")
        with patch('backend.education.provider.generate') as generate:
            response = self.client.post(BASE+'/generate',headers=self.admin,
                json={'recommendation_ids':['A','B'],'expected_snapshot':self.preview()['snapshot']})
        self.assertEqual(response.status_code,422)
        generate.assert_not_called()

    def test_rank_refresh_is_not_scope_change_and_new_quarter_hides_old_bundle(self):
        row = self.create()
        with get_connection() as conn:
            conn.execute("UPDATE education_recommendation SET recommendation_rank=8 WHERE recommendation_id='A'")
        self.assertEqual(self.publish(row).status_code, 200)
        with get_connection() as conn:
            conn.execute("INSERT INTO quarterly_summary(quarter,created_at) VALUES('2026-Q4',CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING")
            conn.execute("UPDATE education_recommendation SET quarter='2026-Q4' WHERE recommendation_id='A'")
        self.assertEqual(self.client.get('/api/worker/education-bundles', headers=self.worker).json()['items'], [])

    def test_edit_and_overlap_publish_are_independent(self):
        row = self.create()
        response = self.client.patch(BASE+'/'+row['bundle_id'], headers=self.admin,
                                     json={**CONTENT, 'title':'검토 완료', 'expected_version':0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['version'], 1)
        self.assertEqual(self.publish(response.json()).status_code, 200)
        other = self.create(['A','C'])
        self.assertEqual(self.publish(other).status_code, 200)
        self.assertEqual(len(self.client.get('/api/worker/education-bundles', headers=self.worker).json()['items']), 2)

    def test_postgres_refresh_waiter_cannot_approve_stale_snapshot(self):
        from backend.db import connection
        from backend.education_bundles import service
        from fastapi import HTTPException
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event, current_thread
        import time
        if not connection.is_postgres():
            self.skipTest('Requires isolated PPE_TEST_POSTGRES_URL database')
        main_thread = current_thread()
        for action in ('create', 'publish'):
            with self.subTest(action=action):
                row = self.create()
                selected = self.preview()
                entered = Event()
                pids = []
                original = connection.begin_write
                def observe(conn):
                    if current_thread() != main_thread:
                        pids.append(conn.execute('SELECT pg_backend_pid()').fetchone()[0])
                        entered.set()
                    return original(conn)
                def waiting_operation():
                    if action == 'create':
                        return service.create(['A','B'],selected['snapshot'],MaterialContent(**CONTENT),'admin02')
                    return service.change(row['bundle_id'],0,'admin02',action='publish')
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with get_connection() as writer:
                        original(writer)
                        writer.execute("UPDATE quarterly_summary SET created_at=created_at WHERE quarter='2026-Q3'")
                        writer.execute("UPDATE education_recommendation SET confirmed_count=confirmed_count+1 WHERE recommendation_id='A'")
                        with patch.object(connection,'begin_write',side_effect=observe):
                            future = pool.submit(waiting_operation)
                            self.assertTrue(entered.wait(5))
                            deadline = time.monotonic()+5
                            while time.monotonic() < deadline:
                                waiting = writer.execute('SELECT 1 FROM pg_locks WHERE pid=? AND NOT granted', (pids[0],)).fetchone()
                                if waiting:
                                    break
                                time.sleep(0.01)
                            self.assertTrue(waiting, 'Waiter must block after acquiring its old snapshot')
                            writer.commit()
                            with self.assertRaises(HTTPException) as error:
                                future.result(timeout=10)
                            self.assertEqual(error.exception.status_code,409)

    def test_postgres_publication_unique_race_returns_conflict(self):
        from backend.education_bundles import service
        from fastapi import HTTPException
        class UniqueRace(Exception):
            sqlstate = '23505'
        with patch.object(service, 'run_stats_transaction', side_effect=UniqueRace()):
            with self.assertRaises(HTTPException) as error:
                service.change('bundle',0,'admin',action='publish')
        self.assertEqual(error.exception.status_code,409)

    def test_migration_is_repeatable_and_does_not_remove_history(self):
        row = self.create()
        from backend.db.init_db import init_database
        init_database()
        self.assertEqual(self.client.get(BASE+'/'+row['bundle_id'],headers=self.admin).json()['sources'],row['sources'])


if __name__ == '__main__':
    unittest.main()
