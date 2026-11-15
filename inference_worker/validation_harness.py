"""Swappable scored-detector harness. No API, final judgment or model score synthesis."""
from copy import deepcopy
from math import isfinite
import re
from inference_worker.detection_contract import DetectionContractError,validate_detections
from inference_worker.assessment_pipeline import assess_detection_frame
from inference_worker.person_tracker import PersonTracker
from inference_worker.violation_tracker import ViolationTracker


def validate_manifest(manifest):
    if (not isinstance(manifest,dict) or manifest.get("contract")!="scored_xyxy_v1"
            or manifest.get("confidence_semantics")!="detection_score_only"
            or manifest.get("coordinate_basis")!="original_frame_xyxy"
            or not re.fullmatch("[0-9a-f]{64}",str(manifest.get("artifact_sha256","")))):
        raise DetectionContractError("unsupported_detector_manifest")
    mapping=manifest.get("class_map")
    if not isinstance(mapping,dict) or any(not str(k).isdigit() or not isinstance(v,str) for k,v in mapping.items()):
        raise DetectionContractError("unsupported_detector_class_map")
    if not {"person","helmet"}.issubset(mapping.values()):
        raise DetectionContractError("unsupported_required_classes")
    return {int(k):v for k,v in mapping.items()}


def validate_cached_detections(document):
    """Old cache or a different model cannot be attested as replacement output."""
    metadata=document.get("metadata",{})
    manifest=document.get("detector_contract")
    mapping=validate_manifest(manifest)
    if metadata.get("model_sha256")!=manifest["artifact_sha256"]:
        raise DetectionContractError("cache_artifact_mismatch")
    rows=document.get("detections")
    if not isinstance(rows,list) or len(rows)!=metadata.get("frame_count"):
        raise DetectionContractError("cache_frame_count")
    for frame in rows:validate_detections(frame,class_map=mapping)
    return manifest


def replay_frames(detector,frames,*,mode="review",required_ppe=("helmet","vest"),camera_id="replay"):
    """Strict acceptance harness: invalid replacement IO is rejected, not patched."""
    manifest=deepcopy(detector.contract_manifest)
    mapping=validate_manifest(manifest)
    if mode=="verified_helmet" and "no_helmet" not in mapping.values():
        raise DetectionContractError("negative_class_unsupported")
    if any(p not in ("helmet","vest") for p in required_ppe):
        raise ValueError("unsupported_ppe_policy")
    tracker=PersonTracker();episodes=ViolationTracker(camera_id=camera_id)
    output=[];transitions=[];previous=None
    for timestamp,frame in frames:
        if (isinstance(timestamp,bool) or not isinstance(timestamp,(int,float))
                or not isfinite(timestamp) or timestamp<0
                or (previous is not None and timestamp<=previous)):
            raise ValueError("source_time")
        detections=detector.detect(frame)
        validate_detections(detections,class_map=mapping)
        rows=assess_detection_frame(detections,frame.shape,
            supported_ppe=detector.supported_ppe,mode=mode)
        rows=tracker.update(rows,timestamp)
        for row in rows:
            row["policy_scope_key"]=("replay",tuple(sorted(required_ppe)))
            if "ppe" in row:
                for ppe,state in row["ppe"].items():
                    if ppe not in required_ppe:state["status"]="unknown"
        changes=episodes.update_transitions(rows,timestamp)
        transitions.extend(changes)
        output.append({"timestamp":timestamp,"people":rows})
        previous=timestamp
    transitions.extend(episodes.lifecycle.close_all("primary_source_ended"))
    return {"detector_contract":manifest,"assessment_mode":mode,"required_ppe":list(required_ppe),
            "model_version":detector.model_version,"source_time":"provided_source_seconds",
            "automatic_confirmation_enabled":False,"collection_complete":False,
            "media_delivery_tested":False,"frames":output,"transitions":transitions}
