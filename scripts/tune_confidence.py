"""Evaluate confidence thresholds with emphasis on person recall."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Sequence

from ultralytics import YOLO


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--data", default="configs/merged_ppe.yaml")
    parser.add_argument(
        "--thresholds",
        nargs="+",
        type=float,
        default=[0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40],
    )
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--device", default=None)
    parser.add_argument("--output", type=Path, default=Path("confidence_sweep.csv"))
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    model = YOLO(args.model)
    person_id = next(
        (class_id for class_id, name in model.names.items() if name == "person"),
        None,
    )
    if person_id is None:
        raise ValueError("model does not contain a person class")

    rows = []
    for threshold in args.thresholds:
        metrics = model.val(
            data=args.data,
            imgsz=args.imgsz,
            batch=args.batch,
            conf=threshold,
            iou=args.iou,
            device=args.device,
            plots=False,
            verbose=False,
        )
        person_precision = float(metrics.box.p[person_id])
        person_recall = float(metrics.box.r[person_id])
        person_f1 = (
            2 * person_precision * person_recall / (person_precision + person_recall)
            if person_precision + person_recall
            else 0.0
        )
        row = {
            "confidence": threshold,
            "precision": float(metrics.box.mp),
            "recall": float(metrics.box.mr),
            "map50": float(metrics.box.map50),
            "map50_95": float(metrics.box.map),
            "person_precision": person_precision,
            "person_recall": person_recall,
            "person_f1": person_f1,
        }
        rows.append(row)
        print(
            f"conf={threshold:.2f} person P={person_precision:.3f} "
            f"R={person_recall:.3f} F1={person_f1:.3f}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    best = max(rows, key=lambda row: row["person_f1"])
    print(
        f"best person F1: conf={best['confidence']:.2f}, "
        f"P={best['person_precision']:.3f}, R={best['person_recall']:.3f}"
    )
    print(f"saved: {args.output}")


if __name__ == "__main__":
    main()
