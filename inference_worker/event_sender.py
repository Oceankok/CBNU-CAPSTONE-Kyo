
"""Submit validated event payloads without changing their event IDs."""


class EventSender:
    def __init__(self, client):
        self.client = client

    def send(self, payload):
        """Submit the same payload on retry.

        The caller must build and retain the payload before invoking send.
        """
        if not isinstance(payload, dict):
            raise ValueError("Event payload must be a dictionary")

        event_id = payload.get("event_id")

        if not isinstance(event_id, str) or not event_id.startswith("EVT_"):
            raise ValueError("Invalid event ID")

        return self.client.submit_event(payload)
