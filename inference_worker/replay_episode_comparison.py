"""Offline comparison on cached detections; never submits events or writes an API.

Baseline modules come from an explicit local Git commit. Cache fingerprints include
video/model SHA256. Source time is frame index / native FPS, not wall clock.
The output is diagnostic and cannot assert collection/media completion.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from types import ModuleType

import cv2

from inference_worker.person_tracker import PersonTracker
from inference_worker.assessment_pipeline import assess_detection_frame
from inference_worker.validation_harness import validate_cached_detections
from inference_worker.ppe_rules import assess_ppe
from inference_worker.violation_tracker import ViolationTracker


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def baseline_module(commit, name):
    source = subprocess.check_output(
        ["git", "show", f"{commit}:inference_worker/{name}.py"], text=True, encoding="utf-8")
    module = ModuleType("baseline_" + name)
    exec(compile(source, f"{commit}/{name}.py", "exec"), module.__dict__)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, action="append", required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assessment-mode",choices=("review","verified_helmet"),default="review")
    args = parser.parse_args()
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", args.baseline_ref+"^{commit}"], text=True).strip()
    old_tracking = baseline_module(commit, "person_tracker")
    old_rules = baseline_module(commit, "ppe_rules")
    old_events = baseline_module(commit, "violation_tracker")
    args.output.mkdir(parents=True, exist_ok=True)
    model_digest = digest(args.model)
    from inference_worker.ppe_detector import PPEDetector
    detector = PPEDetector(args.model)
    if args.assessment_mode=="verified_helmet" and not detector.supports_no_helmet:
        raise ValueError("negative_class_unsupported")
    for video in args.video:
        capture = cv2.VideoCapture(str(video))
        fps = capture.get(cv2.CAP_PROP_FPS)
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        shape = [int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                 int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), 3]
        capture.release()
        metadata = {"video_sha256": digest(video), "model_sha256": model_digest,
                    "fps": fps, "frame_count": total, "shape": shape,
                    "detection_threshold": .1, "ppe_threshold": .5,
                    "assessment_mode":args.assessment_mode,"contract_version":"scored_xyxy_v1"}
        cache = args.output/(video.stem+".detections.json")
        if cache.exists():
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if saved["metadata"] != metadata:
                raise ValueError("Detection cache fingerprint mismatch")
        else:
            from inference_worker.ppe_detector import PPEDetector
            import ultralytics
            if detector is None:
                detector = PPEDetector(args.model)
            capture = cv2.VideoCapture(str(video))
            frames = []
            try:
                for index in range(total):
                    ok, frame = capture.read()
                    if not ok:
                        raise ValueError("Incomplete input decode")
                    frames.append(detector.detect(frame))
                    if index % 150 == 0:
                        print(f"{video.name}: detected {index}/{total}", flush=True)
            finally:
                capture.release()
            saved = {"metadata": metadata,"detector_contract":detector.contract_manifest,
                     "model_version": detector.model_version,
                     "device": str(detector.model.predictor.device),
                     "ultralytics": ultralytics.__version__, "detections": frames}
            cache.write_text(json.dumps(saved, allow_nan=False), encoding="utf-8")
        validate_cached_detections(saved)
        outputs = {}
        for variant in ("baseline", "updated_strong_only", "updated_weak_tracking"):
            tracker = old_tracking.PersonTracker() if variant == "baseline" else PersonTracker()
            timers = {policy: old_events.ViolationTracker() if variant == "baseline"
                      else ViolationTracker(camera_id=video.stem)
                      for policy in ("helmet", "helmet_vest")}
            events, transitions, assessments = [], [], []
            for index, detections in enumerate(saved["detections"]):
                timestamp = index/fps
                strong = [d for d in detections if d["confidence"] >= .5]
                input_rows = strong if variant != "updated_weak_tracking" else detections
                if variant == "baseline":
                    old_assessment=(old_rules.assess_ppe(input_rows,shape) if args.assessment_mode=="review"
                                    else old_rules.assess_helmets(input_rows))
                    people = tracker.update(old_assessment)
                else:
                    people = tracker.update(assess_detection_frame(input_rows,shape,
                        supported_ppe=detector.supported_ppe,mode=args.assessment_mode),timestamp)
                assessments.append({"t": timestamp, "people": people})
                for policy, timer in timers.items():
                    rows = deepcopy(people)
                    for row in rows:
                        row["policy_scope_key"] = ("replay", policy)
                        if policy == "helmet" and "ppe" in row:
                            row["ppe"]["vest"]["status"] = "unknown"
                    if variant == "baseline":
                        candidates = timer.update(rows, timestamp)
                    else:
                        changes = timer.update_transitions(rows, timestamp)
                        transitions.extend({"policy": policy, **change,
                            "model_version": saved["model_version"]} for change in changes)
                        candidates = [c for c in changes if c["kind"] == "start"]
                    events.extend({"policy": policy, **candidate} for candidate in candidates)
            if variant != "baseline":
                for policy, timer in timers.items():
                    transitions.extend({"policy": policy, **change,
                        "model_version": saved["model_version"]}
                        for change in timer.lifecycle.close_all("primary_source_ended"))
            detail = {"events": events, "transitions": transitions, "assessments": assessments}
            (args.output/(video.stem+"."+variant+".json")).write_text(
                json.dumps(detail, allow_nan=False), encoding="utf-8")
            outputs[variant] = {
                "events": events,
                "unique_track_ids": sorted({p["tracking_id"] for f in assessments for p in f["people"]}),
                "close": [c for c in transitions if c["kind"] == "close"],
            }
        result = {"source": video.name, "metadata": metadata, "baseline_commit": commit,
                  "source_time": "frame_index / native_fps; relative seconds, no shared UTC calibration",
                  "model_version": saved["model_version"], "device": saved["device"],
                  "detector_contract":saved["detector_contract"],"assessment_mode":args.assessment_mode,
                  "ultralytics": saved["ultralytics"],
                  "settings": {"threshold_seconds":3, "missing_grace_seconds":1,
                               "rewear_seconds":.5,"qualification_grace_seconds":.5,
                               "qualification_window_seconds":6,"max_sample_gap_seconds":.5},
                  "collection_complete": False, "media_delivery_tested": False,
                  "variants": outputs}
        destination = args.output/(video.stem+".summary.json")
        destination.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps({"video":video.name,
              "events": {k:[(e["policy"],e["ppe_type"],round(e["end_time"],3),e["tracking_id"])
                            for e in v["events"]] for k,v in outputs.items()}}),flush=True)


if __name__ == "__main__":
    main()
