"""Approved education-only concentration: PPE fraction within its event zone."""
import unittest
from uuid import uuid4

from backend.db import event_repository as repository
from backend.tests.support import isolated_api


class EducationZoneShareTests(unittest.TestCase):
    def setUp(self):
        self.client=self.enterContext(isolated_api())
        with repository.get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='pending'")
            for camera,zone in (('SHARE_A','A'),('SHARE_B','B'),('SHARE_NONE',None)):
                conn.execute("INSERT INTO camera_info(camera_id,camera_location,zone_name,status) VALUES(?,?,?,'active')",(camera,camera,zone))

    def add(self, count, ppe='helmet', camera='SHARE_A', status='confirmed', stamp='2026-04-15T10:00:00+09:00'):
        ids=[]
        with repository.get_connection() as conn:
            for _ in range(count):
                ident='EVT_'+uuid4().hex;ids.append(ident)
                conn.execute('''INSERT INTO candidate_event(event_id,camera_id,ppe_type,event_status,timestamp_start,timestamp_end)
                    VALUES(?,?,?,?,?,?)''',(ident,camera,ppe,status,stamp,stamp))
        return ids

    def recommendations(self,quarter='2026-Q2'):
        return {(row['zone_name'],row['ppe_type']):{**row,**row['score_breakdown']} for row in
                repository.generate_education_recommendations(quarter)['items']}

    def test_twenty_and_five_in_one_zone_are_point_eight_and_point_two(self):
        self.add(20);self.add(5,'vest')
        rows=self.recommendations()
        self.assertEqual(rows['A','helmet']['zone_concentration'],.8)
        self.assertEqual(rows['A','vest']['zone_concentration'],.2)
        for row in rows.values():
            self.assertEqual(row['process_risk_weight'],1.0)
            expected=round(row['confirmed_count']*(1+.2*row['occurrence_week_ratio'])*row['process_risk_weight'],2)
            self.assertEqual(row['priority_score'],expected)

    def test_other_zone_additions_do_not_change_this_zone_share_or_score(self):
        self.add(20);self.add(5,'vest')
        before=self.recommendations()
        self.add(100,'helmet','SHARE_B')
        after=self.recommendations()
        for key in (('A','helmet'),('A','vest')):
            for field in ('zone_concentration','confirmed_count','repeat_weeks','priority_score'):
                self.assertEqual(before[key][field],after[key][field])
        self.assertEqual(after['B','helmet']['zone_concentration'],1)

    def test_only_confirmed_events_in_requested_local_quarter_count(self):
        # Korea local midnight April 1 belongs to Q2 even though UTC is March.
        self.add(4,stamp='2026-03-31T15:00:00+00:00')
        self.add(1,'vest',stamp='2026-06-30T14:59:59+00:00')
        for state in ('pending','false_positive','unreviewable','merged'):
            self.add(10,'vest',status=state)
        self.add(10,'vest',stamp='2026-03-31T14:59:59+00:00')
        self.add(10,'vest',stamp='2026-06-30T15:00:00+00:00')
        rows=self.recommendations()
        self.assertEqual(rows['A','helmet']['confirmed_count'],4)
        self.assertEqual(rows['A','vest']['confirmed_count'],1)
        self.assertEqual(rows['A','helmet']['zone_concentration'],.8)
        self.assertEqual(rows['A','vest']['zone_concentration'],.2)

    def test_frozen_event_zone_is_used_after_camera_moves(self):
        self.add(20);self.add(5,'vest')
        with repository.get_connection() as conn:
            conn.execute("UPDATE camera_info SET zone_name='B' WHERE camera_id='SHARE_A'")
        self.add(100,'helmet')
        rows=self.recommendations()
        self.assertEqual(rows['A','helmet']['zone_concentration'],.8)
        self.assertEqual(rows['A','vest']['zone_concentration'],.2)
        self.assertEqual(rows['B','helmet']['zone_concentration'],1)

    def test_unassigned_zone_and_empty_quarter_are_safe(self):
        self.assertEqual(self.recommendations('2027-Q1'),{})
        self.add(4,camera='SHARE_NONE');self.add(1,'vest',camera='SHARE_NONE')
        rows=self.recommendations()
        self.assertEqual(rows['','helmet']['zone_concentration'],.8)
        self.assertEqual(rows['','vest']['zone_concentration'],.2)

    def test_all_ppe_rows_contribute_to_denominator_before_top_three_limit(self):
        for ppe,count in (('helmet',10),('vest',9),('gloves',8),('safety_shoes',7)):
            self.add(count,ppe)
        rows=self.recommendations()
        self.assertEqual(len(rows),3)
        self.assertEqual(rows['A','helmet']['zone_concentration'],round(10/34,2))
        self.assertEqual(rows['A','vest']['zone_concentration'],round(9/34,2))

    def test_null_and_empty_snapshot_zone_are_one_effective_unassigned_bucket(self):
        self.add(10,camera='SHARE_NONE')
        blank=self.add(10,camera='SHARE_NONE')
        self.add(5,'vest',camera='SHARE_NONE')
        with repository.get_connection() as conn:
            for ident in blank:
                conn.execute("UPDATE event_zone_snapshot SET zone_name='' WHERE event_id=?",(ident,))
        result=repository.generate_education_recommendations('2026-Q2')['items']
        self.assertEqual(len(result),2)
        rows={(row['zone_name'],row['ppe_type']):row['score_breakdown'] for row in result}
        self.assertEqual(rows['','helmet']['confirmed_count'],20)
        self.assertEqual(rows['','helmet']['zone_concentration'],.8)
        self.assertEqual(rows['','vest']['zone_concentration'],.2)
