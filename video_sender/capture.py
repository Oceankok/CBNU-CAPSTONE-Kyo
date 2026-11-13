
import cv2
import os
import subprocess
from pathlib import Path
import time
from datetime import datetime,timezone
from uuid import uuid4

if __package__:
    from .backpressure import Policy
    from .chunks import next_sequence, publish_completed_segments
    from .capture_control import run_id_from_env, stop_requested, publish_result
else:
    from backpressure import Policy
    from chunks import next_sequence, publish_completed_segments
    from capture_control import run_id_from_env, stop_requested, publish_result

CAMERA_INDEX = int(os.getenv("VIDEO_CAMERA_INDEX", "0"))
FPS = 15
WIDTH = 640
HEIGHT = 360
SEGMENT_SECONDS = 1

OUTPUT_DIR = Path(os.getenv("VIDEO_OUTPUT_DIR", "video_sender/output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
START_SEQUENCE = next_sequence(OUTPUT_DIR)
CONTROL_RUN_ID = run_id_from_env()
SPOOL_POLICY = Policy.from_env() if CONTROL_RUN_ID else None
stopped_by_request = False
protective_stop_reason = None
frame_count = 0
SEGMENT_LIST = OUTPUT_DIR / f".capture-{uuid4().hex}.list"

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
    "-map", "0:v:0",
    "-an",
    "-c:v", "libx264",
    "-preset", "ultrafast",
    "-pix_fmt", "yuv420p",
    "-g", str(FPS),
    "-sc_threshold", "0",
    "-f", "segment",
    "-segment_format", "mp4",
    "-segment_list", str(SEGMENT_LIST),
    "-segment_list_type", "flat",
    "-segment_list_size", "8",
    "-segment_time", str(SEGMENT_SECONDS),
    "-segment_start_number", str(START_SEQUENCE),
    "-reset_timestamps", "1",
    str(OUTPUT_DIR / "chunk_%05d.mp4.part"),
]

process = subprocess.Popen(command, stdin=subprocess.PIPE)



try:
    print("영상 저장 시작. 종료하려면 Ctrl+C")

    frame_interval = 1.0 / FPS
    next_frame_time = time.perf_counter()

    frame_count = 0

    while True:
        if SPOOL_POLICY is not None:
            reason = SPOOL_POLICY.reason(OUTPUT_DIR)
            if reason:
                protective_stop_reason = reason
                print('[CAPTURE] protective stop: '+reason)
                (OUTPUT_DIR / f'.stop-{CONTROL_RUN_ID}').touch(exist_ok=True)
        if stop_requested(OUTPUT_DIR, CONTROL_RUN_ID):
            stopped_by_request = True
            break
        publish_completed_segments(OUTPUT_DIR, SEGMENT_LIST)
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
                sequence = START_SEQUENCE + frame_count // (FPS *SEGMENT_SECONDS)

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

    ffmpeg_exit = process.wait()
    publish_completed_segments(OUTPUT_DIR, SEGMENT_LIST)
    SEGMENT_LIST.unlink(missing_ok=True)
    publish_result(OUTPUT_DIR, CONTROL_RUN_ID, stopped_by_request=stopped_by_request,
                   ffmpeg_exit=ffmpeg_exit, start_sequence=START_SEQUENCE, frame_count=frame_count, fps=FPS, protective_stop_reason=protective_stop_reason)
    print("프로그램 종료")
