"""Native host worker. GPU code is reused; no direct database access."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit

import httpx

from backend.media.paths import WORK_ROOT
from backend.media.redaction import MediaError, redact_image, redact_video

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, server, token):
        parsed = urlsplit(server)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('Set PPE_SERVER_URL to the API origin only')
        if parsed.scheme != 'https' and parsed.hostname not in {'localhost', '127.0.0.1'}:
            raise ValueError('Remote processing requires HTTPS')
        self.server = server.rstrip('/')
        self.client = httpx.Client(base_url=self.server, headers={'Authorization': 'Bearer ' + token},
                                   trust_env=False, follow_redirects=False, timeout=15)
        self.root = (WORK_ROOT / 'worker_jobs' / hashlib.sha256((self.server + token).encode()).hexdigest()[:24]).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def erase_payloads(self, folder):
        target = folder.resolve()
        if target.parent != self.root or not re.fullmatch(r'[0-9a-f-]{36}', target.name) or folder.is_symlink():
            raise ValueError('Unsafe worker folder')
        for item in folder.iterdir():
            if item.name != 'journal.json':
                if item.is_dir() or item.is_symlink():
                    raise ValueError('Unexpected worker payload')
                item.unlink()

    def recover_cleanup(self):
        for folder in self.root.iterdir():
            if not folder.is_dir() or folder.is_symlink() or not re.fullmatch(r'[0-9a-f-]{36}', folder.name):
                continue
            try:
                journal = json.loads((folder / 'journal.json').read_text(encoding='utf-8'))
                if journal.get('server') != self.server or journal.get('job_id') != folder.name:
                    continue
                self.erase_payloads(folder)
                prefix = '/api/processing/ai/jobs' if journal.get('kind') == 'ai' else '/api/processing/jobs'
                response = self.client.post(f'{prefix}/{folder.name}/cleanup')
                if response.status_code == 200:
                    (folder / 'journal.json').unlink()
                    folder.rmdir()
            except (OSError, ValueError, httpx.HTTPError):
                logger.warning('Worker cleanup deferred; will retry')

    def execute(self, job):
        ident = job['job_id']
        if not re.fullmatch(r'[0-9a-f-]{36}', ident):
            raise ValueError('Invalid job ID')
        folder = self.root / ident
        folder.mkdir(exist_ok=False)
        (folder / 'journal.json').write_text(json.dumps({'job_id': ident, 'server': self.server}), encoding='utf-8')
        headers = {'X-Processing-Lease': job['lease_token']}
        done, lost = threading.Event(), threading.Event()

        def renew():
            while not done.wait(30):
                try:
                    response = self.client.post(f'/api/processing/jobs/{ident}/lease', headers=headers)
                    if response.status_code != 200:
                        lost.set()
                        return
                except httpx.HTTPError:
                    lost.set()
                    return

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        last_check = [0.0]
        def yield_for_inference():
            if lost.is_set() or time.time() >= job['lease_until'] + 1800:
                raise MediaError('media_processing_failed')
            if time.monotonic() - last_check[0] < 0.25:
                return
            while True:
                response = self.client.get('/api/processing/inference-load')
                response.raise_for_status()
                last_check[0] = time.monotonic()
                if not response.json()['busy']:
                    return
                if lost.is_set():
                    raise MediaError('media_processing_failed')
                time.sleep(0.1)
        try:
            source = folder / ('source.jpg' if job['kind'] == 'image' else 'source.mp4')
            size = 0
            with self.client.stream('GET', f'/api/processing/jobs/{ident}/source', headers=headers) as response:
                response.raise_for_status()
                with source.open('wb') as file:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > int(os.environ.get('PPE_MEDIA_MAX_UPLOAD_BYTES', 100 * 1024 * 1024)):
                            raise MediaError('media_processing_failed')
                        file.write(chunk)
            output = folder / ('output.jpg' if job['kind'] == 'image' else 'output.mp4')
            from backend.processing.priority import inference_guard
            with inference_guard(yield_for_inference):
                details = redact_image(source, output, job['redaction_mode']) if job['kind'] == 'image' else \
                    redact_video(source, output, folder, job['redaction_mode'])
            if lost.is_set():
                raise MediaError('media_processing_failed')
            metadata = {key: details[key] for key in ('faces_detected', 'processed_frames', 'redaction_mode', 'inference_backend', 'processing_seconds')}
            with output.open('rb') as file:
                response = self.client.post(f'/api/processing/jobs/{ident}/result', content=file,
                    headers={**headers, 'X-Processing-Metadata': json.dumps(metadata),
                             'Content-Type': 'image/jpeg' if job['kind'] == 'image' else 'video/mp4'}, timeout=120)
                response.raise_for_status()
            logger.info('Media processing completed: %s', ident)
        except Exception as error:
            code = str(error) if isinstance(error, MediaError) else 'media_processing_failed'
            allowed = {'source_file_missing', 'source_expired', 'invalid_frame', 'video_decode_failed',
                       'incomplete_video_decode', 'scrfd_model_missing', 'face_model_missing', 'gpu_unavailable'}
            for _ in range(2):
                try:
                    self.client.post(f'/api/processing/jobs/{ident}/failure', headers=headers,
                                     json={'error_code': code if code in allowed else 'media_processing_failed'})
                    break
                except httpx.HTTPError:
                    continue
            logger.warning('Media processing failed: %s', ident)
        finally:
            done.set()
            thread.join(timeout=20)
            # Leave only the journal until the server confirms cleanup.
            self.erase_payloads(folder)
            self.recover_cleanup()

    def run_once(self):
        self.recover_cleanup()
        self.client.post('/api/field/heartbeat').raise_for_status()
        ai_enabled = os.environ.get('PPE_HOST_AI_ENABLED', 'false').lower() == 'true'
        if ai_enabled:
            from backend.processing.ai_worker import capabilities
            self.client.put('/api/processing/ai/capabilities', json={'capabilities': capabilities()}).raise_for_status()
        response = self.client.post('/api/processing/claim')
        response.raise_for_status()
        job = response.json()['job']
        if job:
            self.execute(job)
        elif ai_enabled:
            from backend.processing.ai_worker import execute
            response = self.client.post('/api/processing/ai/claim')
            response.raise_for_status()
            job = response.json()['job']
            if job:
                execute(self, job)
        return bool(job)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    token = os.environ.get('PPE_PROCESSING_NODE_TOKEN', '')
    if not token:
        raise SystemExit('Set PPE_PROCESSING_NODE_TOKEN')
    worker = Worker(os.environ.get('PPE_SERVER_URL', 'http://127.0.0.1:8000'), token)
    lock = worker.root / 'worker.lock'
    try:
        with lock.open('x') as file:
            file.write(str(os.getpid()))
    except FileExistsError:
        worker.client.close()
        raise SystemExit('Worker already running or stale worker.lock; verify process before removing lock')
    logging.basicConfig(level=logging.INFO)
    try:
        while True:
            try:
                worker.run_once()
            except httpx.HTTPError:
                logger.warning('Processing server unavailable; will retry')
            if args.once:
                break
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        worker.client.close()
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
