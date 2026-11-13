import unittest
from uuid import uuid4
from unittest.mock import patch
from backend.tests.support import isolated_api, login
from backend.db import event_repository as repo
from backend.education.identity import recommendation_id


class RecommendationSemanticIdentityTests(unittest.TestCase):
    def setUp(self):
        self.client=self.enterContext(isolated_api());self.admin=login(self.client)
        with repo.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='pending'")
            conn.execute("INSERT INTO camera_info(camera_id,camera_location,zone_name,status) VALUES('IDENTITY_CAM','Identity','ID_ZONE','active')")
        self.content={'title':'Draft','body':'Scope-specific body','checklist':['Check']}

    def add(self,ppe,count):
        ids=[]
        with repo.get_connection() as conn:
            for _ in range(count):
                ident='EVT_'+uuid4().hex;ids.append(ident)
                conn.execute('''INSERT INTO candidate_event(event_id,camera_id,ppe_type,event_status,timestamp_start,timestamp_end)
                    VALUES(?,'IDENTITY_CAM',?,'confirmed','2026-04-15 10:00:00','2026-04-15 10:00:03')''',(ident,ppe))
        return ids

    def generate(self):
        return repo.generate_education_recommendations('2026-Q2')['items']

    def material(self,ident,**extra):
        return self.client.post('/api/recommendations/'+ident+'/materials',headers=self.admin,json={**self.content,**extra})

    def scope(self,row):
        return {**{k:row[k] for k in ('recommendation_id','zone_name','ppe_type','education_topic')},'quarter':'2026-Q2'}

    def test_rank_swap_keeps_identity_and_old_cache_without_guard_targets_original_scope(self):
        self.add('helmet',3);self.add('vest',2)
        before={r['ppe_type']:r for r in self.generate()}
        self.add('vest',4)
        after={r['ppe_type']:r for r in self.generate()}
        self.assertNotEqual(before['helmet']['recommendation_rank'],after['helmet']['recommendation_rank'])
        for ppe in before:self.assertEqual(before[ppe]['recommendation_id'],after[ppe]['recommendation_id'])
        saved=self.material(before['helmet']['recommendation_id'])
        self.assertEqual(saved.status_code,201,saved.text)
        self.assertEqual(saved.json()['ppe_type'],'helmet')

    def test_legacy_id_retires_without_alias_and_material_survives(self):
        self.add('helmet',3);current=self.generate()[0]
        legacy='EDU_2026Q2_01'
        with repo.get_connection() as conn:
            conn.execute('UPDATE education_recommendation SET recommendation_id=? WHERE recommendation_id=?',(legacy,current['recommendation_id']))
        old={**current,'recommendation_id':legacy}
        material=self.material(legacy).json();mid=material['material_id']
        # Existing stored legacy row is unchanged merely by reads.
        self.assertEqual(repo.get_education_recommendations('2026-Q2')['items'][0]['recommendation_id'],legacy)
        new=self.generate()[0]
        self.assertNotEqual(legacy,new['recommendation_id'])
        self.assertEqual(self.material(legacy).status_code,404)
        self.assertEqual(self.material(legacy,expected_scope=self.scope(old)).status_code,409)
        fetched=self.client.get('/api/education/materials/'+mid,headers=self.admin).json()
        self.assertEqual(fetched,material)
        listed=self.client.get('/api/recommendations/'+new['recommendation_id']+'/materials',headers=self.admin).json()['items']
        self.assertIn(mid,[m['material_id'] for m in listed])
        published=self.client.post('/api/education/materials/'+mid+'/publish',headers=self.admin,json={'approved':True,'expected_version':0})
        self.assertEqual(published.status_code,200,published.text)

    def test_disappearance_and_reappearance_restore_same_semantic_id(self):
        ids=self.add('helmet',2);first=self.generate()[0]['recommendation_id']
        with repo.get_connection() as conn:
            for ident in ids:conn.execute("UPDATE candidate_event SET event_status='hold' WHERE event_id=?",(ident,))
        self.assertEqual(self.generate(),[]);self.assertEqual(self.material(first).status_code,404)
        with repo.get_connection() as conn:
            for ident in ids:conn.execute("UPDATE candidate_event SET event_status='confirmed' WHERE event_id=?",(ident,))
        self.assertEqual(self.generate()[0]['recommendation_id'],first)

    def test_top_three_displacement_does_not_reassign_evicted_id(self):
        self.add('helmet',3);first=self.generate()[0]['recommendation_id']
        for ppe in ('vest','gloves','safety_shoes'):self.add(ppe,10)
        self.assertNotIn(first,[r['recommendation_id'] for r in self.generate()])
        self.assertEqual(self.material(first).status_code,404)
        self.add('helmet',20)
        self.assertIn(first,[r['recommendation_id'] for r in self.generate()])

    def test_topic_change_changes_identity_and_blocks_old_material_publish(self):
        self.add('helmet',3);old=self.generate()[0];material=self.material(old['recommendation_id']).json()
        with patch.object(repo,'_get_education_topic',return_value='New topic'):
            new=self.generate()[0]
        self.assertNotEqual(old['recommendation_id'],new['recommendation_id'])
        self.assertEqual(self.material(old['recommendation_id']).status_code,404)
        r=self.client.post('/api/education/materials/'+material['material_id']+'/publish',headers=self.admin,json={'approved':True,'expected_version':0})
        self.assertEqual(r.status_code,409)

    def test_generator_inflight_scope_guard_still_rejects_target_disappearance(self):
        from backend.education import service, provider
        from backend.education.models import MaterialContent
        self.add('helmet',3);old=self.generate()[0]
        def generate(scope):
            with patch.object(repo,'_get_education_topic',return_value='Changed during generation'):self.generate()
            return MaterialContent(**self.content),'mock'
        with patch.object(provider,'settings') as settings,patch.object(provider,'generate',side_effect=generate):
            settings.return_value.provider='mock'
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as caught:service.generate_draft(old['recommendation_id'],'admin02')
        self.assertEqual(caught.exception.status_code,409)

    def test_exact_components_and_delimiters_have_distinct_identity(self):
        base=['2026-Q2','A','helmet','Topic']
        result=recommendation_id(*base)
        self.assertTrue(result.startswith('EDU_S1_'));self.assertEqual(len(result),71)
        for i in range(4):
            changed=base.copy();changed[i]+=' '
            self.assertNotEqual(result,recommendation_id(*changed))
        self.assertNotEqual(recommendation_id('x','a|b','c','d'),recommendation_id('x','a','b|c','d'))
        self.assertNotEqual(recommendation_id('2026-Q2','구역A','helmet','Topic'),result)

    def test_reinitialization_never_resurrects_retired_legacy_rank_ids(self):
        from backend.db.init_db import init_database
        self.add('helmet',3);new=self.generate()[0]
        legacy='EDU_2026Q2_01'
        with repo.get_connection() as conn:
            conn.execute('UPDATE education_recommendation SET recommendation_id=? WHERE recommendation_id=?',(legacy,new['recommendation_id']))
        self.generate()
        init_database();init_database()
        self.assertEqual(self.material(legacy).status_code,404)
        with repo.get_connection() as conn:
            rows=conn.execute('SELECT recommendation_id,quarter,zone_name,ppe_type,education_topic FROM education_recommendation').fetchall()
        for row in rows:
            self.assertEqual(row['recommendation_id'],recommendation_id(row['quarter'],row['zone_name'],row['ppe_type'],row['education_topic']))

    def test_legacy_rows_and_empty_cache_are_preserved_on_reinitialization(self):
        from backend.db.init_db import init_database
        self.add('helmet',3);current=self.generate()[0]
        with repo.get_connection() as conn:
            conn.execute("UPDATE education_recommendation SET recommendation_id='EDU_2026Q2_01' WHERE recommendation_id=?",(current['recommendation_id'],))
            before=[dict(r) for r in conn.execute('SELECT * FROM education_recommendation ORDER BY recommendation_id')]
        init_database()
        with repo.get_connection() as conn:
            self.assertEqual(before,[dict(r) for r in conn.execute('SELECT * FROM education_recommendation ORDER BY recommendation_id')])
            conn.execute('DELETE FROM education_recommendation')
        init_database()
        with repo.get_connection() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM education_recommendation').fetchone()[0],0)

    def test_postgres_recommendation_seed_runs_only_on_fresh_seeded_schema(self):
        from backend.tests.test_postgres_initialization_order import PostgresInitializationOrderTests
        from backend.db import postgres
        from pathlib import Path
        statement=(Path(postgres.__file__).parent/'postgres_education_seed.sql').read_text()
        harness=PostgresInitializationOrderTests()
        for version,seed in ((None,True),(None,False),(28,True),(29,True)):
            calls=harness.run_initialization(version,seed=seed)
            self.assertEqual(('sql',statement,None) in calls, version is None and seed)
