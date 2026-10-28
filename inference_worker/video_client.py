
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

    def close(self):
        self.client.close()
