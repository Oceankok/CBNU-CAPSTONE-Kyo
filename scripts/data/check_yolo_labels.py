from __future__ import annotations

import argparse
from pathlib import Path


VALID_CLASSES = {0, 1, 2}


def check_label_file(label_path: Path) -> list[str]:
    errors = []

    for line_no, line in enumerate(label_path.read_text(encoding="utf-8").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue

        parts = raw.split()

        if len(parts) != 5:
            errors.append(f"{label_path}:{line_no} invalid column count -> {raw}")
            continue

        try:
            cls = int(parts[0])
            x, y, w, h = map(float, parts[1:])
        except ValueError:
            errors.append(f"{label_path}:{line_no} non numeric value -> {raw}")
            continue

        if cls not in VALID_CLASSES:
            errors.append(f"{label_path}:{line_no} invalid class id -> {raw}")

        if not all(0.0 <= v <= 1.0 for v in [x, y, w, h]):
            errors.append(f"{label_path}:{line_no} bbox out of range -> {raw}")

        if w <= 0 or h <= 0:
            errors.append(f"{label_path}:{line_no} invalid bbox size -> {raw}")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label-dir", type=Path, required=True)
    args = parser.parse_args()

    label_files = sorted(args.label_dir.rglob("*.txt"))

    all_errors = []

    for label_path in label_files:
        all_errors.extend(check_label_file(label_path))

    if not all_errors:
        print("OK: all labels are valid.")
        print(f"checked files: {len(label_files)}")
        return

    print(f"Found {len(all_errors)} errors.")
    for err in all_errors[:100]:
        print(err)


if __name__ == "__main__":
    main()