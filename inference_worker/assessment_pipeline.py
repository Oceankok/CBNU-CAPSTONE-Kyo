"""Shared production/replay mode routing over the existing detector contract."""
import logging
from inference_worker.detection_contract import DetectionContractError, unknown_people, validate_detections
from inference_worker.ppe_rules import assess_ppe, assess_helmets


def assess_detection_frame(detections, shape, *, supported_ppe=("helmet", "vest"), mode="review"):
    if mode not in ("review", "verified_helmet"):
        raise ValueError("assessment_mode")
    try:
        validate_detections(detections)
    except DetectionContractError:
        return unknown_people(detections)
    return (assess_ppe(detections, shape, supported_ppe) if mode == "review"
            else assess_helmets(detections))


def detect_assess(detector, frame, *, mode="review"):
    try:
        detections = detector.detect(frame)
    except DetectionContractError:
        logging.getLogger(__name__).warning("Detector observation failed scored-xyxy contract")
        # Adapter could not produce any trustworthy scored measurement.
        return []
    return assess_detection_frame(detections, frame.shape,
        supported_ppe=getattr(detector, "supported_ppe", ("helmet", "vest")), mode=mode)
