
"""Coordinate event submission and requested clip delivery.

This module performs HTTP writes when called with a real VideoClient.
Use a fake client for offline tests.
"""

from pathlib import Path
import time

import httpx

from inference_worker.event_sender import EventSender


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
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 409:
                    raise
                # A timed-out upload may have committed. Confirm delivery instead
                # of treating an arbitrary conflict as success or uploading anew.
                current = _retry(lambda: client.get_clip_requests(event["event_id"]))
                received = next((row for row in current["items"]
                    if row["request_id"] == request["request_id"]
                    and row["camera_id"] == event["camera_id"]
                    and row["status"] == "received"), None)
                if received is None:
                    raise
                upload_result = {"request_id": received["request_id"], "status": "received"}
        uploaded.append(upload_result)

    if not uploaded:
        raise RuntimeError("No matching trigger-camera clip request")

    return {
        "event_id": event["event_id"],
        "uploaded_count": len(uploaded),
        "uploads": uploaded,
    }
