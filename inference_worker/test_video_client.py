"""Exercise the real HTTP client against an isolated FastAPI instance."""
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from backend.tests.support import isolated_api, login
from backend.db.event_repository import get_connection
from inference_worker.video_client import VideoClient
from inference_worker.event_builder import build_event
from inference_worker.event_pipeline import submit_event_with_clip


class VideoClientContractTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        source = root / ".private_media" / "originals"
        source.mkdir(parents=True)
        for name, value in (
            ("backend.media.paths.PROJECT_ROOT", root),
            ("backend.media.paths.STORAGE_ROOT", root / "storage"),
            ("backend.media.paths.SOURCE_ROOT", source),
            ("backend.media.service.SOURCE_ROOT", source),
            ("backend.media.routes.WORK_ROOT", root / ".private_media"),
        ):
            self.enterContext(patch(name, value))
        self.enterContext(patch.dict(os.environ, {"PPE_PROCESSING_MODE": "remote"}))
        self.api = self.enterContext(isolated_api())
        self.admin = login(self.api)
        token = self.api.post("/api/nodes", headers=self.admin,
                             json={"node_id": "inference-contract", "name": "Test"}).json()["token"]
        self.api.put("/api/processing/workers/inference-contract", headers=self.admin,
                     json={"enabled": True}).raise_for_status()
        self.enterContext(patch.dict(os.environ, {
            "PPE_SERVER_URL": "http://testserver",
            "PPE_PROCESSING_NODE_TOKEN": token}))
        self.requests = []
        self.drop_upload_response = False

        def handle(request):
            self.requests.append((request.method, request.url.path))
            response = self.api.request(request.method, request.url.raw_path.decode(),
                                        headers=dict(request.headers), content=request.content)
            if self.drop_upload_response and request.url.path.endswith("/clip") and response.status_code == 201:
                self.drop_upload_response = False
                raise httpx.ReadTimeout("test: upload committed but response lost", request=request)
            return httpx.Response(response.status_code, content=response.content,
                                  headers=dict(response.headers))

        self.client = VideoClient(transport=httpx.MockTransport(handle))
        self.addCleanup(self.client.close)

    def event(self):
        now = datetime.now(timezone.utc)
        return build_event("CAM_001", "track-test", now, now, 0.9, 1)

    def test_event_clip_and_idempotent_retry_use_actual_routes(self):
        directory = self.enterContext(TemporaryDirectory())
        clip = Path(directory) / "clip.mp4"
        clip.write_bytes((Path(__file__).resolve().parents[1] /
                          "backend/db/demo_assets/sample.mp4").read_bytes())
        event = self.event()
        # Real ingestion queues processing; no GPU worker or redactor runs.
        self.drop_upload_response = True
        with patch("inference_worker.event_pipeline.time.sleep"):
            first = submit_event_with_clip(self.client, event, clip)
        second = submit_event_with_clip(self.client, event, clip)
        self.assertFalse(self.drop_upload_response)
        self.assertEqual(first["uploaded_count"], 1)
        with get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM event_media WHERE event_id=?",
                (event["event_id"],)).fetchone()[0], 1)
        self.assertEqual(second["event_id"], first["event_id"])
        with get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM candidate_event WHERE event_id=?",
                (event["event_id"],)).fetchone()[0], 1)
        self.assertEqual(self.client.get_clip_requests(event["event_id"])["event_id"],
                         event["event_id"])
        self.assertIn(("POST", "/api/processing/events/" + event["event_id"] + "/clip"),
                      self.requests)

    def test_worker_permission_and_priority_contract(self):
        self.client.set_inference_load(True)
        self.client.set_inference_load(False)
        self.assertFalse(self.api.get("/api/processing/inference-load",
            headers=self.client.client.headers).json()["busy"])
        self.assertEqual(self.client.list_streams(), [])
        self.assertIn("required_ppe", self.client.get_ppe_policy("CAM_001"))
        self.api.put("/api/processing/workers/inference-contract", headers=self.admin,
                     json={"enabled": False}).raise_for_status()
        with self.assertRaises(httpx.HTTPStatusError) as raised:
            self.client.submit_event(self.event())
        self.assertEqual(raised.exception.response.status_code, 403)

    def test_rejected_payload_is_not_retried(self):
        event = self.event()
        event["confidence"] = 2
        with self.assertRaises(httpx.HTTPStatusError) as raised:
            self.client.submit_event(event)
        self.assertEqual(raised.exception.response.status_code, 422)

    def test_camera_policy_without_roi_preserves_camera_wide_rules(self):
        policy = self.client.get_ppe_policy("CAM_001")
        legacy = self.api.get("/api/cameras/CAM_001/ppe-policy",
                              headers=self.admin).json()
        for key, value in legacy.items():
            self.assertEqual(policy[key], value)
        self.assertEqual(policy["areas"], [])
        self.assertTrue(policy["policy_version"])
        self.assertIn(("GET", "/api/processing/cameras/CAM_001/work-area-policy"),
                      self.requests)
        self.api.put("/api/processing/workers/inference-contract", headers=self.admin,
                     json={"enabled": False}).raise_for_status()
        with self.assertRaises(httpx.HTTPStatusError) as error:
            self.client.get_ppe_policy("CAM_001")
        self.assertEqual(error.exception.response.status_code, 403)

    def test_roi_policy_reaches_person_resolver_and_invalid_roi_fails_closed(self):
        from inference_worker.work_area_policy import resolve_person_policy

        root = "/api/cameras/CAM_001"
        self.api.put("/api/zones/프레스 구역", headers=self.admin,
                     json={"required_ppe": [], "rules": []}).raise_for_status()
        equipment = self.api.post("/api/equipment", headers=self.admin,
            json={"name": "ROI contract", "zone_name": "프레스 구역",
                  "required_ppe": ["helmet"]})
        equipment.raise_for_status()
        self.api.put(root + "/equipment", headers=self.admin,
            json={"equipment_ids": [equipment.json()["equipment_id"]]}).raise_for_status()
        area = self.api.post(root + "/work-areas", headers=self.admin,
            json={"name": "ROI", "equipment_id": equipment.json()["equipment_id"],
                  "points": [[0.6, 0], [1, 0], [1, 1], [0.6, 1]]})
        area.raise_for_status()
        version = self.api.get(root + "/work-areas", headers=self.admin).json()["version"]
        self.api.put(root + "/work-area-config", headers=self.admin,
            json={"enabled": True, "expected_version": version}).raise_for_status()

        policy = self.client.get_ppe_policy("CAM_001")
        self.assertTrue(policy["configured"])
        self.assertEqual(policy["scope"], "work_area")
        outside = resolve_person_policy(policy, [10, 10, 30, 80], 100, 100)
        inside = resolve_person_policy(policy, [70, 10, 90, 80], 100, 100)
        self.assertEqual(outside["required_ppe"], [])
        self.assertEqual(outside["area_ids"], [])
        self.assertEqual(inside["required_ppe"], ["helmet"])
        self.assertEqual(inside["area_ids"], [area.json()["area_id"]])
        legacy = self.api.get("/api/processing/cameras/CAM_001/ppe-policy",
                              headers=self.client.client.headers).json()
        self.assertFalse(legacy["configured"])

        self.api.delete(root + "/work-areas/" + area.json()["area_id"],
            headers=self.admin, params={"expected_version": area.json()["version"]}).raise_for_status()
        invalid = self.client.get_ppe_policy("CAM_001")
        self.assertFalse(invalid["configured"])
        self.assertIn("work_areas_empty", invalid["configuration_issues"])
        with self.assertRaises(ValueError):
            resolve_person_policy(invalid, [70, 10, 90, 80], 100, 100)
        self.assertNotIn(("GET", "/api/processing/cameras/CAM_001/ppe-policy"),
                         self.requests)


    def test_reference_collector_attaches_peer_without_second_event(self):
        from datetime import timedelta
        from backend.db.event_repository import get_connection
        from inference_worker.reference_collector import ReferenceCollector
        self.api.post('/api/camera-groups', headers=self.admin,
            json={'name': 'reference-contract', 'camera_ids': ['CAM_001', 'CAM_002']}).raise_for_status()
        event = self.event()
        self.client.submit_event(event)
        requests = self.client.list_reference_requests()
        self.assertEqual(len(requests), 1)
        request = requests[0]
        start = datetime.fromisoformat(request['clip_start_at'])
        chunks = [{'chunk_id': str(index), 'capture_start_at': (start+timedelta(seconds=2*index)).isoformat(),
                   'duration_ms': 2000} for index in range(2)]
        roots = []
        def assembled(client, chosen, before, after, folder):
            roots.append(folder)
            path = folder/'reference.mp4'
            path.write_bytes((Path(__file__).resolve().parents[1]/'backend/db/demo_assets/sample.mp4').read_bytes())
            return path
        with get_connection() as conn:
            count = conn.execute('SELECT COUNT(*) FROM candidate_event').fetchone()[0]
        with patch.object(self.client, 'list_streams', return_value=[{'camera_id': 'CAM_002', 'stream_id': 'peer'}]), \
             patch.object(self.client, 'list_chunks', return_value=chunks), \
             patch('inference_worker.reference_collector.assemble', side_effect=assembled):
            ReferenceCollector(self.client).run_once()
        self.assertTrue(roots)
        self.assertTrue(all(not path.exists() for path in roots))
        self.assertEqual(self.client.list_reference_requests(), [])
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM candidate_event').fetchone()[0], count)
            row = conn.execute('SELECT camera_id,role,status,redaction_status FROM event_media WHERE request_id=?',
                               (request['request_id'],)).fetchone()
            self.assertEqual(row['camera_id'], 'CAM_002')
            self.assertEqual(row['role'], 'reference')
            self.assertEqual(row['status'], 'processing')
            self.assertNotEqual(row['redaction_status'], 'complete')
        media = self.api.get('/api/events/'+event['event_id']+'/media', headers=self.admin).json()['items']
        self.assertTrue(all(item['url'] is None for item in media))
        # A synthetic redaction result follows the real worker API publication path.
        import cv2
        import json
        from backend.media import paths
        target = paths.STORAGE_ROOT/'candidate_events'/'processed'
        self.enterContext(patch('backend.processing.routes.PROCESSED_ROOT', target))
        job = self.api.post('/api/processing/claim', headers=self.client.client.headers).json()['job']
        headers = {**dict(self.client.client.headers), 'X-Processing-Lease': job['lease_token']}
        payload = self.api.get(job['source_url'], headers=headers).content
        sample = Path(__file__).resolve().parents[1]/'backend/db/demo_assets/sample.mp4'
        cap = cv2.VideoCapture(str(sample))
        try:
            count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        finally:
            cap.release()
        meta = {'faces_detected': 0, 'processed_frames': count, 'redaction_mode': 'scrfd',
                'inference_backend': 'synthetic-no-model', 'processing_seconds': 0}
        response = self.api.post('/api/processing/jobs/'+job['job_id']+'/result', content=payload,
            headers={**headers, 'Content-Type': 'video/mp4', 'X-Processing-Metadata': json.dumps(meta)})
        self.assertEqual(response.status_code, 200, response.text)
        media = self.api.get('/api/events/'+event['event_id']+'/media', headers=self.admin).json()['items']
        self.assertEqual(media[0]['status'], 'ready')
        self.assertIsNotNone(media[0]['url'])
        self.assertEqual(media[0]['camera_id'], 'CAM_002')
        self.assertNotIn('source_path', media[0])
        # Polling again must not generate a second media row or event.
        ReferenceCollector(self.client).run_once()
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_media WHERE request_id=?', (request['request_id'],)).fetchone()[0], 1)


    def test_review_only_http_submission_warns_without_final_judgment(self):
        """3-class candidate -> queue -> field result; judgment stays pending."""
        from backend.db.event_repository import get_broadcast_settings, save_broadcast_settings
        from field_agent.main import execute
        self.api.put('/api/cameras/CAM_001/node', headers=self.admin,
            json={'source_node_id': 'inference-contract', 'output_node_id': 'inference-contract'}).raise_for_status()
        self.api.post('/api/field/heartbeat', headers=self.client.client.headers).raise_for_status()
        settings = get_broadcast_settings()
        settings.update(enabled=True, cooldown_sec=30, default_language='ko', templates=[
            {'ppe_type': 'helmet', 'zone_name': '', 'language': 'ko', 'message': '안전모 착용 상태를 확인해 주세요.'}])
        save_broadcast_settings(settings)
        event = self.event()
        event["review_only"] = True
        event["confidence"] = None

        with patch("field_agent.main.speak_message", return_value={'spoken': True}) as speak:
            # First HTTP submission
            first = self.client.submit_event(event)

            self.assertTrue(first["created"])
            self.assertTrue(first["review_only"])
            self.assertTrue(first["broadcast"]["queued"])

            # Retry must remain review-only.
            second = self.client.submit_event(event)

            self.assertFalse(second["created"])
            self.assertFalse(second["broadcast"]["queued"])

            # Changing the same event to normal submission is forbidden.
            changed = {
                **event,
                "review_only": False,
                "confidence": 0.9,
            }

            with self.assertRaises(httpx.HTTPStatusError) as error:
                self.client.submit_event(changed)

            self.assertEqual(error.exception.response.status_code, 409)

            self.assertEqual(second['broadcast']['reason'], 'already_requested')
            command = self.api.post('/api/field/commands/claim',
                headers=self.client.client.headers).json()['command']
            self.assertEqual(command['command_id'], first['broadcast']['command_id'])
            result = execute(command)
            self.assertEqual(result['status'], 'completed')
            speak.assert_called_once_with('안전모 착용 상태를 확인해 주세요.', 'ko', None)
            self.api.put('/api/field/commands/'+command['command_id']+'/result',
                headers=self.client.client.headers, json=result).raise_for_status()
            self.assertIsNone(self.api.post('/api/field/commands/claim',
                headers=self.client.client.headers).json()['command'])

        with get_connection() as conn:
            saved = conn.execute(
                "SELECT event_status FROM candidate_event WHERE event_id=?",
                (event["event_id"],),
            ).fetchone()

            mode = conn.execute(
                """
                SELECT review_only
                FROM processing_review_only_event
                WHERE event_id=?
                """,
                (event["event_id"],),
            ).fetchone()

        self.assertIsNotNone(saved)
        self.assertEqual(saved["event_status"], "pending")
        self.assertIsNotNone(mode)
        self.assertEqual(mode["review_only"], 1)

        with get_connection() as conn:
            event_count = conn.execute(
                "SELECT COUNT(*) FROM candidate_event WHERE event_id=?",
                (event["event_id"],),
            ).fetchone()[0]
            clip_request_count = conn.execute(
                "SELECT COUNT(*) FROM event_clip_request WHERE event_id=?",
                (event["event_id"],),
            ).fetchone()[0]
        self.assertEqual(event_count, 1)
        self.assertEqual(clip_request_count, 1)
        with get_connection() as conn:
            command_row = conn.execute('SELECT event_id,status FROM field_command WHERE command_id=?',
                (command['command_id'],)).fetchone()
        self.assertEqual(command_row['event_id'], event['event_id'])
        self.assertEqual(command_row['status'], 'completed')



