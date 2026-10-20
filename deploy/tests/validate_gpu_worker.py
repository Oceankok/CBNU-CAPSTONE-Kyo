"""Opt-in real GPU smoke check against disposable Docker API/PostgreSQL services."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy.snapshot import IMAGE, docker_path, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-image',required=True,help='Published API ghcr.io/...@sha256:... digest')
    args = parser.parse_args()
    if not IMAGE.fullmatch(args.api_image):
        raise ValueError('Published API digest required')
    import cv2
    import httpx
    import numpy as np
    import onnxruntime as ort
    from backend.processing.worker import Worker
    if 'CUDAExecutionProvider' not in ort.get_available_providers():
        raise RuntimeError('CUDA provider unavailable; this check never substitutes CPU')
    ident = uuid4().hex
    project = 'kyo-gpu-smoke-'+ident[:12]
    env = dict(os.environ)
    env['PATH'] = str(Path(docker_path()).parent)+os.pathsep+env.get('PATH','')
    env['KYO_SMOKE_DB_PASSWORD'] = secrets.token_hex(24)
    env['KYO_SMOKE_DATABASE_URL'] = 'postgresql://kyo:'+env['KYO_SMOKE_DB_PASSWORD']+'@postgres:5432/kyo'
    env['KYO_SMOKE_JWT_SECRET'] = secrets.token_hex(32)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    report = []
    with tempfile.TemporaryDirectory(prefix='kyo-gpu-smoke-') as temporary:
        folder = Path(temporary)
        config = folder/'compose.json'
        empty = folder/'empty.env'
        empty.write_text('')
        config.write_text(json.dumps({'services':{
            'postgres':{'image':'postgres:17','environment':{'POSTGRES_DB':'kyo','POSTGRES_USER':'kyo',
                'POSTGRES_PASSWORD':'${KYO_SMOKE_DB_PASSWORD}'},'volumes':['db:/var/lib/postgresql/data'],
                'healthcheck':{'test':['CMD-SHELL','pg_isready -U kyo -d kyo'],'interval':'2s','timeout':'2s','retries':30}},
            'api':{'image':args.api_image,'environment':{'PPE_DATABASE_URL':'${KYO_SMOKE_DATABASE_URL}',
                'PPE_JWT_SECRET':'${KYO_SMOKE_JWT_SECRET}','PPE_PROCESSING_MODE':'remote','PPE_LOCAL_PROCESSING':'false'},
                'volumes':['media:/app/storage','private:/app/.private_media'],
                'ports':[f'127.0.0.1:{port}:8000'],'depends_on':{'postgres':{'condition':'service_healthy'}}}},
            'volumes':{'db':{},'media':{},'private':{}}}),encoding='utf-8')
        base = ['compose','--project-name',project,'--env-file',str(empty),'-f',str(config)]
        def docker(arguments,**kwargs):
            return run(arguments,env=env,**kwargs)
        label = 'label=com.docker.compose.project='+project
        if docker(['ps','-aq','--filter',label]).strip() or docker(['volume','ls','-q','--filter',label]).strip():
            raise RuntimeError('Test project already exists')
        try:
            docker(base+['up','-d','--wait','--wait-timeout','120','postgres'])
            password = secrets.token_urlsafe(24)
            bootstrap = """
import os,sys
os.environ['PPE_DB_SEED']='true'
from backend.db.init_db import init_database
from backend.auth.service import create_user
init_database()
create_user('gpu_smoke_admin',sys.stdin.read(),'GPU smoke admin','admin')
"""
            # stdin avoids exposing the disposable password in command arguments.
            credential = folder/'bootstrap-input'
            credential.write_text(password)
            try:
                with credential.open('rb') as source:
                    docker(base+['run','--rm','--no-deps','-T','api','python','-c',bootstrap],stdin=source)
            finally:
                credential.unlink(missing_ok=True)
            docker(base+['up','-d','--wait','--wait-timeout','120','api'])
            origin = 'http://127.0.0.1:'+str(port)
            with httpx.Client(base_url=origin,trust_env=False,timeout=120) as client:
                login = client.post('/api/auth/login',json={'user_id':'gpu_smoke_admin','password':password})
                login.raise_for_status()
                client.headers['Authorization'] = 'Bearer '+login.json()['access_token']
                password = None
                node = client.post('/api/nodes',json={'node_id':'gpu_smoke','name':'Disposable GPU worker'})
                node.raise_for_status()
                token = node.json()['token']
                client.put('/api/processing/workers/gpu_smoke',json={'enabled':True}).raise_for_status()
                event = client.get('/api/events/EVT_0001')
                event.raise_for_status()
                camera_id = event.json()['event']['camera_id']
                frame = np.full((240,320,3),80,dtype=np.uint8)
                image = cv2.imencode('.jpg',frame)[1].tobytes()
                video = folder/'synthetic.mp4'
                writer = cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),12,(320,240))
                if not writer.isOpened():
                    raise RuntimeError('Synthetic video encoder unavailable')
                try:
                    for _ in range(12): writer.write(frame)
                finally:
                    writer.release()
                # The host worker uses HTTP only, never either PostgreSQL data directory.
                with patch('backend.processing.worker.WORK_ROOT',folder/'worker'):
                    worker = Worker(origin,token)
                token = None
                try:
                    for mode in ('enhanced','scrfd'):
                        for kind,payload in (('image',image),('video',video.read_bytes())):
                            request_id = None
                            if kind=='video':
                                request = worker.client.post('/api/processing/events/EVT_0001/clip-requests',json={'camera_id':camera_id})
                                request.raise_for_status()
                                request_id = request.json()['request_id']
                                upload = worker.client.post('/api/processing/events/EVT_0001/clip',
                                    params={'request_id':request_id,'camera_id':camera_id,'redaction_mode':mode},
                                    content=payload,headers={'Content-Type':'video/mp4'})
                            else:
                                upload = client.post('/api/events/EVT_0001/media',params={'kind':kind,'redaction_mode':mode},
                                                     content=payload,headers={'Content-Type':'image/jpeg'})
                            upload.raise_for_status()
                            queued = upload.json()
                            if upload.status_code!=201 or queued['url'] is not None or queued['processing_state']!='queued':
                                raise AssertionError('Source exposed before processing')
                            source_url = '/api/processing/jobs/'+queued['job_id']+'/source'
                            if client.get(source_url).status_code!=401 or httpx.get(origin+source_url,trust_env=False).status_code!=401:
                                raise AssertionError('Original source accessible without worker lease')
                            if not report:
                                worker.client.put('/api/processing/inference-load',json={'busy':True,'ttl_seconds':10}).raise_for_status()
                                if worker.run_once(): raise AssertionError('Busy inference did not defer queued processing')
                                worker.client.put('/api/processing/inference-load',json={'busy':False}).raise_for_status()
                            if not worker.run_once(): raise AssertionError('Queued GPU job not claimed')
                            response = client.get('/api/events/EVT_0001/media')
                            response.raise_for_status()
                            media = next(item for item in response.json()['items'] if item['media_id']==queued['media_id'])
                            if media['status']!='ready' or media['redaction_status']!='complete' or media['inference_backend']!='onnxruntime-cuda':
                                raise AssertionError('Real CUDA result not published: '+str(media.get('error_code')))
                            expected_frames = 1 if kind=='image' else 12
                            if media['processed_frames']!=expected_frames: raise AssertionError('Incomplete frame processing')
                            if not media['url'].startswith('/storage/'): raise AssertionError('Unexpected result URL')
                            published = client.get(media['url'])
                            published.raise_for_status()
                            if not published.content: raise AssertionError('Empty published result')
                            if kind=='image' and cv2.imdecode(np.frombuffer(published.content,dtype=np.uint8),cv2.IMREAD_COLOR) is None:
                                raise AssertionError('Undecodable published image')
                            if kind=='video':
                                requests = response.json()['clip_requests']
                                if not any(item['request_id']==request_id and item['status']=='received' for item in requests):
                                    raise AssertionError('Clip request not connected to received media')
                                result_path = folder/'published.mp4'
                                result_path.write_bytes(published.content)
                                capture = cv2.VideoCapture(str(result_path))
                                frames = 0
                                try:
                                    while True:
                                        success,_ = capture.read()
                                        if not success: break
                                        frames += 1
                                finally:
                                    capture.release()
                                if frames!=expected_frames: raise AssertionError('Published video frame loss')
                            if list(worker.root.iterdir()): raise AssertionError('Worker payload/journal cleanup incomplete')
                            # Only a server-side fixture check queries its own DB.
                            inspect = 'from backend.db.event_repository import get_connection; c=get_connection(); r=c.execute("SELECT status,local_cleanup_status FROM processing_job WHERE media_id=?",('+repr(queued['media_id'])+',)).fetchone(); assert r[0]=="succeeded" and r[1]=="confirmed"; c.close()'
                            docker(base+['exec','-T','api','python','-c',inspect])
                            report.append({'mode':mode,'kind':kind,'frames':expected_frames,'backend':media['inference_backend'],
                                           'processing_seconds':media['processing_seconds'],'result':'PASS'})
                            print('PASS:',mode,kind,'CUDA publish and local cleanup',flush=True)
                finally:
                    worker.client.close()
        finally:
            # Explicitly scoped project generated above; never target kyo-server.
            if project != 'kyo-gpu-smoke-'+ident[:12]: raise RuntimeError('Unsafe test cleanup')
            docker(base+['down','--volumes','--remove-orphans'])
    output = ROOT/'.private_media/gpu-worker-smoke-results.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({'result':'PASS','cases':report,'face_accuracy_evaluated':False,
                                 'production_changed':False},indent=2),encoding='utf-8')
    print('GPU WORKER SMOKE PASS: 4 real CUDA cases; disposable services cleaned',flush=True)


if __name__ == '__main__':
    main()
