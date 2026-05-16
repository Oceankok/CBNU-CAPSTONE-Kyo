from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def normalize(value: Any) -> str:
    return str(value).strip()


def find_annotations(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        candidate_keys = [
            "annotations",
            "annotation",
            "objects",
            "object",
            "labels",
            "label",
            "shapes",
            "instances",
            "data",
        ]

        for key in candidate_keys:
            value = data.get(key)

            if isinstance(value, list):
                return [v for v in value if isinstance(v, dict)]

            if isinstance(value, dict):
                for inner_key in candidate_keys:
                    inner_value = value.get(inner_key)
                    if isinstance(inner_value, list):
                        return [v for v in inner_value if isinstance(v, dict)]

        for value in data.values():
            if isinstance(value, dict):
                found = find_annotations(value)
                if found:
                    return found

    if isinstance(data, list):
        return [v for v in data if isinstance(v, dict)]

    return []


def extract_label_candidates(obj: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "label",
        "class",
        "class_name",
        "category",
        "category_name",
        "name",
        "type",
        "object_class",
        "object_name",
        "class_id",
        "category_id",
        "label_id",
        "code",
    ]

    result = {}

    for key in keys:
        if key in obj:
            result[key] = obj[key]

    attrs = obj.get("attributes")
    if isinstance(attrs, dict):
        for key in keys:
            if key in attrs:
                result[f"attributes.{key}"] = attrs[key]

    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label-dir", type=Path, required=True)
    parser.add_argument("--limit-samples", type=int, default=5)
    args = parser.parse_args()

    json_files = sorted(args.label_dir.rglob("*.json"))

    if not json_files:
        raise FileNotFoundError(f"No json files found in {args.label_dir}")

    counter = Counter()
    samples = defaultdict(list)

    for json_path in json_files:
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            data = json.loads(json_path.read_text(encoding="cp949"))

        annotations = find_annotations(data)

        for obj in annotations:
            label_info = extract_label_candidates(obj)

            if not label_info:
                counter["<NO_LABEL_KEY>"] += 1
                if len(samples["<NO_LABEL_KEY>"]) < args.limit_samples:
                    samples["<NO_LABEL_KEY>"].append((json_path.name, obj))
                continue

            # 우선 가장 흔한 key를 대표 라벨로 사용
            representative = None

            for key in [
                "class",
                "class_id",
                "category",
                "category_id",
                "label",
                "label_id",
                "name",
                "type",
                "object_class",
                "object_name",
            ]:
                if key in label_info:
                    representative = normalize(label_info[key])
                    break

            if representative is None:
                first_key = next(iter(label_info))
                representative = normalize(label_info[first_key])

            counter[representative] += 1

            if len(samples[representative]) < args.limit_samples:
                samples[representative].append((json_path.name, label_info))

    print("\n[AI Hub class distribution]")
    for label, count in counter.most_common():
        print(f"{label}: {count}")

    print("\n[Samples]")
    for label, rows in samples.items():
        print(f"\n## label = {label}")
        for filename, info in rows:
            print(f"- {filename}: {info}")


if __name__ == "__main__":
    main()