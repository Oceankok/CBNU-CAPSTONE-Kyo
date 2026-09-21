"""Audit a YOLO detection dataset before training.

Checks missing/invalid labels, class balance, multi-person coverage and
person/head-PPE co-occurrence.  The JSON output can be kept with experiment
results to explain recall changes between dataset versions.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any, Sequence

import yaml


IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def resolve_split(config_path: Path, data: dict[str, Any], split: str) -> list[Path]:
    value = data.get(split)
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    root_value = data.get("path")
    root = (
        (config_path.parent / root_value).resolve()
        if root_value
        else config_path.parent.resolve()
    )
    return [
        path if path.is_absolute() else (root / path).resolve()
        for path in (Path(item) for item in values)
    ]


def label_path_for(image_path: Path) -> Path:
    parts = list(image_path.parts)
    try:
        index = len(parts) - 1 - parts[::-1].index("images")
        parts[index] = "labels"
        return Path(*parts).with_suffix(".txt")
    except ValueError:
        return image_path.with_suffix(".txt")


def read_label_file(
    label_path: Path,
    class_count: int,
) -> tuple[list[int], list[str]]:
    classes: list[int] = []
    errors: list[str] = []
    for line_number, line in enumerate(
        label_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        values = line.split()
        location = f"{label_path}:{line_number}"
        if len(values) != 5:
            errors.append(f"{location}: expected 5 values, got {len(values)}")
            continue
        try:
            class_id = int(values[0])
            x, y, width, height = (float(value) for value in values[1:])
        except ValueError:
            errors.append(f"{location}: non-numeric label")
            continue
        if not 0 <= class_id < class_count:
            errors.append(f"{location}: class {class_id} outside [0, {class_count - 1}]")
            continue
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            errors.append(f"{location}: non-finite box")
            continue
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
            errors.append(f"{location}: invalid normalized box")
            continue
        if x - width / 2 < 0 or x + width / 2 > 1:
            errors.append(f"{location}: box crosses horizontal image boundary")
        if y - height / 2 < 0 or y + height / 2 > 1:
            errors.append(f"{location}: box crosses vertical image boundary")
        classes.append(class_id)
    return classes, errors


def audit_split(
    directories: Sequence[Path], names: dict[int, str]
) -> dict[str, Any]:
    missing_directories = [str(directory) for directory in directories if not directory.exists()]
    images = sorted(
        path
        for directory in directories
        if directory.exists()
        for path in directory.rglob("*")
        if path.suffix.lower() in IMAGE_SUFFIXES
    )
    class_counts: Counter[int] = Counter()
    missing_labels: list[str] = []
    empty_labels: list[str] = []
    errors: list[str] = []
    multi_person_images = 0
    person_without_head_ppe = 0
    head_ppe_without_person = 0
    person_id = next((key for key, value in names.items() if value == "person"), None)
    head_ppe_ids = {
        key for key, value in names.items() if value in {"helmet", "no_helmet"}
    }

    for image_path in images:
        label_path = label_path_for(image_path)
        if not label_path.exists():
            missing_labels.append(str(image_path))
            continue
        classes, label_errors = read_label_file(label_path, len(names))
        errors.extend(label_errors)
        if not classes:
            empty_labels.append(str(label_path))
            continue
        per_image = Counter(classes)
        class_counts.update(per_image)
        people = per_image.get(person_id, 0) if person_id is not None else 0
        head_ppe = sum(per_image.get(class_id, 0) for class_id in head_ppe_ids)
        multi_person_images += people >= 2
        person_without_head_ppe += people > 0 and head_ppe == 0
        head_ppe_without_person += people == 0 and head_ppe > 0

    return {
        "missing_directory_count": len(missing_directories),
        "image_count": len(images),
        "labeled_image_count": len(images) - len(missing_labels),
        "missing_label_count": len(missing_labels),
        "empty_label_count": len(empty_labels),
        "invalid_label_count": len(errors),
        "class_counts": {names[key]: class_counts.get(key, 0) for key in names},
        "multi_person_image_count": multi_person_images,
        "person_without_head_ppe_count": person_without_head_ppe,
        "head_ppe_without_person_count": head_ppe_without_person,
        "examples": {
            "missing_directories": missing_directories,
            "missing_labels": missing_labels[:20],
            "empty_labels": empty_labels[:20],
            "invalid_labels": errors[:50],
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("configs/merged_ppe.yaml"))
    parser.add_argument("--output", type=Path, default=Path("dataset_audit.json"))
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config_path = args.data.resolve()
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw_names = data["names"]
    names = (
        {int(key): value for key, value in raw_names.items()}
        if isinstance(raw_names, dict)
        else dict(enumerate(raw_names))
    )
    report = {
        "data": str(config_path),
        "classes": names,
        "splits": {
            split: audit_split(resolve_split(config_path, data, split), names)
            for split in ("train", "val", "test")
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"saved: {args.output}")
    failures = sum(
        values[metric]
        for values in report["splits"].values()
        for metric in (
            "missing_directory_count",
            "missing_label_count",
            "empty_label_count",
            "invalid_label_count",
        )
    )
    if args.strict and failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
