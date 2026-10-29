
"""Fetch video chunks from the existing FastAPI processing endpoints."""

import os

import httpx


class VideoClient:
    def __init__(self):
        self.server_url = os.environ.get(
            "PPE_SERVER_URL", "http://127.0.0.1:8000"
        ).rstrip("/")

        token = os.environ.get("PPE_PROCESSING_NODE_TOKEN")
        if not token:
            raise RuntimeError("PPE_PROCESSING_NODE_TOKEN is required")

        self.client = httpx.Client(
            base_url=self.server_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20.0,
            follow_redirects=False,
        )

    def list_streams(self):
        response = self.client.get("/api/processing/video/streams")
        response.raise_for_status()
        return response.json()["items"]

    def list_chunks(self, stream_id, after_sequence=-1):
        response = self.client.get(
            f"/api/processing/video/streams/{stream_id}/chunks",
            params={"after_sequence": after_sequence},
        )
        response.raise_for_status()
        return response.json()["items"]

    def download_chunk(self, chunk_id):
        response = self.client.get(
            f"/api/processing/video/chunks/{chunk_id}"
        )
        response.raise_for_status()
        return response.content
    
    
    def get_ppe_policy(self, camera_id):
        """Fetch the configured PPE policy for a camera."""
        response = self.client.get(
            f"/api/processing/cameras/{camera_id}/ppe-policy"
        )
        response.raise_for_status()
        return response.json()

    def submit_event(self, event):
        """Submit an already-qualified PPE violation event."""
        response = self.client.post(
            "/api/processing/events",
            json=event,
        )
        response.raise_for_status()
        return response.json()
    
    
    def get_clip_requests(self, event_id):
        response = self.client.get(
            f"/api/processing/events/{event_id}/clip-requests"
        )
        response.raise_for_status()
        return response.json()

    def upload_clip(self, event_id, request_id, video_bytes):
        """Upload an MP4 clip requested by the server."""
        if not video_bytes:
            raise ValueError("Empty video clip")

        response = self.client.post(
            f"/api/processing/events/{event_id}/clip",
            params={"request_id": request_id},
            headers={"Content-Type": "video/mp4"},
            content=video_bytes,
        )
        response.raise_for_status()
        return response.json()

    def report_clip_failure(self, event_id, request_id, error_code):
        response = self.client.put(
            f"/api/processing/events/{event_id}/clip-requests/{request_id}",
            json={
                "status": "failed",
                "error_code": error_code,
            },
        )
        response.raise_for_status()
        return response.json()


    def close(self):
        self.client.close()
