"""AI execution by the existing native worker; no container credentials or DB access."""
import base64
import json
import logging
import os
import time

import httpx

from backend.education import provider as education
from backend.equipment import provider as equipment
from backend.media.redaction import redact_image

logger = logging.getLogger(__name__)


def capabilities():
    result = {}
    selected = os.environ.get('PPE_EQUIPMENT_PROVIDER', '').strip()
    if selected in {'codex_chatgpt', 'chatgpt_oauth', 'openai'}:
        result['equipment'] = selected
    try:
        result['education'] = education.settings().provider
    except education.GenerationError:
        pass
    return result


def execute(worker, job):
    from uuid import UUID
    ident = job['job_id']
    if str(UUID(ident)) != ident:
        raise ValueError('Invalid AI job ID')
    folder = worker.root / ident
    folder.mkdir(exist_ok=False)
    (folder / 'journal.json').write_text(json.dumps({'server': worker.server, 'job_id': ident, 'kind': 'ai'}), encoding='utf-8')
    headers = {'X-Processing-Lease': job['lease_token']}
    base = '/api/processing/ai/jobs/' + ident
    stage = 'provider'
    try:
        if capabilities().get(job['kind']) != job['provider']:
            raise equipment.ProviderError('provider_unavailable')
        if job['deadline'] - time.time() < 5:
            raise equipment.ProviderError('provider_timeout')
        if job['kind'] == 'equipment':
            stage = 'redaction'
            source, output = folder / 'source.image', folder / 'redacted.jpg'
            size = 0
            with worker.client.stream('GET', base + '/source', headers=headers) as response:
                response.raise_for_status()
                with source.open('xb') as file:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 10 * 1024 * 1024:
                            raise ValueError('Oversized source')
                        file.write(chunk)
            def check_inference():
                while True:
                    if time.time() >= job['deadline'] - 3:
                        raise equipment.ProviderError('provider_timeout')
                    response = worker.client.get('/api/processing/inference-load')
                    response.raise_for_status()
                    if not response.json()['busy']:
                        return
                    time.sleep(0.2)
            from backend.processing.priority import inference_guard
            with inference_guard(check_inference):
                check_inference()
                redact_image(source, output, 'scrfd')
            redacted = output.read_bytes()
            stage = 'provider'
            remaining = job['deadline'] - time.time() - 3
            if remaining <= 0:
                raise equipment.ProviderError('provider_timeout')
            result = equipment.get_provider().analyze(redacted, timeout_seconds=min(60, remaining))
            payload = {'analysis': result.model_dump(), 'redacted_jpeg': base64.b64encode(redacted).decode('ascii')}
        else:
            content, model = education.generate(job['input'], timeout_seconds=min(60, job['deadline']-time.time()-3))
            payload = {'content': content.model_dump(), 'model': model}
        response = worker.client.post(base + '/result', headers=headers, json=payload, timeout=20)
        response.raise_for_status()
    except Exception as exc:
        if isinstance(exc, (equipment.ProviderError, education.GenerationError)):
            code = exc.code
        else:
            code = 'redaction_failed' if stage == 'redaction' else 'provider_unavailable'
        try:
            worker.client.post(base + '/failure', headers=headers, json={'error_code': code}).raise_for_status()
        except httpx.HTTPError:
            logger.warning('Host AI failure acknowledgement deferred')
        logger.warning('Host AI request failed: %s', ident)
    finally:
        worker.erase_payloads(folder)
        worker.recover_cleanup()
