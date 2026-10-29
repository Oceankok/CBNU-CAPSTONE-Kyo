"""Refresh the existing inference priority lease at bounded intervals."""
import time


class InferenceLoad:
    def __init__(self, client):
        self.client = client
        self.next_refresh = 0
        self.active = False

    def refresh(self):
        if time.monotonic() >= self.next_refresh:
            # Mark first: a lost response may still have set the server lease.
            self.active = True
            self.client.set_inference_load(True)
            self.next_refresh = time.monotonic() + 10

    def release(self):
        if self.active:
            self.client.set_inference_load(False)
            self.active = False
        self.next_refresh = 0
