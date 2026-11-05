
"""PPE detection using the trained YOLO model."""

from pathlib import Path
import hashlib

from ultralytics import YOLO


DEFAULT_MODEL = (
    Path(__file__).resolve().parents[1]
    / "Exp01_yolov8n_640_clean_3class-13"
    / "weights"
    / "best.pt"
)


class PPEDetector:
    def __init__(self, model_path=DEFAULT_MODEL, confidence=0.5):
        model_path = Path(model_path)

        if not model_path.is_file():
            raise FileNotFoundError(f"YOLO model not found: {model_path}")

        self.model = YOLO(str(model_path))
        self.confidence = confidence
        with model_path.open("rb") as weights:
            digest = hashlib.file_digest(weights, "sha256").hexdigest()
        self.model_version = "ppe-" + digest[:16]
        self.supports_no_helmet = {"person", "helmet", "no_helmet"}.issubset(self.model.names.values())

    def detect(self, frame):
        results = self.model.predict(
            source=frame,
            conf=self.confidence,
            verbose=False,
        )

        detections = []

        for result in results:
            for box in result.boxes:
                class_id = int(box.cls.item())

                detections.append({
                    "class_id": class_id,
                    "class_name": self.model.names[class_id],
                    "confidence": float(box.conf.item()),
                    "bbox": box.xyxy[0].tolist(),
                })

        return detections
