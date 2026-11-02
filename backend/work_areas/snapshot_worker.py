"""Use the existing GPU redactor on one frame; never publish a raw fallback."""
import json
import re
import time

from backend.media.redaction import MediaError, redact_frame
from backend.processing.priority import inference_guard


def execute(worker, job):
    import cv2
    ident = job['job_id']
    if not re.fullmatch(r'[0-9a-f-]{36}', ident):
        raise ValueError('Invalid snapshot ID')
    folder = worker.root / ident
    folder.mkdir(exist_ok=False)
    (folder / 'journal.json').write_text(json.dumps({'job_id': ident, 'server': worker.server, 'kind': 'snapshot'}), encoding='utf-8')
    headers = {'X-Processing-Lease': job['lease_token']}
    prefix = '/api/processing/snapshots/' + ident

    def yield_for_inference():
        while time.time() < job['lease_until']:
            response = worker.client.get('/api/processing/inference-load')
            response.raise_for_status()
            if not response.json()['busy']:
                return
            time.sleep(0.1)
        raise MediaError('snapshot_processing_failed')

    try:
        source = folder / 'source.mp4'
        size = 0
        with worker.client.stream('GET', prefix+'/source', headers=headers) as response:
            response.raise_for_status()
            with source.open('wb') as file:
                for data in response.iter_bytes():
                    size += len(data)
                    if size > 8*1024*1024:
                        raise MediaError('snapshot_processing_failed')
                    file.write(data)
        cap = cv2.VideoCapture(str(source))
        try:
            ok, frame = cap.read()
        finally:
            cap.release()
        if not ok or frame is None or frame.shape[0]*frame.shape[1] > 1920*1080:
            raise MediaError('snapshot_processing_failed')
        output = folder / 'redacted.jpg'
        with inference_guard(yield_for_inference):
            redact_frame(frame, output, 'scrfd')
        with output.open('rb') as file:
            worker.client.post(prefix+'/result', headers={**headers, 'Content-Type': 'image/jpeg'}, content=file).raise_for_status()
    except Exception as error:
        code = str(error) if isinstance(error, MediaError) and str(error) in ('scrfd_model_missing', 'gpu_unavailable') else 'snapshot_processing_failed'
        try:
            worker.client.post(prefix+'/failure', headers=headers, json={'error_code': code}).raise_for_status()
        except Exception:
            pass  # Journal + lease expiry make cleanup retryable after disconnection.
    finally:
        worker.erase_payloads(folder)
        worker.recover_cleanup()
