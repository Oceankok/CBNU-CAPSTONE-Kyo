
import os
import re
import time
from pathlib import Path

import requests
import subprocess
from datetime import datetime,timezone

# 서버 연결 설정
SERVER_URL = os.getenv(
    "VIDEO_SERVER_URL", "http://127.0.0.1:8000"
).rstrip("/")

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

    print(
        f"[STREAM] {CAMERA_ID} 생성 성공: {stream_id}",
        flush=True,
    )

    return stream_id


def read_capture_start(path):
    """
    MP4 조각에 대응하는 촬영 시작 시각 파일 읽기.

    chunk_00000.mp4
    -> chunk_00000.txt
    """

    timestamp_file = path.with_suffix(".txt")

    if not timestamp_file.exists():
        return None

    capture_start = timestamp_file.read_text(
        encoding="utf-8"
    ).strip()

    if not capture_start:
        return None

    return capture_start


def get_video_duration_ms(path):
    """
    ffprobe로 MP4 파일의 실제 재생 시간을 측정한다.
    """
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )

    duration_seconds = float(result.stdout.strip())
    duration_ms = round(duration_seconds * 1000)

    if duration_ms <= 0:
        raise ValueError("영상 길이가 올바르지 않습니다.")

    return duration_ms


# 서버는 촬영 시작 시각이 너무 오래된 영상을 거부함
# 네트워크 지연을 고려해 45초부터 폐기
MAX_CHUNK_AGE_SECONDS = 45


def is_stale_chunk(capture_start):
    """촬영 시각이 45초 이상 지났는지 확인."""
    try:
        captured = datetime.fromisoformat(
            capture_start.replace("Z", "+00:00")
        )

        if captured.tzinfo is None:
            raise ValueError("촬영 시각에 시간대 정보가 없습니다.")

        age = (
            datetime.now(timezone.utc) - captured
        ).total_seconds()

        return age > MAX_CHUNK_AGE_SECONDS

    except ValueError:
        # 잘못된 촬영 시각은 서버에서도 거부될 가능성이 높음
        return True


def delete_chunk_files(path):
    """MP4와 대응하는 촬영 시각 TXT 파일을 삭제."""
    for file_path in (path, path.with_suffix(".txt")):
        try:
            file_path.unlink(missing_ok=True)
        except OSError as error:
            print(
                f"[CLEAN ERROR] {CAMERA_ID} / "
                f"{file_path.name}: {error}",
                flush=True,
            )



def send_chunk(stream_id, sequence, path, capture_start):
    duration_ms = get_video_duration_ms(path)

    headers = {
        "Content-Type": "video/mp4",
        "X-Capture-Start": capture_start,
        "X-Duration-Ms": str(duration_ms),
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

    print(
        f"[SENT] {CAMERA_ID} / "
        f"{path.name} ({response.status_code})",
        flush=True,
    )



def watch_and_send():
    if not OUTPUT_DIR.exists():
        print(f"[ERROR] 출력 폴더 없음: {OUTPUT_DIR}")
        return

    sent = set()
    waiting_logged = set()

    # 최초 서버 연결 실패 시 3초 간격으로 재시도
    while True:
        try:
            stream_id = create_stream()
            break

        except requests.RequestException as error:
            print(
                f"[RETRY] {CAMERA_ID} / "
                f"스트림 생성 실패: {error}",
                flush=True,
            )
            print("[RETRY] 3초 후 다시 연결합니다.", flush=True)
            time.sleep(3)

    # 스트림 생성 성공 후 영상 전송 시작
    print(f"[LIVE] 카메라: {CAMERA_ID}", flush=True)
    print(f"[LIVE] 출력 폴더: {OUTPUT_DIR}", flush=True)
    print("[LIVE] MP4 조각 감시 및 전송 시작", flush=True)

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
                capture_start = read_capture_start(path)

                if capture_start is None:
                    if path.name not in waiting_logged:
                        print(
                            f"[WAIT] {CAMERA_ID} / "
                            f"{path.name} 촬영 시각 파일 없음",
                            flush=True,
                        )
                        waiting_logged.add(path.name)
                    continue

                if is_stale_chunk(capture_start):
                 print(
                    f"[DROP] {CAMERA_ID} / "
                    f"{path.name} 오래된 영상 폐기",
                    flush=True,
                 )

                delete_chunk_files(path)
                sent.add(path.name)
                waiting_logged.discard(path.name)
                continue


                try:
                    send_chunk(
                        stream_id,
                        sequence,
                        path,
                        capture_start,
                    )

                    sent.add(path.name)
                    waiting_logged.discard(path.name)

                    delete_chunk_files(path)

                except requests.RequestException as error:
                    print(
                        f"[DISCONNECTED] {CAMERA_ID} / "
                        f"전송 실패: {error}",
                        flush=True,
                    )

                    # 서버 재연결 및 새 스트림 생성
                    while True:
                        try:
                            print(
                                f"[RECONNECT] {CAMERA_ID} / "
                                "3초 후 재연결 시도",
                                flush=True,
                            )
                            time.sleep(3)
                            stream_id = create_stream()
                            break

                        except requests.RequestException as retry_error:
                            print(
                                f"[RETRY] {CAMERA_ID} / "
                                f"{retry_error}",
                                flush=True,
                            )

                    # 새 스트림이 생성되었으므로 이전 조각 재전송은
                    # 다음 반복에서 처리
                    break

                except (
                    subprocess.SubprocessError,
                    ValueError,
                    OSError,
                ) as error:
                    print(
                        f"[ERROR] {CAMERA_ID} / "
                        f"{path.name} 처리 실패: {error}",
                        flush=True,
                    )


            time.sleep(0.2)

    except KeyboardInterrupt:
        print(f"\n[LIVE] {CAMERA_ID} 전송 종료")


if __name__ == "__main__":
    watch_and_send()
