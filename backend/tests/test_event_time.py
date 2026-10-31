"""UTC event storage and Korean reporting boundaries; isolated DB only."""
from datetime import datetime
import unittest
from uuid import uuid4

from backend.db.connection import week_expression
from backend.db.event_repository import (get_connection, insert_candidate_event,
    generate_quarterly_stats, generate_education_recommendations)
from backend.event_time import stored_timestamp, public_timestamp, quarter_for, local_time_sql
from backend.tests.support import isolated_api, login


class EventTimeTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.admin = login(self.client)
        token = self.client.post('/api/nodes', headers=self.admin,
            json={'node_id': 'clock', 'name': 'clock'}).json()['token']
        self.node = {'Authorization': 'Bearer '+token}
        self.client.put('/api/processing/workers/clock', headers=self.admin, json={'enabled': True}).raise_for_status()

    def event(self, stamp, status='confirmed'):
        ident = 'CLOCK_'+uuid4().hex
        insert_candidate_event({'event_id': ident, 'camera_id': 'CAM_001', 'tracking_id': 'clock',
            'ppe_type': 'helmet', 'timestamp_start': stamp, 'timestamp_end': stamp, 'duration_sec': 0,
            'frame_sample_count': 1, 'thumbnail_path': '', 'video_clip_path': '', 'ai_confidence': 0.9,
            'person_detected': 1, 'ppe_detected': 0, 'model_version': 'test', 'event_status': status})
        return ident

    def submission(self, stamp):
        return {'event_id': 'EVT_'+uuid4().hex, 'camera_id': 'CAM_001', 'tracking_id': 'clock',
                'ppe_type': 'helmet', 'timestamp_start': stamp, 'timestamp_end': stamp,
                'confidence': 0.9, 'frame_sample_count': 1, 'model_version': 'test'}

    def test_new_processing_event_stores_utc_and_returns_explicit_korean_time(self):
        body = self.submission('2030-04-01T01:00:00+09:00')
        self.client.post('/api/processing/events', headers=self.node, json=body).raise_for_status()
        with get_connection() as conn:
            row = conn.execute('SELECT timestamp_start FROM candidate_event WHERE event_id=?', (body['event_id'],)).fetchone()
            self.assertEqual(row[0], '2030-03-31T16:00:00.000000+00:00')
        event = self.client.get('/api/events/'+body['event_id'], headers=self.admin).json()['event']
        self.assertEqual(event['timestamp_start'], '2030-04-01T01:00:00.000000+09:00')
        self.assertEqual(event['event_date_local'], '2030-04-01')
        self.assertEqual(event['timestamp_timezone'], 'Asia/Seoul')
        listed = self.client.get('/api/events', headers=self.admin).json()['items']
        self.assertEqual(next(item for item in listed if item['event_id'] == body['event_id']), event)
        retry = {**body, 'timestamp_start': '2030-03-31T16:00:00Z', 'timestamp_end': '2030-03-31T16:00:00Z'}
        self.assertFalse(self.client.post('/api/processing/events', headers=self.node, json=retry).json()['created'])

    def test_old_processing_retry_compares_same_instant_without_rewriting_record(self):
        body = self.submission('2030-03-31T16:00:00Z')
        self.client.post('/api/processing/events', headers=self.node, json=body).raise_for_status()
        old = '2030-03-31 16:00:00.000000'
        with get_connection() as conn:
            conn.execute('UPDATE candidate_event SET timestamp_start=?,timestamp_end=? WHERE event_id=?', (old,old,body['event_id']))
        response = self.client.post('/api/processing/events', headers=self.node, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['created'])
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT timestamp_start FROM candidate_event WHERE event_id=?', (body['event_id'],)).fetchone()[0], old)

    def test_unknown_legacy_timezone_is_not_invented(self):
        stamp = '2031-04-01 01:02:03'
        ident = self.event(stamp)
        event = self.client.get('/api/events/'+ident, headers=self.admin).json()['event']
        self.assertEqual(event['timestamp_start'], stamp)
        self.assertIsNone(event['timestamp_timezone'])
        self.assertIsNone(event['event_date_local'])
        self.assertEqual(generate_quarterly_stats('2031-Q2')['summary']['confirmed_count'], 1)

    def test_quarters_and_education_follow_korean_midnight_including_subseconds(self):
        for stamp in ('2030-03-31T14:59:59.999999Z', '2030-03-31T15:00:00Z',
                      '2030-03-31T16:00:00Z', '2030-06-30T15:00:00Z'):
            self.event(stamp)
        stats = generate_quarterly_stats('2030-Q2')
        self.assertEqual(stats['summary']['candidate_count'], 2)
        self.assertEqual(stats['summary']['confirmed_count'], 2)
        trend = {row['quarter']: row['helmet'] for row in stats['trend']}
        self.assertEqual([trend['2030-Q1'], trend['2030-Q2'], trend['2030-Q3']], [1,2,1])
        recommendations = generate_education_recommendations('2030-Q2')['items']
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]['score_breakdown']['confirmed_count'], 2)

    def test_reporting_week_matches_python_korean_calendar(self):
        for stamp in ('2026-04-05T14:59:59.999999Z', '2026-04-05T15:00:00Z'):
            expression = week_expression(local_time_sql('stamp'))
            with get_connection() as conn:
                value = conn.execute(f'SELECT {expression} FROM (SELECT ? AS stamp) event_clock', (stamp,)).fetchone()[0]
            expected = int(datetime.fromisoformat(public_timestamp(stamp)).strftime('%W'))
            self.assertEqual(int(value), expected)

    def test_manual_merge_updates_the_korean_quarter_count(self):
        target = self.event('2030-03-31T15:00:00Z', 'pending')
        source = self.event('2030-03-31T16:00:00Z', 'pending')
        self.assertEqual(generate_quarterly_stats('2030-Q2')['summary']['candidate_count'], 2)
        response = self.client.post('/api/events/'+target+'/merge', headers=self.admin,
            json={'source_event_ids': [source], 'expected_version': 0, 'reason': 'same test incident'})
        self.assertEqual(response.status_code, 200, response.text)
        summary = self.client.get('/api/stats?quarter=2030-Q2', headers=self.admin).json()
        self.assertEqual(summary['summary']['candidate_count'], 1)

    def test_helpers_preserve_instant_and_legacy_values(self):
        self.assertEqual(stored_timestamp('2030-04-01T01:00:00+09:00'), '2030-03-31T16:00:00.000000+00:00')
        self.assertEqual(quarter_for('2030-03-31T15:00:00Z'), '2030-Q2')
        self.assertEqual(stored_timestamp('2030-04-01 01:00:00'), '2030-04-01 01:00:00')


    def test_date_filter_uses_korean_date_and_includes_last_microsecond(self):
        before = self.event('2030-03-31T14:59:59.999999Z')
        first = self.event('2030-03-31T15:00:00Z')
        last = self.event('2030-04-01T14:59:59.999999Z')
        after = self.event('2030-04-01T15:00:00Z')
        response = self.client.get('/api/events?date_from=2030-04-01&date_to=2030-04-01', headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({item['event_id'] for item in response.json()['items']}, {first,last})
        self.assertEqual(response.json()['reporting_timezone'], 'Asia/Seoul')
        self.assertEqual(self.client.get('/api/events?date_from=2030-04-02&date_to=2030-04-01', headers=self.admin).status_code, 400)
        self.assertEqual(self.client.get('/api/events?date_from=invalid', headers=self.admin).status_code, 422)

    def test_false_positive_aggregate_uses_korean_quarter(self):
        from backend.tests.support import ready_media
        ident = self.event('2030-03-31T16:00:00Z', 'pending')
        with ready_media(ident):
            response = self.client.post('/api/events/'+ident+'/review', headers=self.admin,
                json={'review_result': 'false_positive', 'review_reason_code': 'incorrect_ppe_detection'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(generate_quarterly_stats('2030-Q2')['summary']['false_positive_count'], 1)
        self.assertEqual(generate_quarterly_stats('2030-Q1')['summary']['false_positive_count'], 0)
        review = self.client.get('/api/events/'+ident, headers=self.admin).json()['review']
        self.assertIsNotNone(datetime.fromisoformat(review['review_time']).tzinfo)


    def test_local_service_default_timestamp_is_explicit_utc(self):
        from backend.services.candidate_event_service import create_no_helmet_candidate_event
        result = create_no_helmet_candidate_event(camera_id='CAM_001', confidence=0.9, enable_tts=False)
        with get_connection() as conn:
            value = conn.execute('SELECT timestamp_start FROM candidate_event WHERE event_id=?', (result['event_id'],)).fetchone()[0]
        self.assertTrue(value.endswith('+00:00'))
