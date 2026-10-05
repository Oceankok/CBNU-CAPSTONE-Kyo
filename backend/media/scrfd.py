"""SCRFD 10G ONNX face-only adapter; no recognition or identity embeddings.

Output contract documented by InsightFace's SCRFD implementation:
https://github.com/deepinsight/insightface/tree/master/detection/scrfd
Code and pretrained weight licenses differ; see the setup documentation.
"""

import cv2
import numpy as np

from backend.media.enhanced import CudaYuNet, logger
from backend.media.redaction import MediaError


class CudaScrfd(CudaYuNet):
    """Reuse CUDA session setup/locking, replacing YuNet's preprocessing/decoder."""

    def __init__(self, path):
        super().__init__(path)
        self.outputs = [output.name for output in self.session.get_outputs()]
        # The 10G export has score/bbox heads for strides 8,16,32, optionally KPS.
        if len(self.outputs) not in (6, 9) or len(self.input.shape) != 4:
            raise MediaError("scrfd_model_incompatible")
        if self.width <= 0 or self.height <= 0 or self.width % 32 or self.height % 32:
            raise MediaError("scrfd_model_incompatible")
        self.centers = {}
        for stride in (8, 16, 32):
            yy, xx = np.mgrid[:self.height // stride, :self.width // stride]
            self.centers[stride] = np.repeat(
                np.column_stack((xx.ravel(), yy.ravel())).astype(np.float32) * stride,
                2, axis=0,
            )

    def detect(self, frame):
        height, width = frame.shape[:2]
        scale = min(self.width / width, self.height / height)
        rw, rh = max(1, round(width * scale)), max(1, round(height * scale))
        image = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        image[:rh, :rw] = cv2.resize(frame, (rw, rh))
        blob = cv2.dnn.blobFromImage(image, 1 / 128.0, (self.width, self.height),
                                     (127.5, 127.5, 127.5), swapRB=True)
        try:
            with self.run_lock:
                values = self.session.run(self.outputs, {self.input.name: blob})
        except Exception as exc:
            logger.exception("SCRFD CUDA face inference failed")
            raise MediaError("gpu_inference_failed") from exc
        faces = []
        for level, stride in enumerate((8, 16, 32)):
            scores = np.asarray(values[level]).reshape(-1)
            distances = np.asarray(values[level + 3]).reshape(-1, 4) * stride
            centers = self.centers[stride]
            if len(scores) != len(centers) or len(distances) != len(centers):
                raise MediaError("scrfd_model_incompatible")
            selected = np.flatnonzero(np.isfinite(scores) & (scores >= 0.5))
            for index in selected:
                left_d, top_d, right_d, bottom_d = distances[index]
                if not np.isfinite(distances[index]).all() or (distances[index] < 0).any():
                    continue
                cx, cy = centers[index]
                left, top = max(0.0, cx - left_d), max(0.0, cy - top_d)
                right, bottom = min(rw, cx + right_d), min(rh, cy + bottom_d)
                if right <= left or bottom <= top:
                    continue
                row = np.zeros(15, dtype=np.float32)
                row[:4] = (left / scale, top / scale, (right - left) / scale, (bottom - top) / scale)
                row[14] = scores[index]
                faces.append(row)
        if not faces:
            return 1, None
        indices = cv2.dnn.NMSBoxes([row[:4].tolist() for row in faces],
                                  [float(row[14]) for row in faces], 0.5, 0.3, top_k=5000)
        return 1, np.asarray([faces[int(i)] for i in np.asarray(indices).reshape(-1)], dtype=np.float32)
