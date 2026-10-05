"""CUDA YuNet inference and short, single-video face-mask continuity.

This does not identify people or associate PPE/cameras. CPU optical flow moves
existing masks between CUDA detections. Confirmed tracks can bridge 0.8s of
missing detections with valid flow; failed flow has only a 0.12s prediction grace.
"""

import logging
import math
import time
from threading import Lock

import cv2
import numpy as np

from backend.media.redaction import MediaError

logger = logging.getLogger(__name__)
_cache_lock = Lock()
_cached_detectors = {}


def get_cuda_detector(path, model="yunet"):
    """One immutable inference adapter per server process; tracks stay per video."""
    stat = path.stat()
    key = (model, str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        entry = _cached_detectors.get(model)
        if entry is not None and entry[0] == key:
            return entry[1], True
        if model == "scrfd":
            from backend.media.scrfd import CudaScrfd
            detector = CudaScrfd(path)
        else:
            detector = CudaYuNet(path)
        _cached_detectors[model] = (key, detector)
        return detector, False


class CudaYuNet:
    """FaceDetectorYN-compatible adapter using the existing verified ONNX file."""

    def __init__(self, path):
        self.run_lock = Lock()
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise MediaError("gpu_runtime_missing") from exc
        try:
            # Windows can reuse the CUDA/cuDNN DLLs shipped with CUDA PyTorch.
            try:
                import torch  # noqa: F401
            except ImportError:
                pass
            if hasattr(ort, "preload_dlls"):
                ort.preload_dlls()
            if "CUDAExecutionProvider" not in ort.get_available_providers():
                raise MediaError("gpu_provider_unavailable")
            options = ort.SessionOptions()
            self.session = ort.InferenceSession(
                str(path), sess_options=options,
                providers=[("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"})],
            )
            if self.session.get_providers()[0] != "CUDAExecutionProvider":
                raise MediaError("gpu_provider_unavailable")
            self.session.disable_fallback()
            self.input = self.session.get_inputs()[0]
            shape = self.input.shape
            self.height = shape[2] if isinstance(shape[2], int) else 640
            self.width = shape[3] if isinstance(shape[3], int) else 640
            self.outputs = [f"{name}_{stride}" for name in ("cls", "obj", "bbox") for stride in (8, 16, 32)]
        except MediaError:
            raise
        except Exception as exc:
            logger.exception("CUDA face session could not start")
            raise MediaError("gpu_runtime_initialization_failed") from exc

    def setInputSize(self, size):
        # OpenCV's adapter accepts arbitrary frame sizes; this ONNX adapter
        # letterboxes into the graph's declared shape instead.
        pass

    def detect(self, frame):
        height, width = frame.shape[:2]
        scale = min(self.width / width, self.height / height)
        resized_width = max(1, round(width * scale))
        resized_height = max(1, round(height * scale))
        image = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        image[:resized_height, :resized_width] = cv2.resize(frame, (resized_width, resized_height))
        # YuNet consumes BGR values without mean subtraction or normalization.
        blob = np.ascontiguousarray(image.transpose(2, 0, 1)[None], dtype=np.float32)
        try:
            # Serialize inference, while preprocessing and video state remain
            # request-local. A queued request includes this wait in detection time.
            with self.run_lock:
                predictions = dict(zip(self.outputs, self.session.run(self.outputs, {self.input.name: blob})))
        except Exception as exc:
            logger.exception("CUDA face inference failed")
            raise MediaError("gpu_inference_failed") from exc
        faces = []
        for stride in (8, 16, 32):
            cls = predictions[f"cls_{stride}"].reshape(-1)
            obj = predictions[f"obj_{stride}"].reshape(-1)
            boxes = predictions[f"bbox_{stride}"].reshape(-1, 4)
            scores = np.sqrt(np.clip(cls, 0, 1) * np.clip(obj, 0, 1))
            cols = self.width // stride
            for index in np.flatnonzero(scores >= 0.65):
                offsets = boxes[index]
                if not np.isfinite(offsets).all():
                    continue
                bw, bh = np.exp(np.clip(offsets[2:4], -20, 20)) * stride
                cx = (index % cols + offsets[0]) * stride
                cy = (index // cols + offsets[1]) * stride
                left, top = max(0.0, cx - bw / 2), max(0.0, cy - bh / 2)
                right, bottom = min(resized_width, cx + bw / 2), min(resized_height, cy + bh / 2)
                if right <= left or bottom <= top:
                    continue
                row = np.zeros(15, dtype=np.float32)
                row[:4] = (left / scale, top / scale, (right - left) / scale, (bottom - top) / scale)
                row[14] = scores[index]
                faces.append(row)
        if not faces:
            return 1, None
        boxes = [face[:4].tolist() for face in faces]
        scores = [float(face[14]) for face in faces]
        indices = cv2.dnn.NMSBoxes(boxes, scores, 0.65, 0.3, top_k=5000)
        return 1, np.asarray([faces[int(i)] for i in np.asarray(indices).reshape(-1)], dtype=np.float32)


def _iou(first, second):
    x, y, w, h = first[:4]
    ox, oy, ow, oh = second[:4]
    intersection = max(0, min(x + w, ox + ow) - max(x, ox)) * max(0, min(y + h, oy + oh) - max(y, oy))
    union = w * h + ow * oh - intersection
    return intersection / union if union > 0 else 0


class TemporalFaceRedactor:
    def __init__(self, detector, fps):
        self.detector = detector
        self.hold_frames = max(1, round(fps * 0.8))
        self.prediction_frames = max(1, round(fps * 0.12))
        self.rotation_interval = max(3, round(fps * 0.4))
        self.index = 0
        self.previous = None
        self.tracks = []
        self.scale = np.ones(2, dtype=np.float32)
        self.stats = {"held_regions": 0, "rejected_size_jumps": 0,
                      "expired_tracks": 0, "scene_cuts": 0}

    def _points(self, gray, box):
        height, width = gray.shape
        x, y, w, h = np.asarray(box[:4]) * np.tile(self.scale, 2)
        left, top = max(0, math.floor(x)), max(0, math.floor(y))
        right, bottom = min(width, math.ceil(x + w)), min(height, math.ceil(y + h))
        if right <= left or bottom <= top:
            return None
        # Work on the face crop, not full-frame gradients with a selection mask.
        points = cv2.goodFeaturesToTrack(gray[top:bottom, left:right], maxCorners=30,
                                        qualityLevel=0.03, minDistance=3)
        if points is not None:
            points += np.array([left, top], dtype=np.float32)
        return points

    def _move_tracks(self, gray):
        owners, groups = [], []
        for track in self.tracks:
            points = track["points"]
            if points is not None and len(points) >= 3:
                owners.append(track)
                groups.append(points)
            track["flow_ok"] = False
        if groups:
            points = np.concatenate(groups)
            # One forward/backward pair for every face together, at reduced size.
            forward, ok, _ = cv2.calcOpticalFlowPyrLK(self.previous, gray, points, None)
            if forward is not None:
                backward, back_ok, _ = cv2.calcOpticalFlowPyrLK(gray, self.previous, forward, None)
                if backward is not None:
                    good = (ok.reshape(-1) == 1) & (back_ok.reshape(-1) == 1)
                    good &= np.linalg.norm((backward - points).reshape(-1, 2), axis=1) < 1.5
                    offset = 0
                    for track, group in zip(owners, groups):
                        stop = offset + len(group)
                        valid = good[offset:stop]
                        if np.count_nonzero(valid) >= max(3, math.ceil(len(group) * 0.5)):
                            shifts = (forward[offset:stop] - group).reshape(-1, 2)[valid] / self.scale
                            delta = np.median(shifts, axis=0)
                            spread = np.median(np.linalg.norm(shifts - delta, axis=1))
                            extent = max(track["box"][2:4])
                            if (np.isfinite(delta).all() and np.linalg.norm(delta) < extent * 0.6
                                    and spread < max(2, extent * 0.15)):
                                track["velocity"] = delta
                                track["points"] = forward[offset:stop][valid].copy()
                                track["flow_ok"] = True
                        offset = stop
        for track in self.tracks:
            if track["flow_ok"]:
                track["lost"] = 0
                delta = track["velocity"]
            else:
                track["lost"] += 1
                # Do not acquire new shirt/background features after losing the
                # face. Only a fresh detector match may seed points again.
                track["points"] = None
                delta = track["velocity"] * (0.5 ** track["lost"])
            track["box"][0] += float(delta[0])
            track["box"][1] += float(delta[1])

    @staticmethod
    def _compatible(box, face):
        area_ratio = face[2] * face[3] / (box[2] * box[3])
        distance = math.hypot(face[0] + face[2] / 2 - box[0] - box[2] / 2,
                              face[1] + face[3] / 2 - box[1] - box[3] / 2)
        return (0.5 <= area_ratio <= 2.0 and _iou(box, face) >= 0.2
                and distance <= 0.6 * math.sqrt(box[2] * box[3]))

    @staticmethod
    def _engulfs_track(face, track):
        if track["hits"] < 2:
            return False
        box = track["box"]
        area = box[2] * box[3]
        intersection = max(0, min(face[0] + face[2], box[0] + box[2]) - max(face[0], box[0])) * max(
            0, min(face[1] + face[3], box[1] + box[3]) - max(face[1], box[1]))
        return face[2] * face[3] > area * 2.5 and intersection / area >= 0.8

    def redact(self, frame):
        started = time.perf_counter()
        height, width = frame.shape[:2]
        ratio = min(1, 640 / max(height, width))
        size = (max(1, round(width * ratio)), max(1, round(height * ratio)))
        self.scale = np.array([size[0] / width, size[1] / height], dtype=np.float32)
        gray = cv2.cvtColor(cv2.resize(frame, size), cv2.COLOR_BGR2GRAY)
        scene_cut = self.previous is not None and (
            gray.shape != self.previous.shape or
            float(np.mean(cv2.absdiff(gray, self.previous))) > 45
        )
        if scene_cut:
            self.tracks = []
            self.previous = None
            self.stats["scene_cuts"] += 1
        if self.previous is not None:
            self._move_tracks(gray)
        before = len(self.tracks)
        self.tracks = [t for t in self.tracks if self.index - t["seen"] <= self.hold_frames
                       and t["lost"] <= self.prediction_frames
                       and (t["hits"] >= 2 or self.index - t["seen"] <= 1)]
        self.stats["expired_tracks"] += before - len(self.tracks)
        rotate = (self.index % self.rotation_interval == 0 or scene_cut or
                  any(t["lost"] == 2 for t in self.tracks))
        # Run upright detection on every frame so a newly entering face is not
        # deliberately skipped. Extra rotations are periodic or loss-triggered.
        self.detector.metrics["tracking_seconds"] += time.perf_counter() - started
        faces = self.detector.detect_faces(frame, rotated=rotate)
        started = time.perf_counter()
        detected = list(faces)
        unmatched = list(self.tracks)
        rejected = []
        for face in faces:
            compatible = [t for t in unmatched if self._compatible(t["box"], face)]
            match = max(compatible, key=lambda t: _iou(t["box"], face), default=None)
            if match is not None:
                unmatched = [t for t in unmatched if t is not match]
                match.update(box=list(face), seen=self.index, lost=0, hits=match["hits"] + 1)
                match["points"] = self._points(gray, face)
            elif any(self._engulfs_track(face, t) for t in self.tracks):
                rejected.append(face)
                self.stats["rejected_size_jumps"] += 1
            else:
                self.tracks.append({"box": list(face), "seen": self.index,
                                    "velocity": np.zeros(2), "lost": 0, "hits": 1,
                                    "points": self._points(gray, face)})
        # New detections are masked immediately, but one-frame detections do
        # not gain the long hold period until seen again.
        active = [t for t in self.tracks if t["seen"] == self.index or t["hits"] >= 2]
        held = sum(t["seen"] != self.index for t in active)
        self.stats["held_regions"] += held
        self.previous = gray
        self.index += 1
        self.detector.metrics["tracking_seconds"] += time.perf_counter() - started
        self.detector.trace(self.index - 1, detected, [t["box"] for t in active], rejected, held)
        return self.detector.render(frame, [t["box"] for t in active])
