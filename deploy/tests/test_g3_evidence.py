"""G3 PostgreSQL/API evidence contract; synthetic rows, no inference or video proof.

Run: python -m unittest deploy.tests.test_g3_evidence -v
Requires PPE_TEST_POSTGRES_URL pointing to a dedicated kyo_test database.
"""
import os
import time
import unittest

from backend.db.event_repository import get_connection
from backend.tests.support import login, ready_media
from backend.tests import test_media_recovery as recovery


@unittest.skipUnless(os.environ.get('PPE_TEST_POSTGRES_URL'), 'Dedicated PostgreSQL required')
class G3EvidenceTests(unittest.TestCase):
    setUp = recovery.MediaRecoveryTests.setUp

    def test_timeout_is_exposed_and_never_counts_as_complete_evidence(self):
        from backend.media.maintenance import maintain_once
        headers = login(self.client)
        response = self.client.post('/api/events/EVT_0001/clip-requests', headers=headers,
                                    json={'camera_id': 'CAM_002'})
        self.assertEqual(response.status_code, 201, response.text)
        request_id = response.json()['request_id']

        def media():
            response = self.client.get('/api/events/EVT_0001/media', headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        def assert_blocked():
            response = self.client.post('/api/auto-decisions/preview', headers=headers,
                                        json={'event_ids': ['EVT_0001'], 'threshold': .9,
                                              'model_versions': ['test-model'], 'ppe_types': ['helmet'],
                                              'policy_label': 'g3-synthetic-evidence-contract'})
            self.assertEqual(response.status_code, 200, response.text)
            item = response.json()['items'][0]
            self.assertIn('evidence_incomplete', item['blocking_reasons'])
            self.assertFalse(item['automatic_confirmation_allowed'])
            self.assertFalse(item['structural_eligible'])

        self.assertEqual(media()['review_availability']['state'], 'awaiting_clip')
        assert_blocked()
        with get_connection() as conn:
            before = conn.execute('SELECT COUNT(*) FROM event_review').fetchone()[0]
            conn.execute('UPDATE event_clip_request SET created_at=?,updated_at=? WHERE request_id=?',
                         (time.time()-301, time.time()-301, request_id))
        maintain_once()
        result = media()
        request = next(row for row in result['clip_requests'] if row['request_id'] == request_id)
        self.assertEqual((request['status'], request['error_code']),
                         ('unavailable', 'clip_delivery_timeout'))
        self.assertEqual(result['review_availability']['state'], 'no_usable_media')
        self.assertFalse(result['review_availability']['can_review'])
        assert_blocked()

        # Existing UI renders 'available' when ANY media is usable. The API must
        # still expose the missing reference, and auto decision must still block.
        # ready_media is a synthetic image fixture, not validated video evidence.
        with ready_media():
            result = media()
            self.assertEqual(result['review_availability']['state'], 'available')
            self.assertEqual(result['clip_requests'][0]['status'], 'unavailable')
            assert_blocked()
        settings = self.client.get('/api/auto-decisions/settings', headers=headers).json()
        self.assertEqual(settings['mode'], 'off')
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_review').fetchone()[0], before)
        print('[G3 EVIDENCE] pending -> unavailable/clip_delivery_timeout; '
              'media API exposed; preview evidence_incomplete before/after timeout; '
              'OFF and review writes=0; synthetic PostgreSQL only', flush=True)


if __name__ == '__main__':
    unittest.main()
