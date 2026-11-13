import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from backend.tests.support import isolated_api, login
from backend.db.event_repository import get_connection
from backend.db.init_db import init_database
from backend.zones.routes import ZoneRuleRequest, save_zone_rule


class ZoneRiskStorageTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)

    def put(self, zone='RISK', **extra):
        return self.client.put('/api/zones/'+zone, headers=self.admin,
            json={'required_ppe':['helmet'], 'rules':[], **extra})

    def risk(self):
        with get_connection() as conn:
            return conn.execute("SELECT risk_level FROM zone_rule WHERE zone_name='RISK'").fetchone()[0]

    def test_new_existing_omission_and_restart(self):
        self.assertIsNone(self.put().json()['risk_level'])
        for value in range(1,6):
            result=self.put(risk_level=value)
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json()['risk_level'],value)
            self.assertTrue(result.json()['risk_scoring_enabled'])
            self.assertEqual(self.put().json()['risk_level'],value)
        init_database()
        init_database()
        self.assertEqual(self.risk(),5)
        rows=self.client.get('/api/zones',headers=self.admin).json()['items']
        self.assertEqual(next(x for x in rows if x['zone_name']=='RISK')['risk_level'],5)

    def test_invalid_values_rejected_without_partial_update(self):
        self.put(risk_level=4)
        for value in (None,0,6,True,False,1.0,2.5,'3',[],{}):
            r=self.put(risk_level=value,rules=['must not save'])
            self.assertEqual(r.status_code,422,(value,r.text))
            self.assertEqual(self.risk(),4)
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT rules FROM zone_rule WHERE zone_name='RISK'").fetchone()[0],'[]')

    def test_permissions_and_worker_response_unchanged(self):
        self.put(risk_level=5)
        self.client.put('/api/zones/RISK/members',headers=self.admin,json={'user_ids':['worker01']})
        worker=login(self.client,'worker01')
        for headers,code in (({},401),(worker,403)):
            self.assertEqual(self.client.put('/api/zones/RISK',headers=headers,
                json={'required_ppe':[],'rules':[],'risk_level':2}).status_code,code)
        result=self.client.get('/api/worker/zones',headers=worker).json()['items'][0]
        self.assertNotIn('risk_level',result)
        self.assertNotIn('risk_scoring_enabled',result)

    def test_delete_recreate_does_not_infer_old_risk(self):
        self.put(risk_level=5)
        self.assertEqual(self.client.delete('/api/zones/RISK',headers=self.admin).status_code,200)
        self.assertIsNone(self.put().json()['risk_level'])

    def test_concurrent_omitted_update_preserves_explicit_value(self):
        self.put(risk_level=1)
        for _ in range(4):
            barrier=Barrier(2)
            def update(explicit):
                body=ZoneRuleRequest(required_ppe=['helmet'],rules=[],**({'risk_level':5} if explicit else {}))
                barrier.wait()
                return save_zone_rule('RISK',body,{'user_id':'admin02'})
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(update,[True,False]))
            self.assertEqual(self.risk(),5)
            self.put(risk_level=1)

    def test_storage_does_not_change_recommendation_cache_or_events(self):
        with get_connection() as conn:
            before_events=[dict(r) for r in conn.execute('SELECT * FROM candidate_event ORDER BY event_id')]
            before_cache=[dict(r) for r in conn.execute('SELECT * FROM education_recommendation ORDER BY recommendation_id')]
        self.put(risk_level=5)
        with get_connection() as conn:
            self.assertEqual(before_events,[dict(r) for r in conn.execute('SELECT * FROM candidate_event ORDER BY event_id')])
            self.assertEqual(before_cache,[dict(r) for r in conn.execute('SELECT * FROM education_recommendation ORDER BY recommendation_id')])

    def test_explicit_regeneration_uses_saved_risk_without_automatic_rebuild(self):
        from backend.db.event_repository import generate_education_recommendations
        with get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='confirmed'")
            stamp=conn.execute('SELECT timestamp_start FROM candidate_event ORDER BY event_id LIMIT 1').fetchone()[0]
        from datetime import datetime
        date=datetime.fromisoformat(stamp)
        quarter=f"{date.year}-Q{(date.month-1)//3+1}"
        before=generate_education_recommendations(quarter)['items']
        self.assertTrue(before)
        for row in before:
            if row['zone_name']:
                self.put(zone=row['zone_name'],risk_level=5)
        after=generate_education_recommendations(quarter)['items']
        def scores(rows):
            return {(r['zone_name'],r['ppe_type']):(r['priority_score'],r['score_breakdown']) for r in rows}
        self.assertNotEqual(scores(before),scores(after))
        for row in after:
            self.assertEqual(row['score_breakdown']['process_risk_weight'], 2.0 if row['zone_name'] else 1.0)

    def test_upgrade_v28_preserves_rows_and_is_replay_safe(self):
        self.put()
        with get_connection() as conn:
            before=dict(conn.execute("SELECT zone_name,required_ppe,rules,updated_at,updated_by FROM zone_rule WHERE zone_name='RISK'").fetchone())
            conn.execute('ALTER TABLE zone_rule DROP COLUMN risk_level')
            if getattr(conn,'dialect','sqlite')=='postgresql':
                conn.execute('UPDATE app_schema_version SET version=28 WHERE singleton=1')
            else:
                conn.execute('PRAGMA user_version=28')
        init_database();init_database()
        self.assertIsNone(self.risk())
        with get_connection() as conn:
            self.assertEqual(before,dict(conn.execute("SELECT zone_name,required_ppe,rules,updated_at,updated_by FROM zone_rule WHERE zone_name='RISK'").fetchone()))

    def test_version_replay_with_existing_column_preserves_value(self):
        self.put(risk_level=4)
        with get_connection() as conn:
            if getattr(conn,'dialect','sqlite')=='postgresql':
                conn.execute('UPDATE app_schema_version SET version=28 WHERE singleton=1')
            else:
                conn.execute('PRAGMA user_version=28')
        init_database()
        self.assertEqual(self.risk(),4)
