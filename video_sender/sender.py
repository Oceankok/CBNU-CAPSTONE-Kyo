
import os
from pathlib import Path

import requests

SERVER_URL = os.getenv("VIDEO_SERVER_URL", "http://127.0.0.1:8000")
NODE_TOKEN = os.getenv("VIDEO_NODE_TOKEN", "")
CAMERA_ID = os.getenv("VIDEO_CAMERA_ID", "CAM_001")

OUTPUT_DIR = Path("video_sender/output")


def create_stream():
    response = requests.post(
        f"{SERVER_URL}/api/video/streams",
        headers={"Authorization": f"Bearer {NODE_TOKEN}"},
        json={"camera_id": CAMERA_ID},
        timeout=10,
    )
    response.raise_for_status()

    stream_id = response.json()["stream_id"]
    print(f"스트림 생성 성공: {stream_id}")
    return stream_id


def send_chunk(stream_id, sequence, path):
    with path.open("rb") as video:
        response = requests.post(
            f"{SERVER_URL}/api/video/streams/{stream_id}/chunks",
            params={"sequence": sequence},
            headers={
                "Authorization": f"Bearer {NODE_TOKEN}",
                "Content-Type": "video/mp4",
                "X-Duration-Ms": "1000",
            },
            data=video,
            timeout=15,
        )

    response.raise_for_status()
    print(f"전송 성공: {path.name} ({response.status_code})")


def main():
    stream_id = create_stream()

    chunks = sorted(OUTPUT_DIR.glob("chunk_*.mp4"))

    if not chunks:
        print("전송할 MP4 파일이 없습니다.")
        return

    for sequence, chunk in enumerate(chunks):
        send_chunk(stream_id, sequence, chunk)


if __name__ == "__main__":
    main()
