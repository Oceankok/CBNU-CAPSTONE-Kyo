import unittest

from backend.db.connection import is_postgres, schema_version
from backend.db.event_repository import get_connection
from backend.db.migrations import LATEST_VERSION
from backend.tests.support import isolated_api, login
from backend.zones.membership import replace_user_zones


class EducationMaterialTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        self.worker = login(self.client, 'worker01')
        with get_connection() as conn:
            conn.execute('DELETE FROM education_recommendation')
            for quarter in ('2026-Q2', '2026-Q3'):
                conn.execute('INSERT INTO quarterly_summary(quarter,created_at) VALUES(?,CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING', (quarter,))
            replace_user_zones(conn, 'worker01', ['프레스 구역'])
            for ident, quarter, zone in [('REC_OLD','2026-Q2','프레스 구역'), ('REC_CURRENT','2026-Q3','프레스 구역'), ('REC_OTHER','2026-Q3','자재 이동 구역')]:
                self.insert_recommendation(conn, ident, quarter, zone)

    @staticmethod
    def insert_recommendation(conn, ident, quarter, zone):
        conn.execute('''INSERT INTO education_recommendation(recommendation_id,quarter,recommendation_rank,ppe_type,
            zone_name,education_topic,priority_score,confirmed_count,generated_at) VALUES(?,?,1,'helmet',?,'안전모 착용 교육',99,42,CURRENT_TIMESTAMP)''',
            (ident, quarter, zone))

    def draft(self, recommendation='REC_CURRENT', **changes):
        body = {'title': '안전모 점검', 'body': '작업 전 안전모의 상태와 착용을 확인합니다.', 'checklist': ['안전모 외관 확인', '올바른 착용 확인']}
        body.update(changes)
        return self.client.post('/api/recommendations/'+recommendation+'/materials', headers=self.admin, json=body)

    def publish(self, material_id, version=0, approved=True):
        return self.client.post('/api/education/materials/'+material_id+'/publish', headers=self.admin,
                                json={'expected_version': version, 'approved': approved})

    def test_draft_is_hidden_until_admin_reviews_and_publishes(self):
        response = self.draft()
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()
        self.assertEqual(row['created_by'], 'admin02')
        self.assertEqual(row['provider'], 'manual')
        worker = self.client.get('/api/worker/education', headers=self.worker).json()
        self.assertIsNone(worker['items'][0]['material'])
        self.assertEqual(self.publish(row['material_id'], approved=False).status_code, 400)
        self.assertEqual(self.publish(row['material_id'], version=88).status_code, 409)
        saved = self.publish(row['material_id'])
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['published_by'], 'admin02')
        material = self.client.get('/api/worker/education', headers=self.worker).json()['items'][0]['material']
        self.assertIsInstance(material, str)
        self.assertIn(row['title'], material)
        self.assertIn(row['body'], material)
        self.assertIn(row['checklist'][0], material)
        self.assertNotIn('admin02', material)

    def test_stats_regeneration_preserves_all_material_states_and_versions(self):
        previous = self.draft(title='이전 게시본').json()
        self.assertEqual(self.publish(previous['material_id']).status_code, 200)
        published = self.draft(title='현재 게시본').json()
        self.assertEqual(self.publish(published['material_id']).status_code, 200)
        draft = self.draft(title='작성 중 초안').json()
        edited = self.client.patch('/api/education/materials/'+draft['material_id'],
                                  headers=self.admin, json={
                                      'title': '수정 중인 초안', 'body': '보존할 본문',
                                      'checklist': ['보존할 점검 항목'], 'expected_version': 0})
        self.assertEqual(edited.status_code, 200, edited.text)
        other_quarter = self.draft('REC_OLD', title='이전 분기 자료').json()
        self.assertEqual(self.publish(other_quarter['material_id']).status_code, 200)
        with get_connection() as conn:
            before = [dict(row) for row in conn.execute(
                'SELECT * FROM education_material ORDER BY material_id')]
            conn.execute("UPDATE quarterly_summary SET candidate_count=999999 WHERE quarter='2026-Q3'")
        self.assertEqual({row['status'] for row in before}, {'draft', 'published', 'superseded'})
        self.assertEqual(edited.json()['version'], 1)

        # The real stats endpoint must update its cache without deleting the
        # quarter parent and cascading into durable education content.
        for _ in range(2):
            response = self.client.post('/api/stats/generate?quarter=2026-Q3', headers=self.admin)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotEqual(response.json()['summary']['candidate_count'], 999999)
            with get_connection() as conn:
                after = [dict(row) for row in conn.execute(
                    'SELECT * FROM education_material ORDER BY material_id')]
            self.assertEqual(after, before)
            for row in before:
                fetched = self.client.get('/api/education/materials/'+row['material_id'], headers=self.admin)
                self.assertEqual(fetched.status_code, 200, fetched.text)
                self.assertEqual(fetched.json()['version'], row['version'])

    def test_stats_first_generation_and_repeat_create_only_one_summary(self):
        for _ in range(2):
            response = self.client.post('/api/stats/generate?quarter=2099-Q1', headers=self.admin)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['summary']['candidate_count'], 0)
            with get_connection() as conn:
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM quarterly_summary WHERE quarter='2099-Q1'").fetchone()[0], 1)

    def test_draft_edit_versions_and_immutable_published_content(self):
        row = self.draft().json()
        url = '/api/education/materials/'+row['material_id']
        changed = {'title': '수정 제목', 'body': '검토한 안전교육 본문', 'checklist': ['확인 사항'], 'expected_version': 0}
        result = self.client.patch(url, headers=self.admin, json=changed)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['version'], 1)
        self.assertEqual(self.client.patch(url, headers=self.admin, json=changed).status_code, 409)
        self.assertEqual(self.publish(row['material_id'], version=1).status_code, 200)
        changed['expected_version'] = 2
        self.assertEqual(self.client.patch(url, headers=self.admin, json=changed).status_code, 409)

    def test_new_draft_keeps_previous_publication_until_new_approval(self):
        first = self.draft().json()
        self.assertEqual(self.publish(first['material_id']).status_code, 200)
        second = self.draft(title='새 초안').json()
        def visible():
            return self.client.get('/api/worker/education', headers=self.worker).json()['items'][0]['material']
        self.assertTrue(visible().startswith(first['title']))
        self.assertEqual(self.publish(second['material_id']).status_code, 200)
        self.assertTrue(visible().startswith(second['title']))
        previous = self.client.get('/api/education/materials/'+first['material_id'], headers=self.admin).json()
        self.assertEqual(previous['status'], 'superseded')
        self.assertIsNotNone(previous['published_at'])

    def test_other_zone_old_quarter_and_reused_rank_ids_never_leak_content(self):
        for recommendation in ('REC_OLD', 'REC_OTHER'):
            row = self.draft(recommendation, title='교육_'+recommendation).json()
            self.assertEqual(self.publish(row['material_id']).status_code, 200)
        self.assertIsNone(self.client.get('/api/worker/education', headers=self.worker).json()['items'][0]['material'])
        row = self.draft(title='기존 프레스 구역 전용 본문').json()
        self.assertEqual(self.publish(row['material_id']).status_code, 200)
        with get_connection() as conn:
            conn.execute("DELETE FROM education_recommendation WHERE recommendation_id='REC_CURRENT'")
            self.insert_recommendation(conn, 'REC_CURRENT', '2026-Q3', '자재 이동 구역')
        # Recommendation IDs encode rank and can be reused for another zone.
        with get_connection() as conn:
            replace_user_zones(conn, 'worker01', ['자재 이동 구역'])
        body = self.client.get('/api/worker/education', headers=self.worker).json()
        for item in body['items']:
            self.assertNotIn(row['title'], item['material'])

    def test_changed_topic_blocks_stale_draft_and_hides_stale_publication(self):
        published = self.draft().json()
        self.assertEqual(self.publish(published['material_id']).status_code, 200)
        draft = self.draft().json()
        with get_connection() as conn:
            conn.execute("UPDATE education_recommendation SET education_topic='새 작업 안전 지침' WHERE recommendation_id='REC_CURRENT'")
        self.assertEqual(self.publish(draft['material_id']).status_code, 409)
        self.assertIsNone(self.client.get('/api/worker/education', headers=self.worker).json()['items'][0]['material'])

    def test_admin_roles_and_invalid_content(self):
        row = self.draft().json()
        url = '/api/education/materials/'+row['material_id']
        for path in (url, '/api/recommendations/REC_CURRENT/materials'):
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers=self.worker).status_code, 403)
        self.assertEqual(self.client.post(url+'/publish', headers=self.worker, json={'expected_version': 0, 'approved': True}).status_code, 403)
        for changes in ({'title': '   '}, {'body': '   '}, {'checklist': []}, {'checklist': [' ']}, {'created_by': 'spoofed-admin'}):
            self.assertEqual(self.draft(**changes).status_code, 422)
        self.assertEqual(self.draft('MISSING').status_code, 404)

    def test_unpublish_hides_material_preserves_metadata_and_requires_new_approval(self):
        row = self.draft().json()
        published = self.publish(row['material_id']).json()
        url = '/api/education/materials/'+row['material_id']
        result = self.client.post(url+'/unpublish', headers=self.admin,
                                  json={'expected_version': published['version']})
        self.assertEqual(result.status_code, 200, result.text)
        withdrawn = result.json()
        self.assertEqual(withdrawn['status'], 'draft')
        self.assertEqual(withdrawn['version'], published['version']+1)
        self.assertEqual(withdrawn['published_by'], published['published_by'])
        self.assertEqual(withdrawn['published_at'], published['published_at'])
        self.assertEqual(withdrawn['updated_by'], 'admin02')
        self.assertIsNone(self.client.get('/api/worker/education', headers=self.worker).json()['items'][0]['material'])
        changed = {'title': '정정된 교육', 'body': '정정한 안전 지침', 'checklist': ['정정 사항 확인'],
                   'expected_version': withdrawn['version']}
        edited = self.client.patch(url, headers=self.admin, json=changed)
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertIsNone(self.client.get('/api/worker/education', headers=self.worker).json()['items'][0]['material'])
        self.assertEqual(self.publish(row['material_id'], version=edited.json()['version']).status_code, 200)
        self.assertIn('정정된 교육', self.client.get('/api/worker/education', headers=self.worker).json()['items'][0]['material'])

    def test_unpublish_requires_admin_current_version_and_published_status(self):
        row = self.draft().json()
        url = '/api/education/materials/'+row['material_id']+'/unpublish'
        body = {'expected_version': row['version']}
        self.assertEqual(self.client.post(url, json=body).status_code, 401)
        self.assertEqual(self.client.post(url, headers=self.worker, json=body).status_code, 403)
        self.assertEqual(self.client.post(url, headers=self.admin, json=body).status_code, 409)
        published = self.publish(row['material_id']).json()
        self.assertEqual(self.client.post(url, headers=self.admin, json=body).status_code, 409)
        self.assertEqual(self.client.get('/api/education/materials/'+row['material_id'], headers=self.admin).json()['status'], 'published')
        body['expected_version'] = published['version']
        self.assertEqual(self.client.post(url, headers=self.admin, json={'expected_version': -1}).status_code, 422)
        self.assertEqual(self.client.post(url, headers=self.admin, json={**body, 'updated_by': 'spoofed'}).status_code, 422)
        self.assertEqual(self.client.post('/api/education/materials/MISSING/unpublish', headers=self.admin, json=body).status_code, 404)
        self.assertEqual(self.client.post(url, headers=self.admin, json=body).status_code, 200)
        body['expected_version'] += 1
        self.assertEqual(self.client.post(url, headers=self.admin, json=body).status_code, 409)

    def test_unpublish_does_not_restore_superseded_material(self):
        first = self.draft().json()
        self.publish(first['material_id'])
        second = self.draft(title='정정 전 교육').json()
        published = self.publish(second['material_id']).json()
        url = '/api/education/materials/'
        previous = self.client.get(url+first['material_id'], headers=self.admin).json()
        self.assertEqual(self.client.post(url+first['material_id']+'/unpublish', headers=self.admin,
                                        json={'expected_version': previous['version']}).status_code, 409)
        self.assertEqual(self.client.post(url+second['material_id']+'/unpublish', headers=self.admin,
                                        json={'expected_version': published['version']}).status_code, 200)
        self.assertIsNone(self.client.get('/api/worker/education', headers=self.worker).json()['items'][0]['material'])
        self.assertEqual(self.client.get(url+first['material_id'], headers=self.admin).json()['status'], 'superseded')

    def test_v11_upgrade_preserves_consent_and_existing_accounts(self):
        from backend.db.init_db import init_database
        with get_connection() as conn:
            users = [dict(row) for row in conn.execute('SELECT * FROM app_user ORDER BY user_id').fetchall()]
            consents = [dict(row) for row in conn.execute('SELECT * FROM consent_record').fetchall()]
            # Recreate the pre-v12 education boundary, not a modern parent table
            # with later bundle/material tables still present. These synthetic tables must
            # be empty before removing them; never use CASCADE to hide dependencies.
            for table in ('education_bundle', 'education_material'):
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
                conn.execute('DROP TABLE ' + table)
            conn.execute('UPDATE app_schema_version SET version=11 WHERE singleton=1' if is_postgres() else 'PRAGMA user_version=11')
        init_database()
        init_database()
        with get_connection() as conn:
            self.assertEqual(schema_version(conn), LATEST_VERSION)
            self.assertEqual([dict(row) for row in conn.execute('SELECT * FROM app_user ORDER BY user_id').fetchall()], users)
            self.assertEqual([dict(row) for row in conn.execute('SELECT * FROM consent_record').fetchall()], consents)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM education_bundle').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM education_material').fetchone()[0], 0)
