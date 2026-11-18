
import cv2
import os
import subprocess
from pathlib import Path
import time
import json
import sys
from datetime import datetime,timezone
from uuid import uuid4

if __package__:
    from .backpressure import Policy
    from .chunks import next_sequence, publish_completed_segments
    from .capture_control import PostRoll, normal_stop_requested, run_id_from_env, stop_requested, publish_result
    from .frame_reader import LatestFrameReader
else:
    from backpressure import Policy
    from chunks import next_sequence, publish_completed_segments
    from capture_control import PostRoll, normal_stop_requested, run_id_from_env, stop_requested, publish_result
    from frame_reader import LatestFrameReader

CAMERA_INDEX = int(os.getenv("VIDEO_CAMERA_INDEX", "0"))
FPS = 15
WIDTH = 640
HEIGHT = 360
SEGMENT_SECONDS = 1

OUTPUT_DIR = Path(os.getenv("VIDEO_OUTPUT_DIR", "video_sender/output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
START_SEQUENCE = next_sequence(OUTPUT_DIR)
CONTROL_RUN_ID = run_id_from_env()
CLOCK_ANCHOR_FILE = os.environ.get('VIDEO_CLOCK_ANCHOR_FILE')
if CLOCK_ANCHOR_FILE:
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from video_sender.clock_source import capture_stamp
    from clock_contract import AnchoredClock,ClockPolicy
    clock_data=json.loads(Path(CLOCK_ANCHOR_FILE).read_text(encoding='utf-8'))
    capture_clock=AnchoredClock(clock_data['anchor'],ClockPolicy(**clock_data['policy']))
NORMAL_STOP_STREAM_ID = os.environ.get('VIDEO_NORMAL_STOP_STREAM_ID')
post_roll = (PostRoll(OUTPUT_DIR, CONTROL_RUN_ID, NORMAL_STOP_STREAM_ID, START_SEQUENCE, FPS)
             if CLOCK_ANCHOR_FILE and CONTROL_RUN_ID and NORMAL_STOP_STREAM_ID else None)
SPOOL_POLICY = Policy.from_env() if CONTROL_RUN_ID else None
stopped_by_request = False
protective_stop_reason = None
normal_stop_completed = False
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

def immediate_stop():
    if stop_requested(OUTPUT_DIR, CONTROL_RUN_ID):
        return True
    if post_roll is not None:
        post_roll.check_deadline()
    return False


process = subprocess.Popen(command, stdin=subprocess.PIPE)
reader = LatestFrameReader(cap)



try:
    print("영상 저장 시작. 종료하려면 Ctrl+C")

    frame_interval = 1.0 / FPS
    next_frame_time = time.perf_counter()

    frame_count = 0
    reader.start()

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
        result = reader.next_frame(next_frame_time, immediate_stop)
        if result.status == 'stopped':
            stopped_by_request = True
            break
        if result.status == 'ended':
            print("카메라 프레임 읽기 실패")
            break
        frame = result.sample.frame

        if frame.shape[1] != width or frame.shape[0] != height:
            frame = cv2.resize(frame, (width, height))

        try:
            if frame_count % (FPS * SEGMENT_SECONDS) == 0:
                sequence = START_SEQUENCE + frame_count // (FPS *SEGMENT_SECONDS)

                if CLOCK_ANCHOR_FILE:
                    stamp,proof_id=capture_stamp(CLOCK_ANCHOR_FILE,clock=capture_clock,observed_monotonic=result.sample.observed_monotonic)
                    capture_start=datetime.fromtimestamp(stamp,timezone.utc)
                    (OUTPUT_DIR/f'chunk_{sequence:05d}.clock.json').write_text(json.dumps({'proof_id':proof_id}),encoding='utf-8')
                else:capture_start = result.sample.observed_utc
                timestamp_file = OUTPUT_DIR / f"chunk_{sequence:05d}.txt"

                timestamp = capture_start.isoformat(timespec="milliseconds")
                timestamp_file.write_text(timestamp.replace("+00:00","Z"), encoding="utf-8")
                if post_roll is not None:
                    chunk_source_time = datetime.fromisoformat(timestamp).timestamp()

            if post_roll is not None:
                source_time = chunk_source_time + (frame_count % FPS) / FPS
                post_roll.before_frame(frame_count, result.sample.observed_monotonic, proof_id, source_time)
            process.stdin.write(frame.tobytes())
            frame_count += 1
            if post_roll is not None and post_roll.complete(frame_count, result.sample.observed_monotonic, source_time):
                normal_stop_completed = True
                stopped_by_request = True
                break

        except BrokenPipeError as error:
            if CONTROL_RUN_ID:
                if __package__:from .failure_record import record_failure
                else:from failure_record import record_failure
                record_failure(OUTPUT_DIR,CONTROL_RUN_ID,'capture',error)
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
except Exception as error:
    if CONTROL_RUN_ID:
        if __package__:from .failure_record import record_failure
        else:from failure_record import record_failure
        record_failure(OUTPUT_DIR,CONTROL_RUN_ID,'capture',error)
    raise

finally:
    reader_failure = None
    try:
        reader.close()
    except BaseException as error:
        reader_failure = error

    if process.stdin:
        process.stdin.close()

    ffmpeg_exit = process.wait()
    publish_completed_segments(OUTPUT_DIR, SEGMENT_LIST)
    SEGMENT_LIST.unlink(missing_ok=True)
    if reader_failure is not None:
        if CONTROL_RUN_ID:
            if __package__:from .failure_record import record_failure
            else:from failure_record import record_failure
            record_failure(OUTPUT_DIR,CONTROL_RUN_ID,'capture',reader_failure)
        # Encoding can finish while a camera read is still blocked. That is not
        # successful capture-stop proof; keep context recovery fail-closed.
        raise reader_failure
    print(f'[CAPTURE] read={reader.frames_read} superseded={reader.superseded} submitted={frame_count}')
    if post_roll is not None and not normal_stop_completed:
        if post_roll.first_frame is not None or normal_stop_requested(OUTPUT_DIR, CONTROL_RUN_ID):
            stopped_by_request = False
    publish_result(OUTPUT_DIR, CONTROL_RUN_ID, stopped_by_request=stopped_by_request,
                   ffmpeg_exit=ffmpeg_exit, start_sequence=START_SEQUENCE, frame_count=frame_count, fps=FPS, protective_stop_reason=protective_stop_reason,
                   normal_stop_completed=normal_stop_completed)
    print("프로그램 종료")
