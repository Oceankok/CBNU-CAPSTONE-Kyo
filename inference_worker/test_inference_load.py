import unittest
from unittest.mock import Mock, patch
from inference_worker.inference_load import InferenceLoad


class InferenceLoadTests(unittest.TestCase):
    def test_refresh_interval_and_release(self):
        client = Mock()
        lease = InferenceLoad(client)
        with patch("inference_worker.inference_load.time.monotonic", return_value=100):
            lease.refresh()
            lease.refresh()
        client.set_inference_load.assert_called_once_with(True, session_id=lease.session_id)
        with patch("inference_worker.inference_load.time.monotonic", return_value=111):
            lease.refresh()
        lease.release()
        self.assertEqual(client.set_inference_load.call_args.args, (False,))
        self.assertFalse(lease.active)

    def test_lost_acquire_response_still_releases(self):
        client = Mock()
        client.set_inference_load.side_effect = [ConnectionError("lost response"), None]
        lease = InferenceLoad(client)
        with self.assertRaises(ConnectionError):
            lease.refresh()
        lease.release()
        self.assertEqual(client.set_inference_load.call_args.args, (False,))


    def test_two_executions_use_distinct_sessions(self):
        client = Mock()
        first, second = InferenceLoad(client), InferenceLoad(client)
        first.refresh()
        second.refresh()
        first.release()
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(client.set_inference_load.call_args.kwargs['session_id'], first.session_id)
        self.assertTrue(second.active)


    def test_old_server_cannot_silently_ignore_new_session_contract(self):
        import os
        import httpx
        from inference_worker.video_client import VideoClient
        with patch.dict(os.environ, {'PPE_PROCESSING_NODE_TOKEN': 'test-only'}):
            client = VideoClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'busy_until': 1})))
        self.addCleanup(client.close)
        with self.assertRaisesRegex(RuntimeError, 'inference_session_unsupported'):
            client.set_inference_load(True, session_id='a'*32)
        self.assertEqual(client.set_inference_load(False), {'busy_until': 1})
