from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def find_images(image_dir: Path) -> dict[str, Path]:
    images: dict[str, Path] = {}

    for p in image_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
            continue

        rel = p.relative_to(image_dir).as_posix()

        images[p.name] = p
        images[p.stem] = p
        images[rel] = p

    return images


def find_image_candidates(data: dict[str, Any], json_path: Path) -> list[str]:
    candidates: list[str] = []

    image_info = data.get("image")

    if isinstance(image_info, dict):
        filename = (
            image_info.get("filename")
            or image_info.get("file_name")
            or image_info.get("image_name")
            or image_info.get("name")
        )
        folder = image_info.get("path")

        if filename:
            filename = str(filename)
            candidates.append(filename)

            if folder:
                candidates.append(str(Path(str(folder)) / filename))

    elif isinstance(image_info, str):
        candidates.append(image_info)

    for key in ["filename", "file_name", "image_name", "img_name"]:
        value = data.get(key)
        if isinstance(value, str):
            candidates.append(value)

    candidates.append(json_path.stem)
    candidates.append(f"{json_path.stem}.jpg")
    candidates.append(f"{json_path.stem}.png")

    seen = set()
    result = []

    for c in candidates:
        c = str(c).replace("\\", "/")
        if c not in seen:
            seen.add(c)
            result.append(c)

    return result


def resolve_image_path(candidates: list[str], image_lookup: dict[str, Path]) -> Path | None:
    for candidate in candidates:
        candidate = candidate.replace("\\", "/")
        keys = [
            candidate,
            Path(candidate).name,
            Path(candidate).stem,
        ]

        for key in keys:
            if key in image_lookup:
                return image_lookup[key]

    return None


def find_annotations(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        keys = [
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

        for key in keys:
            value = data.get(key)

            if isinstance(value, list):
                return [v for v in value if isinstance(v, dict)]

            if isinstance(value, dict):
                for inner_key in keys:
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


def extract_class_code(obj: dict[str, Any]) -> str | None:
    keys = [
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
        "code",
    ]

    for key in keys:
        value = obj.get(key)
        if value is not None:
            return str(value).strip()

    attrs = obj.get("attributes")
    if isinstance(attrs, dict):
        for key in keys:
            value = attrs.get(key)
            if value is not None:
                return str(value).strip()

    return None


def extract_bbox(obj: dict[str, Any]) -> tuple[float, float, float, float] | None:
    bbox = (
        obj.get("bbox")
        or obj.get("box")
        or obj.get("bounding_box")
        or obj.get("bndbox")
        or obj.get("rect")
    )

    if isinstance(bbox, dict):
        x = bbox.get("x", bbox.get("left"))
        y = bbox.get("y", bbox.get("top"))
        bw = bbox.get("w", bbox.get("width"))
        bh = bbox.get("h", bbox.get("height"))

        if None not in [x, y, bw, bh]:
            x = float(x)
            y = float(y)
            bw = float(bw)
            bh = float(bh)
            return x, y, x + bw, y + bh

        x1 = bbox.get("x1", bbox.get("xmin"))
        y1 = bbox.get("y1", bbox.get("ymin"))
        x2 = bbox.get("x2", bbox.get("xmax"))
        y2 = bbox.get("y2", bbox.get("ymax"))

        if None not in [x1, y1, x2, y2]:
            return float(x1), float(y1), float(x2), float(y2)

    if isinstance(bbox, list) and len(bbox) == 4:
        a, b, c, d = map(float, bbox)

        # AI Hub 일반 bbox는 [x, y, w, h] 형태가 많아서 xywh로 처리
        return a, b, a + c, b + d

    points = obj.get("points") or obj.get("polygon") or obj.get("segmentation")

    if isinstance(points, list) and points:
        xs = []
        ys = []

        for p in points:
            if isinstance(p, dict) and "x" in p and "y" in p:
                xs.append(float(p["x"]))
                ys.append(float(p["y"]))
            elif isinstance(p, list) and len(p) >= 2:
                xs.append(float(p[0]))
                ys.append(float(p[1]))

        if xs and ys:
            return min(xs), min(ys), max(xs), max(ys)

    return None


def clamp_box(
    box: tuple[float, float, float, float],
    img_w: int,
    img_h: int,
    pad_ratio: float = 0.1,
) -> tuple[int, int, int, int] | None:
    x1, y1, x2, y2 = box

    bw = x2 - x1
    bh = y2 - y1

    if bw <= 0 or bh <= 0:
        return None

    pad_x = bw * pad_ratio
    pad_y = bh * pad_ratio

    x1 = max(0, int(x1 - pad_x))
    y1 = max(0, int(y1 - pad_y))
    x2 = min(img_w, int(x2 + pad_x))
    y2 = min(img_h, int(y2 + pad_y))

    if x2 <= x1 or y2 <= y1:
        return None

    return x1, y1, x2, y2


def save_crop_and_marked_image(
    image_path: Path,
    bbox: tuple[float, float, float, float],
    class_code: str,
    json_name: str,
    index: int,
    output_dir: Path,
) -> tuple[str, str] | None:
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        img_w, img_h = img.size

        clamped = clamp_box(bbox, img_w, img_h)
        if clamped is None:
            return None

        x1, y1, x2, y2 = clamped

        crop = img.crop((x1, y1, x2, y2))

        class_dir = output_dir / f"class_{class_code}"
        crop_dir = class_dir / "crops"
        marked_dir = class_dir / "marked"

        crop_dir.mkdir(parents=True, exist_ok=True)
        marked_dir.mkdir(parents=True, exist_ok=True)

        safe_stem = Path(json_name).stem
        crop_name = f"{safe_stem}_{index:03d}_{image_path.stem}.jpg"
        marked_name = f"{safe_stem}_{index:03d}_{image_path.stem}_marked.jpg"

        crop_path = crop_dir / crop_name
        marked_path = marked_dir / marked_name

        crop.save(crop_path, quality=90)

        marked = img.copy()
        draw = ImageDraw.Draw(marked)
        draw.rectangle((x1, y1, x2, y2), outline="red", width=4)
        draw.text((x1, max(0, y1 - 18)), f"class {class_code}", fill="red")
        marked.save(marked_path, quality=90)

        return str(crop_path), str(marked_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create crop samples for each AI Hub class code."
    )
    parser.add_argument("--image-dir", type=Path, required=True)
    parser.add_argument("--label-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/aihub_class_crops"))
    parser.add_argument("--max-per-class", type=int, default=30)
    args = parser.parse_args()

    if args.output_dir.exists():
        shutil.rmtree(args.output_dir)

    image_lookup = find_images(args.image_dir)
    json_files = sorted(args.label_dir.rglob("*.json"))

    saved_count_by_class: dict[str, int] = defaultdict(int)
    index_rows = []

    for json_idx, json_path in enumerate(json_files, start=1):
        print(f"[{json_idx}/{len(json_files)}] {json_path.name}")

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            data = json.loads(json_path.read_text(encoding="cp949"))

        if not isinstance(data, dict):
            continue

        image_candidates = find_image_candidates(data, json_path)
        image_path = resolve_image_path(image_candidates, image_lookup)

        if image_path is None:
            continue

        annotations = find_annotations(data)

        for obj_idx, obj in enumerate(annotations, start=1):
            class_code = extract_class_code(obj)
            if class_code is None:
                continue

            if saved_count_by_class[class_code] >= args.max_per_class:
                continue

            bbox = extract_bbox(obj)
            if bbox is None:
                continue

            result = save_crop_and_marked_image(
                image_path=image_path,
                bbox=bbox,
                class_code=class_code,
                json_name=json_path.name,
                index=obj_idx,
                output_dir=args.output_dir,
            )

            if result is None:
                continue

            crop_path, marked_path = result
            saved_count_by_class[class_code] += 1

            index_rows.append(
                {
                    "class_code": class_code,
                    "json_file": json_path.name,
                    "image_file": image_path.name,
                    "crop_path": crop_path,
                    "marked_path": marked_path,
                }
            )

    index_csv = args.output_dir / "class_crop_index.csv"
    index_csv.parent.mkdir(parents=True, exist_ok=True)

    with index_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "class_code",
                "json_file",
                "image_file",
                "crop_path",
                "marked_path",
            ],
        )
        writer.writeheader()
        writer.writerows(index_rows)

    print()
    print("[DONE] AI Hub class crop samples created.")
    print(f"- output: {args.output_dir}")
    print(f"- index: {index_csv}")

    print()
    print("[Class sample counts]")
    for class_code, count in sorted(saved_count_by_class.items(), key=lambda x: x[0]):
        print(f"- {class_code}: {count}")


if __name__ == "__main__":
    main()