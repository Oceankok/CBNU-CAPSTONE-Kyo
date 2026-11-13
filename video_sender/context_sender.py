"""Explicit, fail-closed capture-session runner. Not called by legacy launchers.

Use a new private session directory. Credentials remain in the environment.
A network/validation failure never discards an unacknowledged clip or declares
capture stopped. Resume drains only a run with completed capture proof; it never
silently restarts a possibly still-recording camera.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4, UUID

import requests

from video_sender.capture_control import stop_requested
from video_sender.backpressure import Policy, retry_upload
from video_sender.chunks import completed_chunks
from video_sender.live_sender import get_video_duration_ms, delete_chunk_files


def atomic_json(path, value):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as f:
        json.dump(value,f);f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)


@contextmanager
def runner_lock(directory):
    """OS lock is released on crash; no PID/timeout is treated as stop proof."""
    path=Path(directory)/'.runner.lock'
    with path.open('a+b') as f:
        if f.tell()==0:f.write(b'0');f.flush()
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)


class ContextClient:
    def __init__(self, origin, token, *, session=None):
        url=urlsplit(origin)
        if (url.scheme not in ('http','https') or not url.hostname or url.username or url.password or
            url.query or url.fragment or url.path not in ('','/') or
            (url.scheme=='http' and url.hostname not in ('localhost','127.0.0.1','::1'))):
            raise ValueError('https_server_origin_required')
        if not token: raise ValueError('node_token_required')
        self.policy=Policy.from_env();self.guard=lambda: None
        self.origin=origin.rstrip('/');self.http=session or requests.Session()
        self.http.headers.update({'Authorization':f'Bearer {token}'})

    def post(self,path,body):
        r=self.http.post(self.origin+path,json=body,timeout=20,allow_redirects=False)
        if 300 <= r.status_code < 400: raise ValueError('redirect_forbidden')
        r.raise_for_status();return r.json()

    def upload(self,stream_id,sequence,path,capture_start):
        duration=get_video_duration_ms(path)
        def attempt(timeout):
            with path.open('rb') as video:
                return self.http.post(self.origin+f'/api/video/streams/{stream_id}/chunks',
                    params={'sequence':sequence},data=video,timeout=timeout,allow_redirects=False,
                    headers={'Content-Type':'video/mp4','X-Capture-Start':capture_start,'X-Duration-Ms':str(duration)})
        r=retry_upload(attempt,policy=self.policy,guard=self.guard)
        if 300 <= r.status_code < 400: raise ValueError('redirect_forbidden')
        r.raise_for_status();data=r.json()
        if data.get('received') is not True or type(data.get('sequence')) is not int or data.get('sequence') != sequence:
            raise ValueError('upload_acknowledgement_mismatch')


class SessionRunner:
    def __init__(self, directory, client):
        self.policy=Policy.from_env()
        self.root=Path(directory).resolve();self.client=client
        if self.policy is not None and isinstance(client,ContextClient):client.guard=self.check_spool
        self.path=self.root/'session.json'
        self.state=json.loads(self.path.read_text(encoding='utf-8'))
        if self.state.get('server_origin') != client.origin:raise ValueError('session_server_origin_mismatch')
        if (type(self.state.get('acknowledged_sequence')) is not int or self.state['acknowledged_sequence'] < -1 or
            type(self.state.get('capture_started')) is not bool or type(self.state.get('closed')) is not bool):
            raise ValueError('invalid_state_values')
        for name in ('session_id','stream_id','run_id'):
            if str(UUID(self.state[name])) != self.state[name]:raise ValueError('invalid_state_identifier')

    @classmethod
    def create(cls,directory,client,camera_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',camera_id):raise ValueError('invalid_camera')
        root=Path(directory);root.mkdir(parents=True,exist_ok=False)
        atomic_json(root/'session.json',{'server_origin':client.origin,'camera_id':camera_id,'session_id':str(uuid4()),
            'stream_id':str(uuid4()),'run_id':str(uuid4()),'capture_started':False,
            'context_id':None,'acknowledged_sequence':-1,'closed':False})
        return cls(root,client)

    def save(self):atomic_json(self.path,self.state)

    def connect(self):
        s=self.state
        result=self.client.post('/api/video/capture-sessions',{'camera_id':s['camera_id'],'session_id':s['session_id']})
        if result.get('camera_id')!=s['camera_id'] or result.get('session_id')!=s['session_id']:
            raise ValueError('capture_context_binding_mismatch')
        context=str(UUID(result['context_id']))
        if s['context_id'] not in (None,context):raise ValueError('capture_context_changed')
        s['context_id']=context;self.save()
        stream=self.client.post('/api/video/streams',{'camera_id':s['camera_id'],
            'capture_context_id':context,'stream_id':s['stream_id']})
        if stream.get('stream_id')!=s['stream_id']:raise ValueError('stream_binding_mismatch')

    def request_stop(self):
        (self.root/f".stop-{self.state['run_id']}").touch(exist_ok=True)

    def check_spool(self):
        if self.policy is None:return None
        reason=self.policy.reason(self.root)
        if reason:
            self.request_stop()
            if self.state.get('protective_stop_reason')!=reason:
                self.state['protective_stop_reason']=reason;self.save()
        # An explicit stop does not cancel draining already captured evidence.
        return reason or ('stop_requested' if stop_requested(self.root,self.state['run_id']) else None)

    def drain(self):
        self.check_spool()
        for path in completed_chunks(self.root):
            self.check_spool()
            n=int(path.stem.removeprefix('chunk_'))
            ack=self.state['acknowledged_sequence']
            if n <= ack:
                if not delete_chunk_files(path):raise OSError('acknowledged_clip_cleanup_pending')
                continue
            if n!=ack+1:raise ValueError('outbox_sequence_gap')
            captured=path.with_suffix('.txt').read_text(encoding='utf-8').strip()
            self.client.upload(self.state['stream_id'],n,path,captured)
            self.state['acknowledged_sequence']=n;self.save()
            if not delete_chunk_files(path):raise OSError('acknowledged_clip_cleanup_pending')
        # Resume a crash during cleanup, using persisted server ack only.
        for marker in self.root.glob('chunk_*.cleanup'):
            n=int(marker.stem.removeprefix('chunk_'))
            if n>self.state['acknowledged_sequence']:raise ValueError('unacknowledged_cleanup_marker')
            if not delete_chunk_files(marker.with_suffix('.mp4')):raise OSError('cleanup_pending')

    def finish(self):
        s=self.state
        proof=json.loads((self.root/f".capture-result-{s['run_id']}.json").read_text(encoding='utf-8'))
        if (proof.get('run_id')!=s['run_id'] or proof.get('capture_stopped') is not True or
            proof.get('ffmpeg_exit')!=0 or proof.get('start_sequence')!=0 or
            type(proof.get('final_sequence')) is not int):
            raise ValueError('capture_completion_unverified')
        reason=proof.get('protective_stop_reason')
        if reason in ('spool_soft_stop_threshold','spool_free_space_reserve'):
            s['protective_stop_reason']=reason;self.save()
        self.drain()
        if s['acknowledged_sequence']!=proof['final_sequence']:
            raise ValueError('final_outbox_acknowledgement_missing')
        if any(self.root.glob('chunk_*')):raise ValueError('outbox_not_empty')
        result=self.client.post(f"/api/video/capture-sessions/{s['context_id']}/stop",{
            'capture_stopped':True,'final_sequences':{s['stream_id']:s['acknowledged_sequence']}})
        # Backend response contract: retired=true after persisted stop.
        if result.get('retired') is not True:raise ValueError('stop_acknowledgement_missing')
        s['closed']=True;self.save()

    def run_capture(self,camera_index=0):
        if self.state['capture_started']:raise ValueError('previous_capture_requires_explicit_recovery')
        if self.check_spool():raise RuntimeError('capture_start_blocked_by_spool_protection')
        self.connect()
        env=os.environ.copy();env.pop('VIDEO_NODE_TOKEN',None)
        env.update(VIDEO_OUTPUT_DIR=str(self.root),VIDEO_CAMERA_INDEX=str(camera_index),VIDEO_CAPTURE_RUN_ID=self.state['run_id'])
        self.state['capture_started']=True;self.save()
        options={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
        child=subprocess.Popen([sys.executable,str(Path(__file__).with_name('capture.py'))],env=env,stdin=subprocess.DEVNULL,**options)
        error=None
        try:
            while child.poll() is None:
                self.drain();time.sleep(.2)
        except (KeyboardInterrupt, Exception) as exc:
            error=exc;self.request_stop()
        finally:
            # A hung capture is not proof of stop; do not terminate+acknowledge it.
            try:child.wait(timeout=30)
            except subprocess.TimeoutExpired:raise RuntimeError('capture_stop_unconfirmed_context_kept_open')
        if error is not None and not isinstance(error,KeyboardInterrupt):raise error
        if child.returncode!=0:raise RuntimeError('capture_process_failed_context_kept_open')
        self.finish()


PRE_SESSION_FAILURE_EXIT = 78  # No server connection/capture attempted; new mode only.


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',required=True);p.add_argument('--camera-index',type=int,default=0)
    mode=p.add_mutually_exclusive_group()
    mode.add_argument('--resume-drain',action='store_true')
    mode.add_argument('--resume-start',action='store_true')
    a=p.parse_args()
    # A dedicated exit code is proof of this narrow local preparation failure,
    # not an inference from a missing/deleted journal after an arbitrary crash.
    if not a.resume_drain and not a.resume_start:
        try:
            client=ContextClient(os.environ['VIDEO_SERVER_URL'],os.environ['VIDEO_NODE_TOKEN'])
            runner=SessionRunner.create(a.directory,client,os.environ['VIDEO_CAMERA_ID'])
        except Exception:
            return PRE_SESSION_FAILURE_EXIT
        with runner_lock(runner.root):
            runner.run_capture(a.camera_index)
        return 0
    client=ContextClient(os.environ['VIDEO_SERVER_URL'],os.environ['VIDEO_NODE_TOKEN'])
    if a.resume_drain:
        runner=SessionRunner(a.directory,client)
        if runner.state['closed']:return
        with runner_lock(runner.root):
            runner.finish()  # no camera restart and no discarded outbox
    else:
        runner=(SessionRunner(a.directory,client) if a.resume_start else
                SessionRunner.create(a.directory,client,os.environ['VIDEO_CAMERA_ID']))
        with runner_lock(runner.root):
            runner.run_capture(a.camera_index)


if __name__=='__main__':raise SystemExit(main())
