"""Opt-in private upload adapter. No runner registration or automatic delivery.

The caller must persist the exact event/request/file binding before calling.
One private verified snapshot (at most max_upload_bytes) is held during upload;
its disk reservation must be included in the coordinator global budget.
Only one owner may use this adapter at a time. Total deadline is checked between
I/O operations; a blocked operation may additionally consume its configured I/O
timeout. A process supervisor is required for hard cancellation and TTL.
An upload receipt means private server custody, never redaction/public readiness.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib
import ipaddress
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class UploadPlan:
    event_id: str
    request_id: str
    camera_id: str
    relative_path: str
    byte_count: int
    sha256: str
    capture_start: float
    capture_end: float
    required_start: float
    required_end: float
    final_range_verified: bool


class PrivateClipTransport:
    def __init__(self, *, server_url, token, private_root, max_upload_bytes,
                 timeout_seconds, max_response_bytes, total_seconds, transport=None):
        url = urlsplit(server_url)
        loopback = url.hostname == 'localhost'
        try:
            loopback = loopback or ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            pass
        if (url.scheme not in ('https', 'http') or (url.scheme == 'http' and not loopback)
                or url.username or url.password or url.query or url.fragment
                or url.path not in ('', '/') or not url.hostname):
            raise ValueError('private endpoint must be HTTPS or loopback without URL credentials')
        if not token or '\r' in token or '\n' in token:
            raise ValueError('token')
        if any(type(v) is not int or v <= 0 for v in (max_upload_bytes, max_response_bytes)):
            raise ValueError('byte limits')
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError('timeout')
        if not math.isfinite(total_seconds) or total_seconds <= 0:
            raise ValueError('total deadline')
        self.total_seconds = total_seconds
        root = Path(private_root)
        if root.is_symlink() or not root.is_dir():
            raise ValueError('private root')
        self.root = root.resolve()
        self.maximum, self.response_max = max_upload_bytes, max_response_bytes
        self.client = httpx.Client(base_url=server_url.rstrip('/'),
            headers={'Authorization': 'Bearer '+token, 'Accept-Encoding':'identity'}, timeout=timeout_seconds,
            trust_env=False,
            follow_redirects=False, transport=transport)

    def _open(self, plan, check_deadline):
        for value in (plan.event_id, plan.request_id, plan.camera_id):
            if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value):
                raise ValueError('invalid binding identifier')
        if (not plan.final_range_verified or not all(math.isfinite(v) for v in (
                plan.capture_start, plan.capture_end, plan.required_start, plan.required_end))
                or not datetime.fromtimestamp(plan.required_start,tz=timezone.utc) <= datetime.fromtimestamp(plan.capture_start,tz=timezone.utc) < datetime.fromtimestamp(plan.capture_end,tz=timezone.utc) <= datetime.fromtimestamp(plan.required_end,tz=timezone.utc)
                or plan.capture_end-plan.capture_start > 60):
            raise ValueError('unverified or out-of-range capture')
        if (type(plan.byte_count) is not int or not 0 < plan.byte_count <= self.maximum
                or not re.fullmatch('[a-f0-9]{64}',plan.sha256)):
            raise ValueError('invalid source identity')
        relative = Path(plan.relative_path)
        if relative.is_absolute() or not relative.parts or any(p in ('.','..') for p in relative.parts):
            raise ValueError('private relative path required')
        path = self.root
        for part in relative.parts:
            path = path/part
            if path.is_symlink():
                raise ValueError('symlink source forbidden')
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('source escapes private root')
        fd = os.open(path, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
        source = os.fdopen(fd,'rb')
        snapshot = None
        try:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != plan.byte_count:
                raise ValueError('source size/type changed')
            snapshot = tempfile.TemporaryFile(dir=self.root, mode='w+b')
            digest=hashlib.sha256(); copied=0
            while chunk:=source.read(65536):
                check_deadline()
                copied+=len(chunk)
                if copied>plan.byte_count:raise ValueError('source grew during snapshot')
                digest.update(chunk);snapshot.write(chunk)
            if copied!=plan.byte_count or digest.hexdigest()!=plan.sha256:
                raise ValueError('source digest changed')
            snapshot.seek(0)
            source.close()
            return snapshot
        except BaseException:
            if snapshot is not None:snapshot.close()
            source.close()
            raise

    def upload(self, plan):
        """No source deletion or ready assertion: caller journals verified receipt."""
        deadline=time.monotonic()+self.total_seconds
        def check_deadline():
            if time.monotonic()>deadline:raise TimeoutError('private upload total deadline')
        with self._open(plan, check_deadline) as source:
            digest=hashlib.sha256(); sent=0
            def chunks():
                nonlocal sent
                while chunk:=source.read(65536):
                    check_deadline()
                    sent+=len(chunk)
                    if sent>plan.byte_count:raise ValueError('source grew during upload')
                    digest.update(chunk)
                    yield chunk
                if sent!=plan.byte_count or digest.hexdigest()!=plan.sha256:
                    raise ValueError('source changed during upload')
            with self.client.stream('POST',f'/api/processing/events/{plan.event_id}/clip',
                    params={'request_id':plan.request_id,'camera_id':plan.camera_id},
                    headers={'Content-Type':'video/mp4','Content-Length':str(plan.byte_count)},
                    content=chunks()) as response:
                check_deadline()
                if response.status_code not in (200,201):
                    # Do not include server bodies, credentials or private paths in errors.
                    raise RuntimeError('private_upload_http_'+str(response.status_code))
                if response.headers.get('content-encoding','identity').lower() != 'identity':
                    raise ValueError('compressed upload receipt forbidden')
                payload=bytearray()
                for chunk in response.iter_bytes(chunk_size=min(65536,self.response_max+1)):
                    check_deadline()
                    if len(payload)+len(chunk)>self.response_max:raise ValueError('upload response too large')
                    payload.extend(chunk)
                import json
                body=json.loads(payload)
                if (not isinstance(body,dict) or not body.get('media_id')
                        or body.get('event_id') != plan.event_id
                        or body.get('camera_id') != plan.camera_id
                        or body.get('clip_request_id') != plan.request_id
                        or body.get('status') not in ('processing','ready')
                        or (body.get('status') == 'ready' and body.get('redaction_status') != 'complete')):
                    raise ValueError('upload receipt binding invalid')
                return body

    def recover_receipt(self, plan, *, session_id, role, expected_media_id=None):
        """Historical byte acceptance only. None means unknown, never retry/delete.

        Does not open/delete a local source, extend TTL or assert redaction.
        Server enforces node ownership and session; all content bindings are
        checked here even when the original upload response was lost.
        """
        if not re.fullmatch('[a-f0-9]{32}',session_id) or role not in ('clip','reference'):
            raise ValueError('receipt owner session or role')
        for value in (plan.event_id,plan.request_id,plan.camera_id):
            if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}',value):
                raise ValueError('receipt identifier')
        if (type(plan.byte_count) is not int or plan.byte_count<=0
                or not re.fullmatch('[a-f0-9]{64}',plan.sha256)):
            raise ValueError('receipt source identity')
        deadline=time.monotonic()+self.total_seconds
        with self.client.stream('GET',
                f'/api/processing/continuous-events/{plan.event_id}/receipts/{plan.request_id}',
                params={'session_id':session_id}) as response:
            if response.status_code==404:return None
            if response.status_code!=200:
                raise RuntimeError('private_receipt_http_'+str(response.status_code))
            if response.headers.get('content-encoding','identity').lower()!='identity':
                raise ValueError('compressed receipt forbidden')
            payload=bytearray()
            for chunk in response.iter_bytes(chunk_size=min(65536,self.response_max+1)):
                if time.monotonic()>deadline:raise TimeoutError('receipt total deadline')
                if len(payload)+len(chunk)>self.response_max:raise ValueError('receipt too large')
                payload.extend(chunk)
            import json
            body=json.loads(payload)
            if (not isinstance(body,dict) or body.get('accepted') is not True
                    or body.get('event_id')!=plan.event_id
                    or body.get('request_id')!=plan.request_id
                    or body.get('camera_id')!=plan.camera_id or body.get('role')!=role
                    or body.get('source_sha256')!=plan.sha256
                    or type(body.get('source_bytes')) is not int
                    or body['source_bytes']!=plan.byte_count
                    or not isinstance(body.get('media_id'),str) or not body['media_id']
                    or (expected_media_id is not None and body['media_id']!=expected_media_id)
                    or body.get('source_state') not in ('available','source_expired',
                        'retention_expired','deleted','unavailable')):
                raise ValueError('historical receipt binding invalid')
            return body

    def close(self):
        self.client.close()
