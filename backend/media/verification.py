"""Bounded secondary checks on encoded private outputs, before publication.

No detection is evidence of a passed detector check, not proof of anonymity.
Original retention/deletion policy is deliberately managed by the existing server.
"""
import math
import subprocess
from pathlib import Path

import cv2

from backend.media.redaction import MediaError
from backend.media.verification_diagnostics import VerificationFailure

MAX_REPAIRS = 2
# Primary face discovery remains at 0.5. The encoded-output check has a
# separate confidence requirement because pixelated face-shaped regions can
# still trigger the model without exposing facial detail.
RESIDUAL_CONFIDENCE_THRESHOLD = 0.8


def residual_faces(frame, detector):
    faces = detector.detect(frame)
    for x, y, w, h, score in faces:
        if not all(math.isfinite(v) for v in (x, y, w, h, score)) or w <= 0 or h <= 0:
            raise VerificationFailure('invalid_repair_box')
    return [face for face in faces if face[4] >= RESIDUAL_CONFIDENCE_THRESHOLD]


def repair_regions(frame, faces, previous=()):
    height, width = frame.shape[:2]
    regions = list(previous)
    for x, y, w, h, score in faces:
        if not all(math.isfinite(v) for v in (x, y, w, h, score)) or w <= 0 or h <= 0:
            raise VerificationFailure('invalid_repair_box')
        # Match the primary face margin rather than expanding across the head.
        # Retain upper/lower colour cues, while removing paired eye detail. Never replace the
        # whole region with an opaque rectangle.
        left, top = max(0, math.floor(x - .3*w)), max(0, math.floor(y - .3*h))
        right, bottom = min(width, math.ceil(x + 1.3*w)), min(height, math.ceil(y + 1.3*h))
        if right <= left or bottom <= top:
            raise VerificationFailure('invalid_repair_box')
        regions.append((left, top, right, bottom))
    # Nested/slightly shifted masks must not create fresh face-like block
    # boundaries when drawn over one another. Render their union just once.
    merged = []
    for region in regions:
        left, top, right, bottom = region
        changed = True
        while changed:
            changed = False
            for old in list(merged):
                if min(right, old[2]) > max(left, old[0]) and min(bottom, old[3]) > max(top, old[1]):
                    left, top = min(left, old[0]), min(top, old[1])
                    right, bottom = max(right, old[2]), max(bottom, old[3])
                    merged.remove(old)
                    changed = True
        merged.append((left, top, right, bottom))
    return merged


def pixelate_regions(frame, regions):
    result = frame.copy()
    for left, top, right, bottom in regions:
        roi = result[top:bottom, left:right]
        tiny = cv2.resize(roi, (min(2, roi.shape[1]), min(2, roi.shape[0])),
                          interpolation=cv2.INTER_AREA)
        result[top:bottom, left:right] = cv2.resize(
            tiny, (roi.shape[1], roi.shape[0]), interpolation=cv2.INTER_NEAREST)
    return result


def cover(frame, faces):
    return pixelate_regions(frame, repair_regions(frame, faces))


def verify_image(output, detector):
    success = False
    try:
        for attempt in range(MAX_REPAIRS + 1):
            frame = cv2.imread(str(output), cv2.IMREAD_COLOR)
            faces = residual_faces(frame, detector)
            if not faces:
                success = True
                return {'model': 'retinaface-resnet50', 'status': 'passed', 'passes': attempt+1,
                        'repairs': attempt, 'frames': 1,
                        'residual_threshold': RESIDUAL_CONFIDENCE_THRESHOLD}
            if attempt == MAX_REPAIRS:
                break
            if not cv2.imwrite(str(output), cover(frame, faces)):
                raise MediaError('image_encode_failed')
        raise VerificationFailure('residual_detection_limit')
    except VerificationFailure as error:
        error.annotate(stage='encoded_verification', passes=attempt+1, frames_checked=1, expected_frames=1)
        raise
    finally:
        if not success:
            output.unlink(missing_ok=True)


def write_video_repair(source, target, fps, size, found, known, previous):
    """Backfill and hold repairs using the existing 0.8s face-continuity window."""
    capture = cv2.VideoCapture(str(source))
    writer = None
    regions_by_frame = []
    window = max(1, round(fps*.8))
    try:
        if not capture.isOpened():
            raise MediaError('video_decode_failed')
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            index = len(regions_by_frame)
            if index >= len(found):
                raise VerificationFailure('frame_count_exceeded')
            if (frame.shape[1], frame.shape[0]) != size:
                raise MediaError('video_resolution_changed')
            nearby = [face for faces in found[max(0,index-window):index+window+1] for face in faces]
            targets = repair_regions(frame, nearby)
            matching = []
            for face in (known[index] if known is not None else ()):
                rect = repair_regions(frame, [face])[0]
                if any(min(rect[2], target[2]) > max(rect[0], target[0]) and
                       min(rect[3], target[3]) > max(rect[1], target[1]) for target in targets):
                    matching.append(face)
            # Strengthen only the affected face/region, not every face in a
            # clip whose unrelated phone/background caused a residual.
            masks = (*matching, *nearby)
            regions = repair_regions(frame, masks, previous[index])
            if writer is None:
                writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*'MJPG'), fps, size)
                if not writer.isOpened():
                    raise MediaError('video_writer_failed')
            writer.write(pixelate_regions(frame, regions) if regions else frame)
            regions_by_frame.append(regions)
        if len(regions_by_frame) != len(found):
            raise MediaError('incomplete_video_decode')
        return regions_by_frame
    finally:
        capture.release()
        if writer is not None:
            writer.release()


def verify_video(output, detector, work_dir, expected_frames, *, repair_masks=None):
    from backend.media.redaction import _ffmpeg
    success = False
    applied_masks = [()] * expected_frames
    try:
        if repair_masks is not None and len(repair_masks) != expected_frames:
            raise MediaError('incomplete_video_decode')
        for attempt in range(MAX_REPAIRS + 1):
            found = []
            capture = cv2.VideoCapture(str(output))
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
                        raise VerificationFailure('frame_count_exceeded')
                    current_size = (frame.shape[1], frame.shape[0])
                    if size is None:
                        size = current_size
                    if current_size != size:
                        raise MediaError('video_resolution_changed')
                    faces = residual_faces(frame, detector)
                    detections += len(faces)
                    found.append(faces)
                if frames != expected_frames or frames == 0:
                    raise MediaError('incomplete_video_decode')
            finally:
                capture.release()
            try:
                if not detections:
                    success = True
                    return {'model': 'retinaface-resnet50', 'status': 'passed', 'passes': attempt+1,
                            'repairs': attempt, 'frames': frames,
                            'residual_threshold': RESIDUAL_CONFIDENCE_THRESHOLD}
                if attempt == MAX_REPAIRS:
                    raise VerificationFailure('residual_detection_limit')
                next_masks = write_video_repair(output, intermediate, fps, size, found,
                                                repair_masks, applied_masks)
                try:
                    subprocess.run([_ffmpeg(), '-y', '-i', str(intermediate), '-an', '-c:v', 'libx264',
                                    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(staged)],
                                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
                except (OSError, subprocess.SubprocessError) as exc:
                    raise MediaError('video_encode_failed') from exc
                if not staged.is_file() or staged.stat().st_size == 0:
                    raise MediaError('video_encode_failed')
                staged.replace(output)
                applied_masks = next_masks
            finally:
                intermediate.unlink(missing_ok=True)
                staged.unlink(missing_ok=True)
        raise VerificationFailure('residual_detection_limit')
    except VerificationFailure as error:
        error.annotate(stage='encoded_verification', passes=attempt+1,
                       frames_checked=frames, expected_frames=expected_frames)
        raise
    finally:
        for attempt in range(MAX_REPAIRS + 1):
            (Path(work_dir) / f'verification-{attempt}.avi').unlink(missing_ok=True)
            (Path(work_dir) / f'verification-{attempt}.mp4').unlink(missing_ok=True)
        if not success:
            output.unlink(missing_ok=True)
