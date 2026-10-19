
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
VIDEO_DIR = ROOT_DIR / "video_sender"

CAM001_DIR = VIDEO_DIR / "output"
CAM002_DIR = VIDEO_DIR / "output_cam002"
SAMPLE_VIDEO = VIDEO_DIR / "fixtures" / "sample.mp4"

SERVER_URL = os.getenv("VIDEO_SERVER_URL", "http://127.0.0.1:8000")

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

    for path in directory.glob("chunk_*.mp4"):
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

    time.sleep(2)

    # 두 카메라의 송신기를 각각 실행
    start_sender("CAM_001", CAM001_DIR)
    start_sender("CAM_002", CAM002_DIR)

    print("\n[RUNNING] 2채널 송신 시작")
    print("종료하려면 Ctrl+C\n", flush=True)

    while True:
        for name, process in processes:
            code = process.poll()

            if code is not None:
                print(f"[ERROR] {name} 종료됨 (code={code})")
                return

        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n사용자 종료 요청")
    finally:
        stop_processes()
