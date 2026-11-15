"""Durable save retries and real API idempotency (PostgreSQL only for DB test)."""
from copy import deepcopy
from datetime import datetime, timezone
import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
import httpx

from inference_worker.pending_events import EventSaveOutbox, EventQueueFull


def event():
    return dict(event_id="EVT_"+uuid4().hex, camera_id="CAM_001",tracking_id="track-1",
                ppe_type="helmet",review_only=True,confidence=None,
                timestamp_start="2026-11-15T00:00:00+00:00",
                timestamp_end="2026-11-15T00:00:03+00:00",
                frame_sample_count=13,model_version="test-only")


def failure(status):
    httpx.Response(status,request=httpx.Request("POST","http://test/events")).raise_for_status()


class EventOutboxTests(unittest.TestCase):
    def box(self, **kw):
        box=EventSaveOutbox(":memory:",camera_id="CAM_001",**kw)
        self.addCleanup(box.close)
        return box

    def test_lost_response_restart_retries_exact_body_and_id(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/"pending.sqlite3"
            body=event()
            body["capture_context_id"]=str(uuid4())
            sent=[]
            def lost(body):
                sent.append(deepcopy(body))
                raise httpx.ReadTimeout("response lost after server commit")
            box=EventSaveOutbox(path,camera_id="CAM_001")
            box.enqueue(body)
            body["tracking_id"]="changed-by-caller"
            box.pump(lost,now=0)
            box.close()
            box=EventSaveOutbox(path,camera_id="CAM_001")
            self.assertEqual(box.pump(lambda b: sent.append(b) or b,now=.5),[])
            self.assertEqual(box.pump(lambda b: sent.append(b) or b,now=1)[0][1],"saved")
            self.assertEqual(sent[0],sent[1])
            self.assertEqual(sent[1]["tracking_id"],"track-1")
            self.assertIsNone(sent[1]["confidence"])
            self.assertTrue(sent[1]["review_only"])
            self.assertEqual(box.counts(),{})
            box.close()

    def test_same_id_cannot_change_body(self):
        box=self.box();body=event();box.enqueue(body);box.enqueue(deepcopy(body))
        with self.assertRaisesRegex(ValueError,"conflict"):
            box.enqueue({**body,"camera_id":"CAM_001","tracking_id":"other"})
        self.assertEqual(box.counts(),{"pending":1})

    def test_permanent_rejection_retained_without_retry_storm(self):
        for status in (409,422):
            with self.subTest(status=status):
                box=self.box();box.enqueue(event());calls=[]
                def reject(body):
                    calls.append(body);failure(status)
                box.pump(reject,now=0)
                box.pump(reject,now=100)
                self.assertEqual(len(calls),1)
                self.assertEqual(box.counts(),{"rejected":1})

    def test_auth_failure_stops_and_preserves_request(self):
        box=self.box();box.enqueue(event())
        with self.assertRaises(httpx.HTTPStatusError):
            box.pump(lambda _:failure(403),now=0)
        self.assertEqual(box.counts(),{"pending":1})

    def test_transient_backoff_and_other_events_not_starved(self):
        box=self.box();one,two=event(),event()
        box.enqueue(one);box.enqueue(two)
        self.assertEqual(box.pump(lambda _:failure(503),now=0)[0][0],one["event_id"])
        self.assertEqual(box.pump(lambda b:b,now=0)[0][0],two["event_id"])
        self.assertEqual(box.pump(lambda b:b,now=.5),[])
        self.assertEqual(box.pump(lambda b:b,now=1)[0][0],one["event_id"])

    def test_full_queue_keeps_existing_request(self):
        box=self.box(limit=1);body=event();box.enqueue(body)
        with self.assertRaises(EventQueueFull):
            box.enqueue(event())
        sent=[];box.pump(lambda b:sent.append(b) or b,now=0)
        self.assertEqual(sent,[body])

    def test_wrong_ack_does_not_remove_pending(self):
        box=self.box();box.enqueue(event())
        self.assertEqual(box.pump(lambda b:{"event_id":"other"},now=0)[0][1],"response_binding")
        self.assertEqual(box.counts(),{"pending":1})

    def test_invalid_json_response_keeps_request_for_retry(self):
        box=self.box();body=event();box.enqueue(body)
        self.assertEqual(box.pump(lambda b:json.loads("invalid"),now=0)[0][1],"response_json")
        self.assertEqual(box.pump(lambda b:b,now=1),[(body["event_id"],"saved")])

    def test_cross_camera_reopen_rejected(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/"pending.sqlite3"
            EventSaveOutbox(path,camera_id="CAM_001").close()
            with self.assertRaisesRegex(ValueError,"camera mismatch"):
                EventSaveOutbox(path,camera_id="CAM_002")

    def test_corrupt_body_never_sent(self):
        box=self.box();box.enqueue(event())
        with box.db:
            box.db.execute("UPDATE requests SET body='{}'")
        def forbidden(_): self.fail("corrupt request submitted")
        self.assertEqual(box.pump(forbidden,now=0)[0][1],"body_integrity")
        self.assertEqual(box.counts(),{"rejected":1})


@unittest.skipUnless(os.environ.get("PPE_TEST_POSTGRES_URL"),"requires isolated PostgreSQL")
class EventOutboxPostgresTests(unittest.TestCase):
    def test_commit_then_lost_response_creates_one_review_event(self):
        from backend.tests.support import isolated_api, login
        from backend.db.event_repository import get_connection
        client=self.enterContext(isolated_api())
        admin=login(client)
        token=client.post("/api/nodes",headers=admin,json={"node_id":"outbox-test","name":"test"}).json()["token"]
        client.put("/api/processing/workers/outbox-test",headers=admin,json={"enabled":True}).raise_for_status()
        node={"Authorization":"Bearer "+token}
        body=event()
        body["timestamp_start"]=body["timestamp_end"]=datetime.now(timezone.utc).isoformat()
        replies=[]
        def submit(b):
            response=client.post("/api/processing/events",headers=node,json=b)
            response.raise_for_status()
            replies.append(response.json())
            if len(replies)==1:
                raise httpx.ReadTimeout("lost after commit")
            return response.json()
        with TemporaryDirectory() as temp:
            path=Path(temp)/"pending.sqlite3"
            box=EventSaveOutbox(path,camera_id="CAM_001");box.enqueue(body)
            box.pump(submit,now=0);box.close()
            box=EventSaveOutbox(path,camera_id="CAM_001")
            self.assertEqual(box.pump(submit,now=1),[(body["event_id"],"saved")])
            box.close()
        self.assertEqual([r["created"] for r in replies],[True,False])
        self.assertTrue(all(r["review_only"] for r in replies))
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidate_event WHERE event_id=?",(body["event_id"],)).fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT review_only FROM processing_review_only_event WHERE event_id=?",(body["event_id"],)).fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT event_status FROM candidate_event WHERE event_id=?",(body["event_id"],)).fetchone()[0],"pending")
