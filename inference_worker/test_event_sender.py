
"""Tests for event submission ID stability.""" 

import unittest

from inference_worker.event_sender import EventSender


class FakeClient:
    def __init__(self):
        self.calls = []
        self.fail_first = True

    def submit_event(self, payload):
        self.calls.append(payload.copy())

        if self.fail_first:
            self.fail_first = False
            raise ConnectionError("Simulated network failure")

        return {
            "event_id": payload["event_id"],
            "created": False,
        }


class EventSenderTest(unittest.TestCase):
    def test_retry_preserves_event_id(self):
        client = FakeClient()
        sender = EventSender(client)

        payload = {
            "event_id": "EVT_" + "a" * 32,
            "camera_id": "CAM_001",
        }

        with self.assertRaises(ConnectionError):
            sender.send(payload)

        result = sender.send(payload)

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0], client.calls[1])
        self.assertEqual(result["event_id"], payload["event_id"])

    def test_invalid_event_id(self):
        sender = EventSender(FakeClient())

        with self.assertRaises(ValueError):
            sender.send({"event_id": "wrong"})


if __name__ == "__main__":
    unittest.main()
