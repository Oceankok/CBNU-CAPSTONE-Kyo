import unittest
import time
from unittest.mock import patch

from backend.services.warning_broadcast_service import _select_broadcast_message
from backend.tests import test_field as field_fixture
from backend.field.service import enqueue_event_broadcast
from backend.db.event_repository import get_connection, get_broadcast_settings
from field_agent.main import execute


class LocaleSelectionTests(unittest.TestCase):
    def test_exact_then_primary_with_zone_priority(self):
        rows = [{'ppe_type':'helmet','zone_name':zone,'language':lang,'message':message}
                for zone,lang,message in [('', 'ko-KR','generic locale'),('A','ko','zone primary'),
                                          ('A','ko-KR','zone locale'),('', 'en','English')]]
        self.assertEqual(_select_broadcast_message(rows,'helmet','A','KO_kr'), 'zone locale')
        self.assertEqual(_select_broadcast_message(rows[:2],'helmet','A','ko-KR'), 'zone primary')
        self.assertEqual(_select_broadcast_message(rows,'helmet','B','en-US'), 'English')
        self.assertIsNone(_select_broadcast_message(rows,'helmet','B','vi-VN'))


class LocaleQueueTests(unittest.TestCase):
    def setUp(self):
        field_fixture.FieldBroadcastTests.setUp(self)

    def test_configured_voice_locale_produces_event_broadcast(self):
        self.client.post('/api/field/heartbeat', headers=self.node).raise_for_status()
        with get_connection() as conn:
            conn.execute("UPDATE field_node SET language='ko-KR',voice_id='test-korean-voice' WHERE node_id='pc1'")
        queued = enqueue_event_broadcast('EVT_0001')
        self.assertTrue(queued['queued'])
        command = self.client.post('/api/field/commands/claim', headers=self.node).json()['command']
        self.assertEqual(command['payload']['language'], 'ko-KR')
        with patch('field_agent.main.speak_message', return_value={'spoken': True}) as speak:
            result = execute(command)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(speak.called)

    def test_one_queued_event_plays_primary_then_additional_languages(self):
        self.client.post('/api/field/heartbeat', headers=self.node).raise_for_status()
        with get_connection() as conn:
            conn.execute("UPDATE field_node SET language='ko-KR',voice_id='korean-voice' WHERE node_id='pc1'")
        settings = get_broadcast_settings()
        settings['additional_languages'] = ['ko', 'en', 'ja']
        settings['templates'].append({
            'ppe_type': 'helmet', 'zone_name': '', 'language': 'en', 'message': 'Wear a helmet.',
        })
        endpoint = '/api/broadcast/settings'
        response = self.client.put(endpoint, headers=self.admin, json=settings).raise_for_status()
        self.assertEqual(response.json()['additional_languages'], ['ko', 'en', 'ja'])
        settings.pop('additional_languages')
        self.client.put(endpoint, headers=self.admin, json=settings).raise_for_status()
        self.assertEqual(get_broadcast_settings()['additional_languages'], ['ko', 'en', 'ja'])
        self.assertTrue(enqueue_event_broadcast('EVT_0001')['queued'])
        self.assertEqual(enqueue_event_broadcast('EVT_0001')['reason'], 'already_requested')
        command = self.client.post('/api/field/commands/claim', headers=self.node).json()['command']
        self.assertEqual(command['payload']['language'], 'ko-KR')
        self.assertEqual(command['payload']['additional_announcements'], [
            {'language': 'en', 'voice_id': None, 'message': 'Wear a helmet.'},
        ])
        with patch('field_agent.main.speak_message', return_value={'spoken': True, 'reason': 'tts_completed'}) as speak:
            result = execute(command)
        self.assertEqual([call.args[1:] for call in speak.call_args_list], [('ko-KR', 'korean-voice'), ('en', None)])
        self.assertEqual(result['status'], 'completed')
        self.assertIsNone(self.client.post('/api/field/commands/claim', headers=self.node).json()['command'])

    def test_schema_38_preserves_existing_broadcast_settings(self):
        from backend.db.connection import is_postgres, schema_version
        from backend.db.migrations import LATEST_VERSION, apply_migrations

        before = get_broadcast_settings()
        postgres = is_postgres()
        with get_connection() as conn:
            conn.execute('ALTER TABLE broadcast_setting DROP COLUMN additional_languages_json')
            if postgres:
                conn.execute('UPDATE app_schema_version SET version=38 WHERE singleton=1')
            else:
                conn.execute('PRAGMA user_version=38')
        if postgres:
            from backend.db.postgres import initialize
            initialize(seed=False)
        else:
            with get_connection() as conn:
                apply_migrations(conn)
        with get_connection() as conn:
            self.assertEqual(schema_version(conn), LATEST_VERSION)
        self.assertEqual(get_broadcast_settings(), before)


class SequencePlaybackTests(unittest.TestCase):
    def test_one_unavailable_voice_does_not_skip_the_next_language(self):
        primary = {'language': 'ko', 'message': '안전모를 착용하세요.'}
        alternate = {'language': 'en', 'message': 'Wear a helmet.'}
        command = {'kind': 'broadcast', 'expires_at': time.time()+30,
                   'payload': {**primary, 'additional_announcements': [alternate]}}
        with patch('field_agent.main.speak_message', side_effect=[
            {'spoken': False, 'reason': 'voice_not_found'},
            {'spoken': True, 'reason': 'tts_completed'},
        ]) as speak:
            result = execute(command)
        self.assertEqual(speak.call_count, 2)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['result']['reason'], 'tts_sequence_incomplete')
        self.assertEqual([item['spoken'] for item in result['result']['announcements']], [False, True])
