import os
import unittest
from unittest.mock import patch
from uuid import uuid4
from backend.db.event_repository import (get_connection, insert_candidate_event, generate_quarterly_stats,
    generate_education_recommendations)
from backend.tests.support import isolated_api, login, ready_media

class EventConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        with get_connection() as conn:
            conn.execute('DELETE FROM candidate_event')
            conn.execute('DELETE FROM false_positive_aggregate')
            conn.execute('DELETE FROM legacy_false_positive_summary')
            conn.execute("UPDATE camera_info SET zone_name='History A',process_type='프레스' WHERE camera_id='CAM_001'")

    def event(self, status='pending'):
        ident = 'CONSISTENCY_'+uuid4().hex
        insert_candidate_event(dict(event_id=ident,camera_id='CAM_001',tracking_id='test',ppe_type='helmet',
            timestamp_start='2030-04-01T01:00:00Z',timestamp_end='2030-04-01T01:00:03Z',duration_sec=3,
            frame_sample_count=3,thumbnail_path='',video_clip_path='',ai_confidence=0.9,
            person_detected=1,ppe_detected=0,model_version='fake',event_status=status))
        return ident

    def test_occurrence_context_survives_camera_move_in_all_queries(self):
        old = self.event('confirmed')
        with get_connection() as conn:
            conn.execute("UPDATE camera_info SET zone_name='History B',process_type='일반' WHERE camera_id='CAM_001'")
        new = self.event('confirmed')
        detail = self.client.get('/api/events/'+old,headers=self.admin).json()['event']
        self.assertEqual((detail['zone_name'],detail['process_type']),('History A','프레스'))
        listing = self.client.get('/api/events?zone=History%20A',headers=self.admin).json()
        self.assertEqual([r['event_id'] for r in listing['items']],[old])
        summary = self.client.get('/api/events/summary',headers=self.admin)
        self.assertEqual(summary.status_code,200,summary.text)
        stats = generate_quarterly_stats('2030-Q2')
        self.assertEqual({r['zone_name']:r['confirmed_count'] for r in stats['by_zone']}, {'History A':1,'History B':1})
        rows = {r['zone_name']:r for r in generate_education_recommendations('2030-Q2')['items']}
        self.assertEqual(rows['History A']['score_breakdown']['process_risk_weight'],1.5)
        self.assertEqual(rows['History B']['score_breakdown']['process_risk_weight'],1.0)

    def test_false_positive_transitions_count_once_and_reverse(self):
        ident = self.event()
        with ready_media(ident):
            for index, decision in enumerate(('false_positive','false_positive','confirmed','false_positive','hold')):
                method = self.client.post if index == 0 else self.client.put
                response = method('/api/events/'+ident+'/review',headers=self.admin,json=dict(
                    review_result=decision, review_reason_code={'false_positive':'incorrect_ppe_detection','confirmed':'confirmed_no_helmet','hold':'insufficient_evidence'}[decision],
                    second_review_needed=True))
                self.assertEqual(response.status_code,200,response.text)
                stats = generate_quarterly_stats('2030-Q2')['summary']
                self.assertEqual(stats['false_positive_count'],int(decision=='false_positive'))
                self.assertEqual(stats['confirmed_count'],int(decision=='confirmed'))

    def test_process_weight_override_and_invalid_config(self):
        self.event('confirmed')
        generate_quarterly_stats('2030-Q2')
        with patch.dict(os.environ,{'PPE_PROCESS_RISK_WEIGHTS':'{"프레스":2.0}'}):
            row=generate_education_recommendations('2030-Q2')['items'][0]
            self.assertEqual(row['score_breakdown']['process_risk_weight'],2.0)
        with patch.dict(os.environ,{'PPE_PROCESS_RISK_WEIGHTS':'{"프레스":-1}'}):
            with self.assertRaises(ValueError):
                generate_education_recommendations('2030-Q2')

    def test_v20_upgrade_repairs_duplicates_and_preserves_anonymous_counts(self):
        from backend.db.connection import is_postgres
        from backend.db.postgres import initialize
        if not is_postgres():
            self.skipTest('PostgreSQL migration contract')
        ident=self.event('false_positive')
        with get_connection() as conn:
            conn.execute("INSERT INTO event_review(review_id,event_id,review_result) VALUES(?,?,'false_positive')",(uuid4().hex,ident))
            conn.execute("INSERT INTO event_review_history(history_id,review_id,event_id,review_result,recorded_at) VALUES(?,?,?,'false_positive','test')",(uuid4().hex,'old',ident))
            conn.execute("INSERT INTO false_positive_aggregate(aggregate_id,quarter,zone_name,ppe_type,false_positive_count,updated_at) VALUES('old','2030-Q2','History A','helmet',5,'test')")
            conn.execute('DROP TABLE legacy_false_positive_summary')
            conn.execute('UPDATE candidate_event SET context_source=NULL,zone_name_snapshot=NULL,process_type_snapshot=NULL')
            conn.execute('UPDATE app_schema_version SET version=20')
        initialize(seed=False)
        self.assertEqual(generate_quarterly_stats('2030-Q2')['summary']['false_positive_count'],4)
        initialize(seed=False)
        self.assertEqual(generate_quarterly_stats('2030-Q2')['summary']['false_positive_count'],4)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT context_source FROM candidate_event WHERE event_id=?',(ident,)).fetchone()[0],'upgrade_current')
