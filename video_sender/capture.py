
import cv2
import os
import subprocess
from pathlib import Path
import time
from datetime import datetime,timezone

CAMERA_INDEX = int(os.getenv("VIDEO_CAMERA_INDEX", "0"))
FPS = 15
WIDTH = 640
HEIGHT = 360
SEGMENT_SECONDS = 1

OUTPUT_DIR = Path(os.getenv("VIDEO_OUTPUT_DIR", "video_sender/output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)

if not cap.isOpened():
    raise RuntimeError("카메라를 열 수 없습니다.")

cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
cap.set(cv2.CAP_PROP_FPS, FPS)

width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

print(f"카메라 연결: {width}x{height}")

command = [
    "ffmpeg", "-y",
    "-f", "rawvideo",
    "-pixel_format", "bgr24",
    "-video_size", f"{width}x{height}",
    "-framerate", str(FPS),
    "-i", "-",
    "-an",
    "-c:v", "libx264",
    "-preset", "ultrafast",
    "-pix_fmt", "yuv420p",
    "-g", str(FPS),
    "-sc_threshold", "0",
    "-f", "segment",
    "-segment_time", str(SEGMENT_SECONDS),
    "-reset_timestamps", "1",
    str(OUTPUT_DIR / "chunk_%05d.mp4"),
]

process = subprocess.Popen(command, stdin=subprocess.PIPE)



try:
    print("영상 저장 시작. 종료하려면 Ctrl+C")

    frame_interval = 1.0 / FPS
    next_frame_time = time.perf_counter()

    frame_count = 0

    while True:
        success, frame = cap.read()

        if not success:
            print("카메라 프레임 읽기 실패")
            break

        now = time.perf_counter()

        if now < next_frame_time:
            continue

        if frame.shape[1] != width or frame.shape[0] != height:
            frame = cv2.resize(frame, (width, height))

        try:
            if frame_count % (FPS * SEGMENT_SECONDS) == 0:
                sequence = frame_count // (FPS *SEGMENT_SECONDS)

                capture_start = datetime.now(timezone.utc)
                timestamp_file = OUTPUT_DIR / f"chunk_{sequence:05d}.txt"

                timestamp_file.write_text(
                    capture_start.isoformat(timespec="milliseconds").replace("+00:00","Z"),
                    encoding="utf-8"
                )

            process.stdin.write(frame.tobytes())
            frame_count += 1

        except BrokenPipeError:
            print("FFmpeg 프로세스가 종료되었습니다.")
            break

        # 이전 예약 시점을 기준으로 다음 프레임 시간 계산
        next_frame_time += frame_interval

        # 처리가 밀리면 지난 예약 시점을 건너뛰기
        now = time.perf_counter()
        if next_frame_time < now:
            next_frame_time = now

except KeyboardInterrupt:
    print("\n영상 저장 종료")



finally:
    cap.release()

    if process.stdin:
        process.stdin.close()

    process.wait()
    print("프로그램 종료")
