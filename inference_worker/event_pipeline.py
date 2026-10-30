
"""Coordinate event submission and requested clip delivery.

This module performs HTTP writes when called with a real VideoClient.
Use a fake client for offline tests.
"""

from pathlib import Path
import time

import httpx

from inference_worker.event_sender import EventSender


class ClipDeliveryError(RuntimeError):
    """Delivery could not be confirmed; the inference loop may continue."""


def _recover_delivery(client, event, request_id):
    current = _retry(lambda: client.get_clip_requests(event['event_id']))
    row = next((item for item in current['items']
                if item['request_id'] == request_id
                and item['camera_id'] == event['camera_id']), None)
    if row is None:
        raise ClipDeliveryError('clip_request_missing_after_upload')
    if row['status'] == 'received':
        return {'request_id': request_id, 'status': 'received'}
    if row['status'] == 'pending':
        # The caller discards this local clip. Do not leave a fictitious retry pending.
        try:
            _retry(lambda: client.report_clip_failure(event['event_id'], request_id,
                'sender_upload_exhausted', status='unavailable'))
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 409:
                raise
            # The original upload may have committed while failure was reported.
            latest = _retry(lambda: client.get_clip_requests(event['event_id']))
            if any(item['request_id'] == request_id and item['camera_id'] == event['camera_id']
                   and item['status'] == 'received' for item in latest['items']):
                return {'request_id': request_id, 'status': 'received'}
            raise
        row = {**row, 'status': 'unavailable'}
    # Preserve ingest failures: the server may still hold an original for reprocessing.
    raise ClipDeliveryError('clip_delivery_' + row['status'])


def _retry(operation):
    # Retry only transient HTTP failures. Never rebuild the event on retry.
    for attempt in range(3):
        try:
            return operation()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (429, 500, 502, 503, 504):
                raise
            if attempt == 2:
                raise
        except httpx.TransportError:
            if attempt == 2:
                raise
        time.sleep(0.5 * (attempt + 1))


def submit_event_with_clip(client, event, clip_path):
    """Submit one event and upload its requested camera clip."""
    clip_path = Path(clip_path)

    if not clip_path.is_file():
        raise FileNotFoundError(clip_path)

    clip_bytes = clip_path.read_bytes()
    if not clip_bytes:
        raise ValueError("Empty clip")

    # Keep the same event payload and ID on retries.
    result = _retry(lambda: EventSender(client).send(event))

    # The server may return clip requests immediately.
    requests = result.get("clip_requests")

    if requests is None:
        response = _retry(lambda: client.get_clip_requests(event["event_id"]))
        requests = response["items"]

    uploaded = []

    for request in requests:
        if request["camera_id"] != event["camera_id"]:
            continue

        if request["role"] != "clip":
            continue

        if request.get("status") == "received":
            upload_result = {"request_id": request["request_id"], "status": "received"}
        else:
            try:
                upload_result = _retry(lambda: client.upload_clip(
                    event["event_id"], request["request_id"], clip_bytes,
                ))
            except httpx.HTTPError:
                try:
                    upload_result = _recover_delivery(client, event, request['request_id'])
                except httpx.HTTPError as recovery_error:
                    raise ClipDeliveryError('clip_delivery_state_unconfirmed') from recovery_error
        uploaded.append(upload_result)

    if not uploaded:
        raise RuntimeError("No matching trigger-camera clip request")

    return {
        "event_id": event["event_id"],
        "uploaded_count": len(uploaded),
        "uploads": uploaded,
    }
