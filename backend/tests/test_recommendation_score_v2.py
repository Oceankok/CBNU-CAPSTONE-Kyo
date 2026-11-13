import unittest
from datetime import date
from uuid import uuid4
from backend.db import event_repository as repository
from backend.db.recommendation_score import monday, period_week_count, risk_multiplier, score
from backend.db.init_db import init_database
from backend.tests.support import isolated_api, login


class ScoreCalendarTests(unittest.TestCase):
    def test_monday_keys_year_boundary_partial_weeks_and_full_quarter(self):
        self.assertEqual(monday(date(2026,1,1)),date(2025,12,29))
        self.assertEqual(monday(date(2026,1,4)),date(2025,12,29))
        self.assertEqual(monday(date(2026,1,5)),date(2026,1,5))
        self.assertEqual(period_week_count('2025-12-31','2026-01-02'),1)
        self.assertEqual(period_week_count('2026-01-04','2026-01-06'),2)
        self.assertEqual(period_week_count('2026-01-05','2026-01-12'),1)
        self.assertEqual(period_week_count('2026-01-01','2026-04-01'),14)
        with self.assertRaises(ValueError):period_week_count('2026-01-01','2026-01-01')

    def test_mapping_and_occurrence_ratio(self):
        self.assertEqual([risk_multiplier(i) for i in (None,1,2,3,4,5)],[1,1,1.25,1.5,1.75,2])
        for value in (True,False,0,6,1.0,'3'):
            with self.assertRaises(ValueError):risk_multiplier(value)
        self.assertEqual(score(20,7,14,3),33)
        self.assertEqual(score(0,0,14,None),0)
        for active,total in ((15,14),(1,0),(-1,14)):
            with self.assertRaises(ValueError):score(20,active,total,1)


class RecommendationScoreV2Tests(unittest.TestCase):
    def setUp(self):
        self.client=self.enterContext(isolated_api())
        self.admin=login(self.client)
        with repository.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='pending'")
            for zone in ('A','B'):
                conn.execute("INSERT INTO camera_info(camera_id,camera_location,zone_name,status) VALUES(?,?,?,'active')",('SCORE_'+zone,zone,zone))

    def set_risk(self,zone,level):
        result=self.client.put('/api/zones/'+zone,headers=self.admin,json={'required_ppe':[],'rules':[],'risk_level':level})
        self.assertEqual(result.status_code,200,result.text)

    def add(self,stamp,zone='A',ppe='helmet',status='confirmed'):
        with repository.get_connection() as conn:
            conn.execute('INSERT INTO candidate_event(event_id,camera_id,ppe_type,event_status,timestamp_start,timestamp_end) VALUES(?,?,?,?,?,?)',
                ('EVT_'+uuid4().hex,'SCORE_'+zone,ppe,status,stamp,stamp))

    def generate(self):
        return repository.generate_education_recommendations('2026-Q1')['items']

    def test_sql_kst_week_keys_and_quarter_boundaries(self):
        for stamp in ('2025-12-31T15:00:00Z','2026-01-04T14:59:59Z','2026-01-04T15:00:00Z','2026-03-31T14:59:59Z'):
            self.add(stamp)
        self.add('2025-12-31T14:59:59Z')
        self.add('2026-03-31T15:00:00Z')
        self.add('2026-02-01T00:00:00Z',status='false_positive')
        row=self.generate()[0];b=row['score_breakdown']
        self.assertEqual(b['confirmed_count'],4)
        self.assertEqual(b['repeat_weeks'],3)
        self.assertEqual(b['period_weeks'],14)
        self.assertEqual(b['occurrence_week_ratio'],3/14)
        self.assertFalse(b['zone_concentration_used_in_score'])
        self.assertEqual(row['priority_score'],round(4*(1+.2*3/14),2))
        self.assertEqual(row['score_version'],'zone_ppe_v2')

    def test_risk_change_only_on_explicit_rebuild_and_frozen_event_zone(self):
        self.add('2026-01-05T00:00:00Z')
        before=self.generate()
        self.set_risk('A',5)
        self.assertEqual(repository.get_education_recommendations('2026-Q1')['items'],before)
        with repository.get_connection() as conn:
            conn.execute("UPDATE camera_info SET zone_name='B' WHERE camera_id='SCORE_A'")
        self.set_risk('B',1)
        after=self.generate()[0]
        self.assertEqual(after['zone_name'],'A')
        self.assertEqual(after['score_breakdown']['process_risk_weight'],2)
        self.assertEqual(after['priority_score'],round(2*(1+.2/14),2))

    def test_zone_share_stays_descriptive_not_score_factor(self):
        self.add('2026-01-05T00:00:00Z')
        before=self.generate()[0]
        for _ in range(20):self.add('2026-01-05T00:00:00Z',ppe='vest')
        after=next(r for r in self.generate() if r['ppe_type']=='helmet')
        self.assertEqual(after['priority_score'],before['priority_score'])
        self.assertNotEqual(after['score_breakdown']['zone_concentration'],before['score_breakdown']['zone_concentration'])

    def test_migration_preserves_legacy_cache_without_recalculation(self):
        self.add('2026-01-05T00:00:00Z');self.generate()
        with repository.get_connection() as conn:
            conn.execute("UPDATE education_recommendation SET priority_score=123.45")
            conn.execute('ALTER TABLE education_recommendation DROP COLUMN score_version')
            if getattr(conn,'dialect','sqlite')=='postgresql':
                conn.execute('UPDATE app_schema_version SET version=29 WHERE singleton=1')
            else:conn.execute('PRAGMA user_version=29')
        init_database();init_database()
        row=repository.get_education_recommendations('2026-Q1')['items'][0]
        self.assertEqual(row['priority_score'],123.45)
        self.assertEqual(row['score_version'],'legacy_unversioned')
        self.assertNotIn('period_weeks',row['score_breakdown'])
        self.assertEqual(self.generate()[0]['score_version'],'zone_ppe_v2')

    def test_rank_uses_unrounded_score_and_stable_tie_keys(self):
        self.set_risk('A',3);self.set_risk('B',2)
        for day in (5,12,19,26):self.add(f'2026-01-{day:02d}T00:00:00Z',zone='A')
        for _ in range(5):self.add('2026-01-05T00:00:00Z',zone='B')
        rows=self.generate()
        self.assertEqual([r['priority_score'] for r in rows],[6.34,6.34])
        self.assertEqual([r['zone_name'] for r in rows],['A','B'])
        # Equal raw scores use zone/PPE rather than database row order.
        with repository.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='pending'")
        self.set_risk('A',1);self.set_risk('B',1)
        for zone,ppe in (('B','vest'),('A','vest'),('A','helmet')):
            self.add('2026-01-05T00:00:00Z',zone=zone,ppe=ppe)
        expected=[('A','helmet'),('A','vest'),('B','vest')]
        for _ in range(2):self.assertEqual([(r['zone_name'],r['ppe_type']) for r in self.generate()],expected)
