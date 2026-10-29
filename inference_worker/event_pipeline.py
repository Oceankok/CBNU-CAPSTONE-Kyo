
"""Coordinate event submission and requested clip delivery.

This module performs HTTP writes when called with a real VideoClient.
Use a fake client for offline tests.
"""

from pathlib import Path

from inference_worker.event_sender import EventSender


def submit_event_with_clip(client, event, clip_path):
    """Submit one event and upload its requested camera clip."""
    clip_path = Path(clip_path)

    if not clip_path.is_file():
        raise FileNotFoundError(clip_path)

    clip_bytes = clip_path.read_bytes()
    if not clip_bytes:
        raise ValueError("Empty clip")

    # Keep the same event payload and ID on retries.
    result = EventSender(client).send(event)

    # The server may return clip requests immediately.
    requests = result.get("clip_requests")

    if requests is None:
        response = client.get_clip_requests(event["event_id"])
        requests = response["items"]

    uploaded = []

    for request in requests:
        if request["camera_id"] != event["camera_id"]:
            continue

        if request["role"] != "clip":
            continue

        upload_result = client.upload_clip(
            event["event_id"],
            request["request_id"],
            clip_bytes,
        )
        uploaded.append(upload_result)

    return {
        "event_id": event["event_id"],
        "uploaded_count": len(uploaded),
        "uploads": uploaded,
    }
