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
        client.set_inference_load.assert_called_once_with(True)
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
