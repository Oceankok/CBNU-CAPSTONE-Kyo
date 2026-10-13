import unittest
from unittest.mock import patch

from backend.services.warning_broadcast_service import _select_broadcast_message
from backend.tests import test_field as field_fixture
from backend.field.service import enqueue_event_broadcast
from backend.db.event_repository import get_connection
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
