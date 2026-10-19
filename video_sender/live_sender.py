
import os
import re
import time
from pathlib import Path

import requests

# 서버 연결 설정
SERVER_URL = os.getenv("VIDEO_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
NODE_TOKEN = os.getenv("VIDEO_NODE_TOKEN", "")
CAMERA_ID = os.getenv("VIDEO_CAMERA_ID", "CAM_001")

# MP4 조각 저장 폴더
OUTPUT_DIR = Path(
    os.getenv("VIDEO_OUTPUT_DIR", "video_sender/output")
)

# 파일명 예시: chunk_00000.mp4
CHUNK_PATTERN = re.compile(r"chunk_(\d+)\.mp4")


def create_stream():
    headers = {}

    if NODE_TOKEN:
        headers["Authorization"] = f"Bearer {NODE_TOKEN}"

    response = requests.post(
        f"{SERVER_URL}/api/video/streams",
        headers=headers,
        json={"camera_id": CAMERA_ID},
        timeout=10,
    )

    response.raise_for_status()

    stream_id = response.json()["stream_id"]
    print(f"[STREAM] 생성 성공: {stream_id}")

    return stream_id


def send_chunk(stream_id, sequence, path):
    headers = {
        "Content-Type": "video/mp4",
        "X-Duration-Ms": "1000",
    }

    if NODE_TOKEN:
        headers["Authorization"] = f"Bearer {NODE_TOKEN}"

    with path.open("rb") as video:
        response = requests.post(
            f"{SERVER_URL}/api/video/streams/{stream_id}/chunks",
            params={"sequence": sequence},
            headers=headers,
            data=video,
            timeout=15,
        )

    response.raise_for_status()

    print(f"[SENT] {path.name} ({response.status_code})")


def watch_and_send():
    if not OUTPUT_DIR.exists():
        print(f"[ERROR] 출력 폴더 없음: {OUTPUT_DIR}")
        return

    stream_id = create_stream()
    sent = set()

    print(f"[LIVE] 카메라: {CAMERA_ID}")
    print("[LIVE] MP4 조각 감시 및 전송 시작")

    try:
        while True:
            chunks = sorted(OUTPUT_DIR.glob("chunk_*.mp4"))

            # 마지막 파일은 FFmpeg가 작성 중일 수 있으므로 제외
            completed = chunks[:-1]

            for path in completed:
                match = CHUNK_PATTERN.fullmatch(path.name)

                if not match or path.name in sent:
                    continue

                sequence = int(match.group(1))

                try:
                    send_chunk(stream_id, sequence, path)
                    sent.add(path.name)

                except requests.RequestException as error:
                    print(f"[ERROR] 전송 실패: {path.name} / {error}")

            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\n[LIVE] 전송 종료")


if __name__ == "__main__":
    watch_and_send()
