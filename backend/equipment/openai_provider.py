"""Paid vision adapter, selected explicitly; receives redacted JPEG only."""
import base64
import os
import re

import httpx

from backend.equipment.provider import ProviderError, ProviderResult
from backend.equipment.subscription_provider import PROMPT, parse_result, response_error


class OpenAIProvider:
    def analyze(self, redacted_jpeg, *, timeout_seconds):
        key = os.environ.get('PPE_EQUIPMENT_OPENAI_API_KEY', '').strip()
        model = os.environ.get('PPE_EQUIPMENT_MODEL', '').strip()
        if not key or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}', model):
            raise ProviderError('provider_unavailable')
        schema = ProviderResult.model_json_schema()
        schema['$defs']['Candidate']['required'] = list(schema['$defs']['Candidate']['properties'])
        payload = {'model': model, 'store': False, 'max_output_tokens': 1600,
                   'instructions': PROMPT,
                   'input': [{'role': 'user', 'content': [
                       {'type': 'input_text', 'text': 'Identify equipment candidates.'},
                       {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,' +
                        base64.b64encode(redacted_jpeg).decode('ascii')}]}],
                   'text': {'format': {'type': 'json_schema', 'name': 'equipment_candidates',
                                       'strict': True, 'schema': schema}}}
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=min(5, timeout_seconds)),
                              trust_env=False, follow_redirects=False) as client:
                response = client.post('https://api.openai.com/v1/responses',
                                       headers={'Authorization': 'Bearer ' + key}, json=payload)
        except httpx.TimeoutException:
            raise ProviderError('provider_timeout') from None
        except httpx.RequestError:
            raise ProviderError('provider_unavailable') from None
        if response.status_code != 200:
            raise response_error(response.status_code)
        try:
            if len(response.content) > 256 * 1024:
                raise ValueError()
            data = response.json()
            if data.get('status') != 'completed' or data.get('error'):
                raise ValueError()
            texts = [part['text'] for item in data['output']
                     if item.get('type') == 'message' and item.get('role') == 'assistant' and item.get('status') == 'completed'
                     for part in item['content'] if part.get('type') == 'output_text']
            if len(texts) != 1:
                raise ValueError()
            return parse_result(texts[0])
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ProviderError('provider_response_invalid') from None
