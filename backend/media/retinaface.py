"""CUDA adapter for the raw loc/conf/landmark RetinaFace ResNet50 ONNX export.

Contract: biubug6/Pytorch_Retinaface (MIT), config cfg_re50, test-phase
probabilities, BGR mean (104,117,123), anchors at strides 8/16/32.
No recognition, downloads, pickle loading or CPU fallback.
"""
import hashlib
import json
import math
import os
from pathlib import Path
from threading import Lock

import cv2
import numpy as np

from backend.media.paths import PROJECT_ROOT
from backend.media.redaction import MediaError

MODEL = 'retinaface-resnet50-raw-640'
_cache_lock = Lock()
_cached = None


def model_path():
    return Path(os.environ.get('PPE_RETINAFACE_MODEL_PATH', str(
        PROJECT_ROOT / 'models/privacy/retinaface_resnet50_640.onnx')))


def configured_detector():
    setting = os.environ.get('PPE_MEDIA_FACE_VERIFIER', 'off')
    if setting == 'off':
        return None
    if setting != 'retinaface':
        raise MediaError('face_verifier_configuration_invalid')
    path = model_path()
    try:
        manifest = json.loads(path.with_suffix('.manifest.json').read_text(encoding='utf-8'))
        if manifest['model'] != MODEL:
            raise ValueError()
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if manifest['sha256'] != digest:
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MediaError('face_verifier_model_invalid') from exc
    global _cached
    with _cache_lock:
        key = (str(path.resolve()), digest)
        if _cached is None or _cached[0] != key:
            _cached = (key, RetinaFace(path))
        return _cached[1]


def priors(size=640):
    anchors = []
    for step, sizes in zip((8, 16, 32), ((16, 32), (64, 128), (256, 512))):
        for y in range(math.ceil(size / step)):
            for x in range(math.ceil(size / step)):
                for minimum in sizes:
                    anchors.append(((x + .5) * step / size, (y + .5) * step / size,
                                    minimum / size, minimum / size))
    return np.asarray(anchors, dtype=np.float32)


class RetinaFace:
    def __init__(self, path):
        self.lock = Lock()
        self.anchors = priors()
        try:
            import onnxruntime as ort
            try:
                import torch  # noqa: F401 -- make installed CUDA DLLs available
            except ImportError:
                pass
            if hasattr(ort, 'preload_dlls'):
                ort.preload_dlls()
            if 'CUDAExecutionProvider' not in ort.get_available_providers():
                raise RuntimeError('CUDA unavailable')
            self.session = ort.InferenceSession(str(path), providers=['CUDAExecutionProvider'])
            self.session.disable_fallback()
            if self.session.get_providers()[0] != 'CUDAExecutionProvider':
                raise RuntimeError('CUDA unavailable')
            inputs = self.session.get_inputs()
            if len(inputs) != 1 or inputs[0].shape != [1, 3, 640, 640] or inputs[0].type != 'tensor(float)':
                raise ValueError('input contract')
            self.input_name = inputs[0].name
            outputs = self.session.get_outputs()
            if len(outputs) != 3:
                raise ValueError('output contract')
        except Exception as exc:
            raise MediaError('face_verifier_model_invalid') from exc

    def detect(self, frame):
        from backend.processing.priority import yield_to_inference
        yield_to_inference()
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise MediaError('invalid_frame')
        height, width = frame.shape[:2]
        if not width or not height:
            raise MediaError('invalid_frame')
        scale = 640 / max(height, width)
        scaled_w, scaled_h = max(1, round(width * scale)), max(1, round(height * scale))
        # Top-left letterbox with mean-valued padding (zero after normalization).
        image = np.full((640, 640, 3), (104, 117, 123), dtype=np.float32)
        image[:scaled_h, :scaled_w] = cv2.resize(frame, (scaled_w, scaled_h))
        tensor = (image - np.array((104, 117, 123), dtype=np.float32)).transpose(2, 0, 1)[None]
        try:
            with self.lock:
                result = self.session.run(None, {self.input_name: np.ascontiguousarray(tensor)})
            heads = {array.shape[-1]: array for array in result if isinstance(array, np.ndarray) and array.ndim == 3}
            count = len(self.anchors)
            if (set(heads) != {2, 4, 10} or any(a.shape != (1, count, n) or not np.isfinite(a).all()
                                              for n, a in heads.items())):
                raise ValueError('invalid output contract')
            confidence = heads[2][0]
            if np.any(confidence < 0) or np.any(confidence > 1) or not np.allclose(confidence.sum(axis=1), 1, atol=.01):
                raise ValueError('expected probabilities')
            scores = confidence[:, 1]
            selected = np.flatnonzero(scores >= .5)
            selected = selected[np.argsort(scores[selected])[::-1]][:5000]
            loc, anchor = heads[4][0][selected], self.anchors[selected]
            centers = anchor[:, :2] + loc[:, :2] * .1 * anchor[:, 2:]
            with np.errstate(over='raise', invalid='raise'):
                sizes = anchor[:, 2:] * np.exp(loc[:, 2:] * .2)
            boxes = np.concatenate((centers - sizes / 2, centers + sizes / 2), axis=1) * 640
            boxes[:, [0, 2]] *= width / scaled_w
            boxes[:, [1, 3]] *= height / scaled_h
            boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, width)
            boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, height)
            boxes[:, 2:] -= boxes[:, :2]
            if not np.isfinite(boxes).all():
                raise ValueError('invalid boxes')
            valid = (boxes[:, 2] > 0) & (boxes[:, 3] > 0)
            boxes, scores = boxes[valid], scores[selected][valid]
            if not len(boxes):
                return []
            keep = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), .5, .3)
            return [(*map(float, boxes[i]), float(scores[i])) for i in np.asarray(keep).reshape(-1)]
        except Exception as exc:
            raise MediaError('face_verification_failed') from exc
