"""Train a reproducible Nano/Small/Medium PPE experiment matrix."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from ultralytics import YOLO


def parse_batch(value: str) -> int | float:
    return float(value) if "." in value else int(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="configs/merged_ppe.yaml")
    parser.add_argument(
        "--models",
        nargs="+",
        default=["yolo11n.pt", "yolo11s.pt", "yolo11m.pt"],
        help="base weights; all models are trained with identical settings",
    )
    parser.add_argument("--imgsz", nargs="+", type=int, default=[640])
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument(
        "--batch",
        type=parse_batch,
        default=-1,
        help="-1 uses automatic GPU batch sizing; a fraction uses that GPU-memory share",
    )
    parser.add_argument("--patience", type=int, default=30)
    parser.add_argument("--device", default=None)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--project", default="runs/ppe/model_comparison")
    parser.add_argument("--cache", choices=["none", "ram", "disk"], default="none")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    data_path = Path(args.data)
    if not data_path.exists():
        raise FileNotFoundError(f"데이터셋 설정 파일을 찾을 수 없습니다: {data_path}")

    cache: bool | str = False if args.cache == "none" else args.cache
    for base_model in args.models:
        for image_size in args.imgsz:
            run_name = f"{Path(base_model).stem}_img{image_size}_seed{args.seed}"
            settings = {
                "data": str(data_path),
                "epochs": args.epochs,
                "patience": args.patience,
                "imgsz": image_size,
                "batch": args.batch,
                "device": args.device,
                "workers": args.workers,
                "seed": args.seed,
                "deterministic": True,
                "amp": True,
                "cache": cache,
                "close_mosaic": 10,
                "project": args.project,
                "name": run_name,
                "exist_ok": False,
            }
            print(f"[TRAIN] model={base_model} name={run_name} settings={settings}")
            if args.dry_run:
                continue
            YOLO(base_model).train(**settings)


if __name__ == "__main__":
    main()
