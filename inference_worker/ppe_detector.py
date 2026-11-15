
"""PPE detection using the trained YOLO model."""

from pathlib import Path
import hashlib
from inference_worker.detection_contract import DetectionContractError, unit_score, validate_detections

from ultralytics import YOLO


DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "best.pt"


class PPEDetector:
    def __init__(self, model_path=DEFAULT_MODEL, confidence=0.5):
        model_path = Path(model_path)

        if not model_path.is_file():
            raise FileNotFoundError(f"YOLO model not found: {model_path}")

        if not unit_score(confidence) or confidence == 0:
            raise ValueError("detector confidence threshold")
        self.model = YOLO(str(model_path))
        self.confidence = confidence
        if (not isinstance(self.model.names, dict)
                or any(type(k) is not int or k < 0 or not isinstance(v,str) or not v
                       for k,v in self.model.names.items())):
            raise ValueError("current detector class map required")
        names = set(self.model.names.values())
        if not {"person", "helmet"}.issubset(names):
            raise ValueError(f"PPE model must contain person and helmet: {self.model.names}")
        self.supported_ppe = {ppe for ppe in ("helmet", "vest") if ppe in names}
        with model_path.open("rb") as weights:
            digest = hashlib.file_digest(weights, "sha256").hexdigest()
        self.model_version = "ppe-" + digest[:16]
        self.contract_manifest = {"contract":"scored_xyxy_v1", "artifact_sha256":digest,
            "class_map":dict(self.model.names), "supported_ppe":sorted(self.supported_ppe),
            "confidence_semantics":"detection_score_only", "coordinate_basis":"original_frame_xyxy",
            "person_prediction_threshold":min(confidence,.1),"ppe_threshold":confidence}
        self.supports_no_helmet = {"person", "helmet", "no_helmet"}.issubset(self.model.names.values())

    def detect(self, frame):
        results = self.model.predict(
            source=frame,
            # Keep weak people for tracking; PPE evidence retains its threshold.
            conf=min(self.confidence, 0.1),
            verbose=False,
        )

        detections = []

        try:
            for result in results:
                for box in result.boxes:
                    raw_class = box.cls.item()
                    if isinstance(raw_class, bool) or raw_class != int(raw_class):
                        raise DetectionContractError("detector_class_id")
                    class_id = int(raw_class)
                    row = {"class_id":class_id, "class_name":self.model.names[class_id],
                           "confidence":box.conf.item(), "bbox":box.xyxy[0].tolist()}
                    # Validate BEFORE threshold filtering: an invalid score must
                    # not disappear and leave a false absence behind.
                    validate_detections([row], class_map=self.model.names)
                    row["confidence"] = float(row["confidence"])
                    if row["class_name"] != "person" and row["confidence"] < self.confidence:
                        continue
                    detections.append(row)
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError, IndexError) as error:
            raise DetectionContractError("unsupported_detector_output") from error
        return detections
