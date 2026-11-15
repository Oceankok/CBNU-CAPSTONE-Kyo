"""Source/API interruptions must not restart an already observed episode."""
import unittest
from unittest.mock import patch
import httpx

from inference_worker import run_server as runner
from inference_worker.pending_events import EventSaveOutbox
from inference_worker import test_runner_resilience as resilience_fixtures
from inference_worker.test_server_mock import FakeVideoClient
from inference_worker.test_roi_runner_end_to_end import ReviewDetector


class EpisodeRunnerTests(unittest.TestCase):
    def run_case(self, client, *, dual=False):
        if dual:
            policy=client.get_ppe_policy
            client.get_ppe_policy=lambda c: {**policy(c),"required_ppe":["helmet","vest"],
                                           "detectable_ppe":["helmet","vest"]}
        return resilience_fixtures.RunnerResilienceTests().run_runner(client,review=True,detector=ReviewDetector(warmup_frames=0))

    def test_poll_failure_preserves_person_and_active_episode(self):
        client=FakeVideoClient(13)
        original=client.list_chunks
        failed=False
        def chunks(stream,after_sequence=-1):
            nonlocal failed
            if after_sequence==5 and not failed:
                failed=True
                raise httpx.ReadTimeout("poll interruption")
            return original(stream,after_sequence)[:1]
        client.list_chunks=chunks
        output,_=self.run_case(client)
        self.assertTrue(failed)
        self.assertIn("[SERVER RETRY]",output)
        self.assertEqual(len(client.submitted_events),1)
        self.assertEqual(client.downloads,[str(i) for i in range(13)])

    def test_entire_dual_ppe_frame_is_durable_before_lease_failure(self):
        class RecordingOutbox(EventSaveOutbox):
            def close(self):
                self.final_counts=self.counts()
                super().close()
        box=RecordingOutbox(":memory:",camera_id="CAM_001")
        client=FakeVideoClient(9)
        original=client.set_inference_load
        failed=False
        def lease(busy,ttl_seconds=30,*,session_id=None):
            nonlocal failed
            if (not busy and not failed and not hasattr(box,"final_counts")
                    and box.counts().get("pending")==2):
                failed=True
                raise httpx.ReadTimeout("lease response loss after both candidates")
            return original(busy,ttl_seconds,session_id=session_id)
        client.set_inference_load=lease
        with patch.object(runner,"EventSaveOutbox",return_value=box):
            output,_=self.run_case(client,dual=True)
        self.assertTrue(failed)
        self.assertEqual(box.final_counts,{})
        self.assertEqual(len(client.submitted_events),2)
        self.assertEqual({e["ppe_type"] for e in client.submitted_events},{"helmet","vest"})
        self.assertEqual(len({e["event_id"] for e in client.submitted_events}),2)
        self.assertIn("[SERVER RETRY]",output)
        self.assertEqual(client.downloads.count("3"),2)

    def test_metadata_timeout_does_not_erase_episode_or_create_new_id(self):
        client=FakeVideoClient(9)
        original=client.submit_event
        attempts=[]
        def submit(body):
            attempts.append(body)
            if len(attempts)==1:
                raise httpx.ReadTimeout("lost request")
            return original(body)
        client.submit_event=submit
        ticks=iter(range(1000))
        with patch("inference_worker.pending_events.time.time",side_effect=lambda:next(ticks)):
            self.run_case(client)
        self.assertEqual(len(attempts),2)
        self.assertEqual(attempts[0],attempts[1])
        self.assertEqual(len(client.submitted_events),1)



    def test_rejected_metadata_is_retained_without_clip_resubmission(self):
        client=FakeVideoClient(9)
        attempts=[]
        def reject(body):
            attempts.append(body)
            httpx.Response(422,request=httpx.Request("POST","http://test/events")).raise_for_status()
        client.submit_event=reject
        output,delivery=self.run_case(client)
        self.assertEqual(len(attempts),1)
        delivery.assert_not_called()
        self.assertIn("[CLIP BLOCKED]",output)
        self.assertIn("'rejected': 1",output)


    def test_incomplete_clip_auth_rejection_stops(self):
        client=FakeVideoClient()
        def denied(event_id):
            httpx.Response(403,request=httpx.Request("GET","http://test/clips")).raise_for_status()
        client.get_clip_requests=denied
        with self.assertRaises(httpx.HTTPStatusError):
            runner.report_incomplete_clip(client,{"event_id":"EVT_test","camera_id":"CAM_001"})
