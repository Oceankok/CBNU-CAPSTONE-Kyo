"""Shared HTTP-only journey for disposable TestClient and Docker environments.

Synthetic, person-free media is echoed as a worker result. This verifies protocol,
permissions and lifecycle, never face accuracy, PPE inference or speaker output.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import io
import json
from pathlib import Path
from uuid import uuid4

import cv2
from pypdf import PdfWriter


def request(client, method, path, expected=200, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != expected:
        raise AssertionError(f'{method} {path}: expected {expected}, got {response.status_code}')
    return response


def run_scenario(client_factory, password, admin_id='admin02', worker_id='worker01', sender_probe=None):
    sample = Path(__file__).resolve().parents[1]/'db/demo_assets/sample.mp4'
    video = sample.read_bytes()
    capture = cv2.VideoCapture(str(sample))
    frames = 0
    try:
        while capture.read()[0]:
            frames += 1
    finally:
        capture.release()
    assert frames > 0
    with ExitStack() as stack:
        admin, worker, source, processor, anonymous = [stack.enter_context(client_factory()) for _ in range(5)]
        for client, ident in ((admin, admin_id), (worker, worker_id)):
            login = request(client, 'POST', '/api/auth/login', json={'user_id': ident, 'password': password}).json()
            client.headers['Authorization'] = 'Bearer '+login['access_token']
        request(anonymous, 'GET', '/api/events', 401)
        request(worker, 'GET', '/api/events', 403)
        zone = 'INTEGRATION_ZONE'
        request(admin, 'PUT', '/api/zones/'+zone, json={'required_ppe': ['helmet'], 'rules': ['통합 검증 전용']})
        request(admin, 'PUT', '/api/zones/'+zone+'/members', json={'user_ids': [worker_id]})
        for client, ident in ((source, 'integration-source'), (processor, 'integration-processor')):
            node = request(admin, 'POST', '/api/nodes', 201, json={'node_id': ident, 'name': ident}).json()
            client.headers['Authorization'] = 'Bearer '+node['token']
            request(client, 'POST', '/api/field/heartbeat')
        request(admin, 'PUT', '/api/processing/workers/integration-processor', json={'enabled': True})
        for camera in ('INTEGRATION_A', 'INTEGRATION_B'):
            request(admin, 'POST', '/api/cameras', 201, json={'camera_id': camera, 'zone_name': zone})
            request(admin, 'PUT', '/api/cameras/'+camera+'/node', json={'source_node_id': 'integration-source', 'output_node_id': 'integration-source'})
            request(admin, 'PUT', '/api/cameras/'+camera+'/equipment', json={'equipment_ids': []})
            policy = request(processor, 'GET', '/api/processing/cameras/'+camera+'/ppe-policy').json()
            assert policy['configured'] and policy['required_ppe'] == ['helmet']
        request(admin, 'POST', '/api/camera-groups', 201, json={'name': '통합 검증 그룹', 'camera_ids': ['INTEGRATION_A', 'INTEGRATION_B']})
        request(admin, 'PUT', '/api/broadcast/settings', json={'enabled': True, 'default_language': 'ko', 'cooldown_sec': 0,
                'templates': [{'template_id': 'INTEGRATION_HELMET', 'ppe_type': 'helmet', 'zone_name': '', 'language': 'ko', 'message': '통합 검증용 방송 문구'}]})
        captured = datetime.now(timezone.utc).isoformat()
        if sender_probe:
            stream = sender_probe(source.headers['Authorization'].removeprefix('Bearer '), video, captured)
            chunk = request(processor, 'GET', f'/api/processing/video/streams/{stream}/chunks').json()['items'][0]
        else:
            stream = request(source, 'POST', '/api/video/streams', 201, json={'camera_id': 'INTEGRATION_A'}).json()['stream_id']
            chunk = request(source, 'POST', f'/api/video/streams/{stream}/chunks?sequence=0', 202,
                            headers={'Content-Type': 'video/mp4', 'X-Capture-Start': captured, 'X-Duration-Ms': '1000'}, content=video).json()
        chunk_url = '/api/processing/video/chunks/'+chunk['chunk_id']
        assert request(processor, 'GET', chunk_url).content == video
        request(source, 'GET', chunk_url, 403)
        request(anonymous, 'GET', chunk_url, 401)
        events, media_ids, urls = [], [], []
        for camera in ('INTEGRATION_A', 'INTEGRATION_B'):
            body = {'event_id': 'EVT_'+uuid4().hex, 'camera_id': camera, 'tracking_id': 'local-fixture', 'ppe_type': 'helmet',
                    'timestamp_start': captured, 'timestamp_end': captured, 'confidence': 0.9, 'frame_sample_count': 1, 'model_version': 'integration-stub'}
            event = request(processor, 'POST', '/api/processing/events', 201, json=body).json()
            assert event['created'] and len(event['clip_requests']) == 2
            duplicate = request(processor, 'POST', '/api/processing/events', json=body).json()
            assert not duplicate['created']
            assert {row['request_id'] for row in event['clip_requests']} == {row['request_id'] for row in duplicate['clip_requests']}
            events.append(body['event_id'])
            command = request(source, 'POST', '/api/field/commands/claim').json()['command']
            assert command['kind'] == 'broadcast'
            request(source, 'PUT', '/api/field/commands/'+command['command_id']+'/result', json={'status': 'completed', 'result': {'integration_stub': True}})
            assert request(source, 'POST', '/api/field/commands/claim').json()['command'] is None
            for clip in event['clip_requests']:
                queued = request(processor, 'POST', '/api/processing/events/'+body['event_id']+'/clip?request_id='+clip['request_id'], 201,
                                 headers={'Content-Type': 'video/mp4'}, content=video).json()
                assert queued['url'] is None
                job = request(processor, 'POST', '/api/processing/claim').json()['job']
                assert job and job['media_id'] == queued['media_id']
                lease = {'X-Processing-Lease': job['lease_token']}
                source_url = '/api/processing/jobs/'+job['job_id']+'/source'
                request(admin, 'GET', source_url, 401)
                assert request(processor, 'GET', source_url, headers=lease).content == video
                metadata = {'faces_detected': 0, 'processed_frames': frames, 'redaction_mode': 'scrfd', 'inference_backend': 'integration-stub', 'processing_seconds': 0}
                request(processor, 'POST', '/api/processing/jobs/'+job['job_id']+'/result', content=video,
                        headers={**lease, 'Content-Type': 'video/mp4', 'X-Processing-Metadata': json.dumps(metadata)})
                request(processor, 'POST', '/api/processing/jobs/'+job['job_id']+'/cleanup')
                media_ids.append(queued['media_id'])
        root, original = events
        request(admin, 'POST', '/api/events/'+root+'/merge', json={'source_event_ids': [original], 'expected_version': 0, 'reason': '합성 자료 통합 검증'})
        detail = request(admin, 'GET', '/api/events/'+root).json()['event']
        assert {row['media_id'] for row in detail['media']} == set(media_ids)
        for media in detail['media']:
            assert media['redaction_status'] == 'complete' and media['url']
            urls.append(media['url'])
            assert request(admin, 'GET', media['url']).content == video
            request(worker, 'GET', media['url'], 403)
            request(anonymous, 'GET', media['url'], 401)
        request(admin, 'POST', '/api/events/'+root+'/review', json={'review_result': 'confirmed', 'review_reason_code': 'confirmed_no_helmet'})
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        pdf = io.BytesIO()
        writer.write(pdf)
        consent = request(admin, 'POST', '/api/consent-records', 201,
                          data={'metadata': json.dumps({'site_code': 'INTEGRATION', 'scope_description': '통합 검증용 합성 자료', 'form_version': 'TEST', 'consent_confirmed': True})},
                          files={'file': ('fixture.pdf', pdf.getvalue(), 'application/pdf')}).json()
        record_id = consent['record_id']
        retained = request(admin, 'PUT', '/api/events/'+root+'/retention', json={'decision': 'retain', 'expected_version': 0,
                           'consent_confirmed': True, 'consent_record_id': record_id, 'retain_until': '2099-01-01T00:00:00Z'}).json()
        assert retained['consent_reference'] == record_id
        quarter = f'{datetime.now(timezone.utc).year}-Q{(datetime.now(timezone.utc).month-1)//3+1}'
        request(admin, 'POST', '/api/stats/generate?quarter='+quarter)
        recommendations = request(admin, 'POST', '/api/recommendations/generate?quarter='+quarter).json()['items']
        recommendation = next(item for item in recommendations if item['zone_name'] == zone)
        material = request(admin, 'POST', '/api/recommendations/'+recommendation['recommendation_id']+'/materials', 201,
                           json={'title': '통합 검증 자료', 'body': '실제 안전 지침이 아닌 테스트 본문입니다.', 'checklist': ['테스트 확인']}).json()
        assert all(item['material'] is None for item in request(worker, 'GET', '/api/worker/education').json()['items'])
        request(admin, 'POST', '/api/education/materials/'+material['material_id']+'/publish', json={'expected_version': 0, 'approved': True})
        education = request(worker, 'GET', '/api/worker/education').json()['items']
        assert any('통합 검증 자료' in (item['material'] or '') for item in education)
        assert all('confirmed_count' not in item and 'priority_score' not in item for item in education)
        request(admin, 'POST', '/api/consent-records/'+record_id+'/revoke', json={'expected_version': 0, 'reason': '통합 검증 종료'})
        deleted = request(admin, 'GET', '/api/events/'+root+'/media').json()
        assert deleted['retention']['decision'] == 'delete'
        assert all(item['url'] is None and item['status'] == 'deleted' for item in deleted['items'])
        for url in urls:
            request(admin, 'GET', url, 404)
        request(admin, 'GET', '/api/consent-records/'+record_id+'/file')
        request(worker, 'GET', '/api/consent-records/'+record_id+'/file', 403)
        request(admin, 'POST', '/api/auth/logout')
        request(admin, 'GET', '/api/auth/me', 401)
        return {'result': 'PASS', 'media_count': len(media_ids), 'merged_events': 2,
                'real_inference': False, 'real_speaker': False,
                'cases': ['auth_roles', 'camera_node_group', 'ppe_policy', 'video_ingest', 'event_idempotency', 'broadcast_roundtrip',
                          'clip_worker_protocol', 'private_source', 'manual_merge', 'review', 'consent_retain_revoke', 'education_publish', 'logout']}
