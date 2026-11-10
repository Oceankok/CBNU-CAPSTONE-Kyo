"""Bounded secondary checks on encoded private outputs, before publication.

No detection is evidence of a passed detector check, not proof of anonymity.
Original retention/deletion policy is deliberately managed by the existing server.
"""
import math
import subprocess
from pathlib import Path

import cv2

from backend.media.redaction import MediaError

MAX_REPAIRS = 2


def cover(frame, faces):
    result = frame.copy()
    height, width = frame.shape[:2]
    for x, y, w, h, score in faces:
        if not all(math.isfinite(v) for v in (x, y, w, h, score)) or w <= 0 or h <= 0:
            raise MediaError('face_verification_failed')
        left, top = max(0, math.floor(x - .5*w)), max(0, math.floor(y - .5*h))
        right, bottom = min(width, math.ceil(x + 1.5*w)), min(height, math.ceil(y + 1.5*h))
        if right <= left or bottom <= top:
            raise MediaError('face_verification_failed')
        result[top:bottom, left:right] = 0
    return result


def verify_image(output, detector):
    success = False
    try:
        for attempt in range(MAX_REPAIRS + 1):
            frame = cv2.imread(str(output), cv2.IMREAD_COLOR)
            faces = detector.detect(frame)
            if not faces:
                success = True
                return {'model': 'retinaface-resnet50', 'status': 'passed', 'passes': attempt+1, 'repairs': attempt, 'frames': 1}
            if attempt == MAX_REPAIRS:
                break
            if not cv2.imwrite(str(output), cover(frame, faces)):
                raise MediaError('image_encode_failed')
        raise MediaError('face_verification_failed')
    finally:
        if not success:
            output.unlink(missing_ok=True)


def verify_video(output, detector, work_dir, expected_frames):
    from backend.media.redaction import _ffmpeg
    success = False
    try:
        for attempt in range(MAX_REPAIRS + 1):
            capture = cv2.VideoCapture(str(output))
            writer = None
            intermediate = Path(work_dir) / f'verification-{attempt}.avi'
            staged = Path(work_dir) / f'verification-{attempt}.mp4'
            try:
                if not capture.isOpened():
                    raise MediaError('video_decode_failed')
                fps = capture.get(cv2.CAP_PROP_FPS)
                if not math.isfinite(fps) or not 1 <= fps <= 120:
                    raise MediaError('invalid_video_fps')
                frames, detections, size = 0, 0, None
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    frames += 1
                    if frames > expected_frames:
                        raise MediaError('face_verification_failed')
                    current_size = (frame.shape[1], frame.shape[0])
                    if size is None:
                        size = current_size
                        if attempt < MAX_REPAIRS:
                            writer = cv2.VideoWriter(str(intermediate), cv2.VideoWriter_fourcc(*'MJPG'), fps, size)
                            if not writer.isOpened():
                                raise MediaError('video_writer_failed')
                    if current_size != size:
                        raise MediaError('video_resolution_changed')
                    faces = detector.detect(frame)
                    detections += len(faces)
                    if writer is not None:
                        writer.write(cover(frame, faces) if faces else frame)
                if frames != expected_frames or frames == 0:
                    raise MediaError('incomplete_video_decode')
            finally:
                capture.release()
                if writer is not None:
                    writer.release()
            try:
                if not detections:
                    success = True
                    return {'model': 'retinaface-resnet50', 'status': 'passed', 'passes': attempt+1, 'repairs': attempt, 'frames': frames}
                if attempt == MAX_REPAIRS:
                    raise MediaError('face_verification_failed')
                try:
                    subprocess.run([_ffmpeg(), '-y', '-i', str(intermediate), '-an', '-c:v', 'libx264',
                                    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(staged)],
                                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
                except (OSError, subprocess.SubprocessError) as exc:
                    raise MediaError('video_encode_failed') from exc
                if not staged.is_file() or staged.stat().st_size == 0:
                    raise MediaError('video_encode_failed')
                staged.replace(output)
            finally:
                intermediate.unlink(missing_ok=True)
                staged.unlink(missing_ok=True)
        raise MediaError('face_verification_failed')
    finally:
        for attempt in range(MAX_REPAIRS + 1):
            (Path(work_dir) / f'verification-{attempt}.avi').unlink(missing_ok=True)
            (Path(work_dir) / f'verification-{attempt}.mp4').unlink(missing_ok=True)
        if not success:
            output.unlink(missing_ok=True)
