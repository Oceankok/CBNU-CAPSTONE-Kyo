"""Selectable face detection followed by pixelation; never falls back to raw output."""

import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import cv2

from backend.media.paths import MODEL_SHA256, model_path, scrfd_model_path


class MediaError(RuntimeError):
    pass


class FaceRedactor:
    def __init__(self, mode="scrfd"):
        started = time.perf_counter()
        self.metrics = {name: 0.0 for name in (
            "model_init_seconds", "detection_seconds", "tracking_seconds", "pixelation_seconds",
            "decode_seconds", "intermediate_write_seconds", "encoding_seconds")}
        self.detection_passes = 0
        self.cache_reused = False
        self.trace_enabled = os.environ.get("PPE_MEDIA_TRACE") == "1"
        self.frame_index = 0
        self.trace_id = uuid4().hex
        if mode not in {"enhanced", "scrfd"}:
            raise MediaError("invalid_redaction_mode")
        self.mode = mode
        path = scrfd_model_path() if mode == "scrfd" else model_path()
        if not path.is_file():
            raise MediaError("scrfd_model_missing" if mode == "scrfd" else "face_model_missing")
        expected_hash = MODEL_SHA256
        if mode == "scrfd":
            try:
                manifest = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
                expected_hash = manifest["sha256"]
                if manifest["model"] != "scrfd-10g" or len(expected_hash) != 64:
                    raise ValueError("invalid manifest")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise MediaError("scrfd_manifest_missing") from exc
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise MediaError("face_model_checksum_mismatch")
        from backend.media.enhanced import get_cuda_detector
        self.detector, self.cache_reused = get_cuda_detector(path, "scrfd" if mode == "scrfd" else "yunet")
        self.backend = "onnxruntime-cuda"
        self.metrics["model_init_seconds"] = time.perf_counter() - started

    def detect_faces(self, frame, rotated=True):
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
            raise MediaError("invalid_frame")
        height, width = frame.shape[:2]
        if not width or not height:
            raise MediaError("invalid_frame")
        started = time.perf_counter()
        try:
            # A frontal-only pass can miss faces when a person is lying down
            # or the camera sees the face turned sideways. Check quarter turns
            # too, then map all candidate boxes back to the original frame.
            candidates = []
            views = [(frame, None)]
            if rotated:
                views.extend((
                    (cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE), "cw"),
                    (cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE), "ccw"),
                ))
            for view, rotation in views:
                view_height, view_width = view.shape[:2]
                self.detector.setInputSize((view_width, view_height))
                _, found_faces = self.detector.detect(view)
                self.detection_passes += 1
                for face in ([] if found_faces is None else found_faces):
                    x, y, w, h = map(float, face[:4])
                    if not all(math.isfinite(value) for value in (x, y, w, h)) or w <= 0 or h <= 0:
                        raise MediaError("invalid_face_box")
                    if rotation == "cw":
                        x, y, w, h = y, height - (x + w), h, w
                    elif rotation == "ccw":
                        x, y, w, h = width - (y + h), x, h, w
                    score = float(face[14]) if len(face) > 14 else 0.0
                    if self.mode == "enhanced" and score < (0.75 if rotation else 0.65):
                        continue
                    if self.mode == "scrfd" and score < (0.55 if rotation else 0.5):
                        continue
                    candidates.append((x, y, w, h, score))

            # Rotated passes may find the same face more than once. Keep the
            # strongest overlapping box so the reported count is not inflated.
            return self._deduplicate_faces(candidates)
        except cv2.error as exc:
            raise MediaError("face_detection_failed") from exc
        finally:
            self.metrics["detection_seconds"] += time.perf_counter() - started

    def redact(self, frame):
        faces = self.detect_faces(frame)
        self.trace(self.frame_index, faces, faces, [], 0)
        self.frame_index += 1
        return self.render(frame, faces)

    def render(self, frame, faces):
        started = time.perf_counter()
        result = self.pixelate(frame, faces)
        self.metrics["pixelation_seconds"] += time.perf_counter() - started
        return result

    def trace(self, index, detected, masks, rejected, held):
        if not self.trace_enabled:
            return
        def boxes(rows):
            return [[round(float(value), 2) for value in row] for row in rows]
        logging.getLogger("uvicorn.error").info("media_trace %s", json.dumps({
            "trace_id": self.trace_id, "mode": self.mode, "frame": index, "detected": boxes(detected),
            "masks": boxes(masks), "rejected": boxes(rejected), "held": held,
        }))

    @staticmethod
    def pixelate(frame, faces):
        height, width = frame.shape[:2]
        try:
            output = frame.copy()
            count = 0
            for x, y, w, h, _ in faces:
                # Extra margin includes the face edge and reduces boundary leakage.
                left, top = max(0, int(x - w * 0.3)), max(0, int(y - h * 0.3))
                right, bottom = min(width, math.ceil(x + w * 1.3)), min(height, math.ceil(y + h * 1.3))
                if right <= left or bottom <= top:
                    continue
                roi = output[top:bottom, left:right]
                tiny = cv2.resize(roi, (max(1, roi.shape[1] // 24), max(1, roi.shape[0] // 24)),
                                  interpolation=cv2.INTER_AREA)
                output[top:bottom, left:right] = cv2.resize(
                    tiny, (roi.shape[1], roi.shape[0]), interpolation=cv2.INTER_NEAREST
                )
                count += 1
            return output, count
        except cv2.error as exc:
            raise MediaError("face_detection_failed") from exc

    @staticmethod
    def _deduplicate_faces(candidates):
        kept = []
        for candidate in sorted(candidates, key=lambda face: face[4], reverse=True):
            x, y, w, h, _ = candidate
            duplicate = False
            for other in kept:
                ox, oy, ow, oh, _ = other
                left, top = max(x, ox), max(y, oy)
                right, bottom = min(x + w, ox + ow), min(y + h, oy + oh)
                intersection = max(0.0, right - left) * max(0.0, bottom - top)
                union = w * h + ow * oh - intersection
                if union > 0 and intersection / union >= 0.35:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(candidate)
        return kept


def redact_image(source: Path, output: Path, mode="scrfd") -> dict:
    started = time.perf_counter()
    detector = FaceRedactor(mode)
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    redacted, faces = detector.redact(image)
    if not cv2.imwrite(str(output), redacted):
        raise MediaError("image_encode_failed")
    return _details(detector, faces, 1, started)


def redact_frame(frame, output: Path, mode="scrfd") -> dict:
    started = time.perf_counter()
    detector = FaceRedactor(mode)
    redacted, faces = detector.redact(frame)
    if not cv2.imwrite(str(output), redacted):
        raise MediaError("image_encode_failed")
    return _details(detector, faces, 1, started)


def _details(detector, faces, frames, started):
    return {"faces_detected": faces, "processed_frames": frames,
            "redaction_mode": detector.mode, "inference_backend": detector.backend,
            "processing_seconds": time.perf_counter() - started,
            "profile": {"revision": "tracking-v2", "face_model": "scrfd-10g" if detector.mode == "scrfd" else "yunet-2023mar", "trace_id": detector.trace_id,
                        "cache_reused": detector.cache_reused,
                        "detection_passes": detector.detection_passes, **detector.metrics,
                        **getattr(detector, "tracking_stats", {})}}


def _ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        raise MediaError("ffmpeg_missing") from exc


def redact_video(source: Path, output: Path, work_dir: Path, mode="scrfd") -> dict:
    started = time.perf_counter()
    detector = FaceRedactor(mode)
    capture = cv2.VideoCapture(str(source))
    writer = None
    intermediate = work_dir / "redacted.avi"
    frames, total_faces = 0, 0
    try:
        if not capture.isOpened():
            raise MediaError("video_decode_failed")
        fps = capture.get(cv2.CAP_PROP_FPS)
        expected = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not math.isfinite(fps) or not 1 <= fps <= 120:
            raise MediaError("invalid_video_fps")
        if mode in {"enhanced", "scrfd"}:
            from backend.media.enhanced import TemporalFaceRedactor
            processor = TemporalFaceRedactor(detector, fps)
        else:
            processor = detector
        max_frames = int(fps * float(os.environ.get("PPE_MEDIA_MAX_VIDEO_SECONDS", "60")))
        while True:
            stage_started = time.perf_counter()
            ok, frame = capture.read()
            detector.metrics["decode_seconds"] += time.perf_counter() - stage_started
            if not ok:
                break
            if frames >= max_frames:
                raise MediaError("video_too_long")
            redacted, count = processor.redact(frame)
            stage_started = time.perf_counter()
            if writer is None:
                size = (redacted.shape[1], redacted.shape[0])
                writer = cv2.VideoWriter(str(intermediate), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
                if not writer.isOpened():
                    raise MediaError("video_writer_failed")
            if (redacted.shape[1], redacted.shape[0]) != size:
                raise MediaError("video_resolution_changed")
            writer.write(redacted)
            detector.metrics["intermediate_write_seconds"] += time.perf_counter() - stage_started
            frames += 1
            total_faces += count
        if frames == 0 or (math.isfinite(expected) and expected > 0 and frames < expected - 1):
            raise MediaError("incomplete_video_decode")
    finally:
        capture.release()
        if writer is not None:
            writer.release()
    stage_started = time.perf_counter()
    try:
        subprocess.run([
            _ffmpeg(), "-y", "-i", str(intermediate), "-an", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-movflags", "+faststart", str(output)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaError("video_encode_failed") from exc
    detector.metrics["encoding_seconds"] += time.perf_counter() - stage_started
    if not output.is_file() or output.stat().st_size == 0:
        raise MediaError("video_encode_failed")
    if mode in {"enhanced", "scrfd"}:
        detector.tracking_stats = processor.stats
    return _details(detector, total_faces, frames, started)
