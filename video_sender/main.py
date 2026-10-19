
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import threading
from datetime import datetime,timezone

ROOT_DIR = Path(__file__).resolve().parent.parent
VIDEO_DIR = ROOT_DIR / "video_sender"

CAM001_DIR = VIDEO_DIR / "output"
CAM002_DIR = VIDEO_DIR / "output_cam002"
SAMPLE_VIDEO = VIDEO_DIR / "fixtures" / "sample.mp4"

SERVER_URL = os.getenv("VIDEO_SERVER_URL", "http://127.0.0.1:8000")
TOKEN_FILE = ROOT_DIR / ".private_media" / "demo" / "node_token.txt"

if TOKEN_FILE.is_file() and not os.getenv("VIDEO_NODE_TOKEN"):
    os.environ["VIDEO_NODE_TOKEN"] = TOKEN_FILE.read_text(
        encoding="utf-8-sig"
    ).strip()

processes = []


def start_process(name, command, env=None):
    process = subprocess.Popen(
        command,
        cwd=ROOT_DIR,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    processes.append((name, process))
    print(f"[START] {name} / PID={process.pid}", flush=True)
    return process


def start_sender(camera_id, output_dir):
    env = os.environ.copy()
    env["VIDEO_SERVER_URL"] = SERVER_URL
    env["VIDEO_CAMERA_ID"] = camera_id
    env["VIDEO_OUTPUT_DIR"] = str(output_dir)

    return start_process(
        f"{camera_id} sender",
        [sys.executable, str(VIDEO_DIR / "live_sender.py")],
        env,
    )


def clear_old_chunks(directory):
    directory.mkdir(parents=True, exist_ok=True)

    for pattern in ("chunk_*.mp4", "chunk_*.txt"):
        for path in directory.glob(pattern):
            path.unlink()

    print(f"[CLEAN] {directory.name}", flush=True)


def stop_processes():
    print("\n[STOP] 영상 송신 프로그램 종료 중...", flush=True)

    # 송신기를 먼저 종료
    for name, process in reversed(processes):
        if process.poll() is not None:
            continue

        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        print(f"[STOP] {name}", flush=True)

def write_cam002_timestamps(stop_event):
    """
    CAM_002의 MP4 조각 생성 시각을 기록하는 테스트용 함수.
    """
    recorded = set()

    while not stop_event.is_set():
        chunks = sorted(CAM002_DIR.glob("chunk_*.mp4"))

        for path in chunks:
            if path.name in recorded:
                continue

            timestamp_file = path.with_suffix(".txt")

            capture_start = datetime.now(timezone.utc)

            timestamp_file.write_text(
                capture_start.isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                encoding="utf-8",
            )

            recorded.add(path.name)

        stop_event.wait(0.1)

def main():
    if not SAMPLE_VIDEO.is_file():
        print(f"[ERROR] 테스트 영상 없음: {SAMPLE_VIDEO}")
        return

    print("===================================")
    print("  Video Sender - 2 Channel Test")
    print("===================================")
    print(f"Server: {SERVER_URL}")

    clear_old_chunks(CAM001_DIR)
    clear_old_chunks(CAM002_DIR)

    # CAM_001: 실제 노트북 카메라
    start_process(
        "CAM_001 capture",
        [sys.executable, str(VIDEO_DIR / "capture.py")],
    )

    # CAM_002: 녹화 영상 반복 재생
    ffmpeg_command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-re",
        "-stream_loop", "-1",
        "-i", str(SAMPLE_VIDEO),
        "-an",
        "-vf", "fps=15,scale=640:360",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-g", "15",
        "-sc_threshold", "0",
        "-f", "segment",
        "-segment_time", "1",
        "-reset_timestamps", "1",
        str(CAM002_DIR / "chunk_%05d.mp4"),
    ]

    start_process("CAM_002 video", ffmpeg_command)

    stop_event = threading.Event()

    timestamp_thread = threading.Thread(
        target=write_cam002_timestamps,
        args=(stop_event,),
        daemon=True,
    )

    timestamp_thread.start()

    time.sleep(2)

    # 두 카메라의 송신기를 각각 실행
    start_sender(
        os.getenv("VIDEO_CAMERA_1_ID", "CAM_001"),
        CAM001_DIR,
    )
    start_sender(
        os.getenv("VIDEO_CAMERA_2_ID", "CAM_002"),
        CAM002_DIR,
    )

    print("\n[RUNNING] 2채널 송신 시작")
    print("종료하려면 Ctrl+C\n", flush=True)

    try:
        while True:
            for name, process in processes:
                code = process.poll()

                if code is not None:
                    print(f"[ERROR] {name} 종료됨 (code={code})")
                    return

            time.sleep(1)
    finally:
        stop_event.set()
        timestamp_thread.join(timeout=2)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n사용자 종료 요청")
    finally:
        stop_processes()
