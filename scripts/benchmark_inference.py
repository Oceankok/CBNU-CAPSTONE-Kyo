"""Compare YOLO weights on the same images/video and report real latency.

Example:
    python scripts/benchmark_inference.py \
      --models runs/ppe/yolo11n/weights/best.pt \
               runs/ppe/yolo11s/weights/best.pt \
               runs/ppe/yolo11m/weights/best.pt \
      --source test_videos/site.mp4 --frames 300 --output benchmark.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import statistics
import time
from typing import Any, Iterator, Sequence

import cv2
from ultralytics import YOLO


IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".webp"}


def iter_frames(source: Path, limit: int, stride: int) -> Iterator[Any]:
    if source.is_dir():
        paths = sorted(
            path for path in source.rglob("*") if path.suffix.lower() in IMAGE_SUFFIXES
        )
        for index, path in enumerate(paths):
            if index % stride == 0:
                frame = cv2.imread(str(path))
                if frame is not None:
                    yield frame
                    limit -= 1
                    if limit == 0:
                        return
        return
    if source.suffix.lower() in IMAGE_SUFFIXES:
        frame = cv2.imread(str(source))
        if frame is not None:
            yield frame
        return

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open source: {source}")
    try:
        frame_index = 0
        emitted = 0
        while emitted < limit:
            ok, frame = capture.read()
            if not ok:
                return
            if frame_index % stride == 0:
                yield frame
                emitted += 1
            frame_index += 1
    finally:
        capture.release()


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def person_count(result: Any, names: dict[int, str] | list[str]) -> int:
    return sum(names[int(class_id)] == "person" for class_id in result.boxes.cls)


def validation_metrics(model: YOLO, args: argparse.Namespace) -> dict[str, float]:
    if not args.data:
        return {}
    metrics = model.val(
        data=args.data,
        imgsz=args.imgsz,
        batch=args.val_batch,
        conf=args.conf,
        iou=args.iou,
        device=args.device,
        verbose=False,
        plots=False,
    )
    output = {
        "precision": float(metrics.box.mp),
        "recall": float(metrics.box.mr),
        "map50": float(metrics.box.map50),
        "map50_95": float(metrics.box.map),
    }
    person_ids = [key for key, value in model.names.items() if value == "person"]
    if person_ids and hasattr(metrics.box, "r"):
        recalls = metrics.box.r
        if len(recalls) > person_ids[0]:
            output["person_recall"] = float(recalls[person_ids[0]])
    return output


def benchmark_model(model_path: str, source: Path, args: argparse.Namespace) -> dict[str, Any]:
    model = YOLO(model_path)
    first_frame = next(iter_frames(source, 1, 1), None)
    if first_frame is None:
        raise RuntimeError(f"no readable frames found: {source}")
    predict_options = {
        "imgsz": args.imgsz,
        "conf": args.conf,
        "iou": args.iou,
        "max_det": args.max_det,
        "device": args.device,
        "verbose": False,
    }
    if args.half:
        predict_options["half"] = True
    for _ in range(args.warmup):
        model.predict(first_frame, **predict_options)

    wall_times: list[float] = []
    detector_times: list[float] = []
    bucket_times: dict[str, list[float]] = {"0": [], "1": [], "2+": []}
    max_people = 0
    for frame in iter_frames(source, args.frames, args.stride):
        started = time.perf_counter()
        result = model.predict(frame, **predict_options)[0]
        wall_ms = (time.perf_counter() - started) * 1000.0
        count = person_count(result, model.names)
        bucket = "0" if count == 0 else "1" if count == 1 else "2+"
        wall_times.append(wall_ms)
        bucket_times[bucket].append(wall_ms)
        detector_times.append(sum(float(value) for value in result.speed.values()))
        max_people = max(max_people, count)

    if not wall_times:
        raise RuntimeError(f"no frames were benchmarked: {source}")
    one_person_ms = statistics.fmean(bucket_times["1"]) if bucket_times["1"] else 0.0
    multi_person_ms = statistics.fmean(bucket_times["2+"]) if bucket_times["2+"] else 0.0
    output: dict[str, Any] = {
        "model": model_path,
        "imgsz": args.imgsz,
        "conf": args.conf,
        "frames": len(wall_times),
        "mean_latency_ms": round(statistics.fmean(wall_times), 3),
        "p50_latency_ms": round(percentile(wall_times, 0.50), 3),
        "p95_latency_ms": round(percentile(wall_times, 0.95), 3),
        "mean_detector_ms": round(statistics.fmean(detector_times), 3),
        "fps": round(1000.0 / statistics.fmean(wall_times), 3),
        "zero_person_ms": round(statistics.fmean(bucket_times["0"]), 3)
        if bucket_times["0"]
        else "",
        "one_person_ms": round(one_person_ms, 3) if one_person_ms else "",
        "multi_person_ms": round(multi_person_ms, 3) if multi_person_ms else "",
        "multi_vs_one_ratio": round(multi_person_ms / one_person_ms, 3)
        if one_person_ms and multi_person_ms
        else "",
        "max_people": max_people,
    }
    output.update(validation_metrics(model, args))
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--imgsz", nargs="+", type=int, default=[640])
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--max-det", type=int, default=100)
    parser.add_argument("--device", default=None)
    parser.add_argument("--half", action="store_true")
    parser.add_argument("--data", help="optional dataset YAML for accuracy metrics")
    parser.add_argument("--val-batch", type=int, default=8)
    parser.add_argument("--output", type=Path, default=Path("benchmark.csv"))
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.frames < 1 or args.stride < 1 or args.warmup < 0:
        raise ValueError("frames/stride must be positive and warmup non-negative")
    rows = []
    image_sizes = args.imgsz
    for model_path in args.models:
        for image_size in image_sizes:
            args.imgsz = image_size
            print(f"[BENCHMARK] {model_path} imgsz={image_size}")
            row = benchmark_model(model_path, args.source, args)
            rows.append(row)
            print(
                f"  {row['fps']} FPS, mean={row['mean_latency_ms']} ms, "
                f"p95={row['p95_latency_ms']} ms, max_people={row['max_people']}"
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with args.output.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    args.output.with_suffix(".json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"saved: {args.output} and {args.output.with_suffix('.json')}")


if __name__ == "__main__":
    main()
