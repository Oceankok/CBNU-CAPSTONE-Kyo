from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image


# 최종 프로젝트 class map
# 0: helmet
# 1: vest
# 2: person
CLASS_MAP = {
    # helmet
    "helmet": 0,
    "hardhat": 0,
    "hard_hat": 0,
    "safety_helmet": 0,
    "safetyhelmet": 0,
    "hat": 0,
    "안전모": 0,
    "헬멧": 0,

    # vest
    "vest": 1,
    "safety_vest": 1,
    "safety-vest": 1,
    "safetyvest": 1,
    "reflective_vest": 1,
    "안전조끼": 1,
    "조끼": 1,

    # person
    "person": 2,
    "worker": 2,
    "human": 2,
    "people": 2,
    "man": 2,
    "작업자": 2,
    "사람": 2,
    "인부": 2,
}

# no_helmet은 학습 클래스가 아니라 후처리 판단 대상으로 제외
IGNORE_CLASSES = {
    "no_helmet",
    "no-helmet",
    "nohardhat",
    "no_hardhat",
    "without_helmet",
    "helmet_off",
    "head",
    "face",
    "미착용",
    "안전모미착용",
    "안전모_미착용",
    "무안전모",
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def normalize_name(name: Any) -> str:
    return str(name).strip().lower().replace(" ", "_")


def find_images(image_dir: Path) -> dict[str, Path]:
    """
    이미지 파일을 여러 방식으로 찾을 수 있도록 lookup dict 생성.

    지원:
    - 파일명: S2-N1203M01001.jpg
    - stem: S2-N1203M01001
    - 상대경로: S2-N1203M00001/S2-N1203M01001.jpg
    """
    images: dict[str, Path] = {}

    for p in image_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
            continue

        rel = p.relative_to(image_dir).as_posix()

        images[p.name] = p
        images[p.stem] = p
        images[rel] = p
        images[rel.replace("\\", "/")] = p

    return images


def get_image_size(image_path: Path) -> tuple[int, int]:
    with Image.open(image_path) as img:
        return img.size


def find_image_candidates(data: dict[str, Any], json_path: Path) -> list[str]:
    """
    AI Hub JSON에서 이미지 파일명 후보를 추출한다.

    실제 로그 예시:
    "image": {
        "date": "20201009",
        "path": "S2-N1203M00001",
        "filename": "S2-N1203M01001.jpg",
        ...
    }

    이 경우 후보:
    - S2-N1203M01001.jpg
    - S2-N1203M00001/S2-N1203M01001.jpg
    """
    candidates: list[str] = []

    image_info = data.get("image")

    # AI Hub에서 자주 나오는 형태: image가 dict
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

        for key in ["filename", "file_name", "image_name", "img_name", "name"]:
            value = image_info.get(key)
            if isinstance(value, str):
                candidates.append(value)

    # image가 문자열인 경우
    elif isinstance(image_info, str):
        candidates.append(image_info)

    # 최상위 key에 파일명이 있는 경우
    for key in ["filename", "file_name", "image_name", "img_name"]:
        value = data.get(key)
        if isinstance(value, str):
            candidates.append(value)

    # fallback
    candidates.append(json_path.stem)
    candidates.append(f"{json_path.stem}.jpg")
    candidates.append(f"{json_path.stem}.jpeg")
    candidates.append(f"{json_path.stem}.png")

    # 중복 제거
    seen = set()
    unique_candidates: list[str] = []

    for item in candidates:
        normalized = str(item).replace("\\", "/")
        if normalized not in seen:
            seen.add(normalized)
            unique_candidates.append(normalized)

    return unique_candidates


def xyxy_to_yolo(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    img_w: int,
    img_h: int,
) -> str | None:
    x1 = max(0.0, min(float(img_w), float(x1)))
    y1 = max(0.0, min(float(img_h), float(y1)))
    x2 = max(0.0, min(float(img_w), float(x2)))
    y2 = max(0.0, min(float(img_h), float(y2)))

    box_w = x2 - x1
    box_h = y2 - y1

    if box_w <= 0 or box_h <= 0:
        return None

    cx = x1 + box_w / 2
    cy = y1 + box_h / 2

    return f"{cx / img_w:.6f} {cy / img_h:.6f} {box_w / img_w:.6f} {box_h / img_h:.6f}"


def extract_label(obj: dict[str, Any]) -> str | None:
    """
    여러 AI Hub JSON 라벨 키 대응.
    """
    candidate_keys = [
        "label",
        "class",
        "class_name",
        "category",
        "category_name",
        "name",
        "type",
        "object_class",
        "object_name",
    ]

    for key in candidate_keys:
        value = obj.get(key)
        if value is not None:
            return normalize_name(value)

    # 일부 데이터셋은 attribute 내부에 class가 있을 수 있음
    attributes = obj.get("attributes")
    if isinstance(attributes, dict):
        for key in candidate_keys:
            value = attributes.get(key)
            if value is not None:
                return normalize_name(value)

    return None


def extract_bbox(obj: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """
    다양한 bbox 구조 대응.

    지원:
    1. bbox: [x, y, w, h]
    2. bbox: [x1, y1, x2, y2]
    3. bbox: {"x": ..., "y": ..., "w": ..., "h": ...}
    4. bbox: {"xmin": ..., "ymin": ..., "xmax": ..., "ymax": ...}
    5. points: [{"x":..., "y":...}, ...]
    6. polygon: [{"x":..., "y":...}, ...]
    """
    bbox = (
        obj.get("bbox")
        or obj.get("box")
        or obj.get("bounding_box")
        or obj.get("bndbox")
        or obj.get("rect")
    )

    if isinstance(bbox, dict):
        # x, y, width, height
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

        # xmin, ymin, xmax, ymax
        x1 = bbox.get("x1", bbox.get("xmin"))
        y1 = bbox.get("y1", bbox.get("ymin"))
        x2 = bbox.get("x2", bbox.get("xmax"))
        y2 = bbox.get("y2", bbox.get("ymax"))

        if None not in [x1, y1, x2, y2]:
            return float(x1), float(y1), float(x2), float(y2)

    if isinstance(bbox, list) and len(bbox) == 4:
        a, b, c, d = map(float, bbox)

        # 기본은 [x, y, w, h]로 처리
        # 단, AI Hub 샘플에 따라 [x1, y1, x2, y2]이면 아래 옵션이 필요할 수 있음.
        # 현재는 일반적인 AI 라벨 포맷인 xywh 기준.
        return a, b, a + c, b + d

    # points / polygon 기반
    points = obj.get("points") or obj.get("polygon") or obj.get("segmentation")

    if isinstance(points, list) and points:
        xs: list[float] = []
        ys: list[float] = []

        for p in points:
            if isinstance(p, dict):
                if "x" in p and "y" in p:
                    xs.append(float(p["x"]))
                    ys.append(float(p["y"]))

            elif isinstance(p, list) and len(p) >= 2:
                xs.append(float(p[0]))
                ys.append(float(p[1]))

        if xs and ys:
            return min(xs), min(ys), max(xs), max(ys)

    return None


def find_annotations(data: Any) -> list[dict[str, Any]]:
    """
    AI Hub JSON에서 객체 annotation list를 찾는다.
    """
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
                # 예: {"objects": [...]} 형태
                for inner_key in candidate_keys:
                    inner_value = value.get(inner_key)
                    if isinstance(inner_value, list):
                        return [v for v in inner_value if isinstance(v, dict)]

        # 아주 깊게 들어간 구조 일부 대응
        for value in data.values():
            if isinstance(value, dict):
                found = find_annotations(value)
                if found:
                    return found

    if isinstance(data, list):
        return [v for v in data if isinstance(v, dict)]

    return []


def resolve_image_path(
    image_candidates: list[str],
    image_lookup: dict[str, Path],
) -> Path | None:
    for candidate in image_candidates:
        candidate = candidate.replace("\\", "/")

        possible_keys = [
            candidate,
            Path(candidate).name,
            Path(candidate).stem,
        ]

        for key in possible_keys:
            image_path = image_lookup.get(key)
            if image_path is not None:
                return image_path

    return None


def convert_one_json(
    json_path: Path,
    image_lookup: dict[str, Path],
    out_img_dir: Path,
    out_label_dir: Path,
    copy_empty: bool,
) -> tuple[int, int, int]:
    """
    return:
    - image_count
    - object_count
    - ignored_object_count
    """
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        data = json.loads(json_path.read_text(encoding="cp949"))

    if not isinstance(data, dict):
        print(f"[WARN] invalid json structure: {json_path}")
        return 0, 0, 0

    image_candidates = find_image_candidates(data, json_path)
    image_path = resolve_image_path(image_candidates, image_lookup)

    if image_path is None:
        print(f"[WARN] image not found for {json_path.name}: {image_candidates}")
        return 0, 0, 0

    img_w, img_h = get_image_size(image_path)
    annotations = find_annotations(data)

    yolo_lines: list[str] = []
    ignored_object_count = 0

    for obj in annotations:
        label = extract_label(obj)

        if label is None:
            ignored_object_count += 1
            continue

        if label in IGNORE_CLASSES:
            ignored_object_count += 1
            continue

        if label not in CLASS_MAP:
            print(f"[INFO] ignore unknown class '{label}' in {json_path.name}")
            ignored_object_count += 1
            continue

        bbox = extract_bbox(obj)

        if bbox is None:
            print(f"[INFO] bbox not found for class '{label}' in {json_path.name}")
            ignored_object_count += 1
            continue

        yolo_bbox = xyxy_to_yolo(*bbox, img_w=img_w, img_h=img_h)

        if yolo_bbox is None:
            ignored_object_count += 1
            continue

        cls_id = CLASS_MAP[label]
        yolo_lines.append(f"{cls_id} {yolo_bbox}")

    if not yolo_lines and not copy_empty:
        return 0, 0, ignored_object_count

    out_img_dir.mkdir(parents=True, exist_ok=True)
    out_label_dir.mkdir(parents=True, exist_ok=True)

    out_image_path = out_img_dir / image_path.name
    out_label_path = out_label_dir / f"{image_path.stem}.txt"

    shutil.copy2(image_path, out_image_path)
    out_label_path.write_text(
        "\n".join(yolo_lines) + ("\n" if yolo_lines else ""),
        encoding="utf-8",
    )

    return 1, len(yolo_lines), ignored_object_count


def write_dataset_yaml(output_dir: Path) -> None:
    yaml_text = """path: .
train: images
val: images
test: images

names:
  0: helmet
  1: vest
  2: person
"""
    (output_dir / "aihub_yolo.yaml").write_text(yaml_text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert AI Hub JSON annotations to YOLO txt format for PPE detection."
    )
    parser.add_argument(
        "--image-dir",
        type=Path,
        required=True,
        help="AI Hub image root directory.",
    )
    parser.add_argument(
        "--label-dir",
        type=Path,
        required=True,
        help="AI Hub JSON label root directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed/aihub_yolo"),
        help="Output directory.",
    )
    parser.add_argument(
        "--copy-empty",
        action="store_true",
        help="Copy images even when no valid target object remains.",
    )

    args = parser.parse_args()

    if not args.image_dir.exists():
        raise FileNotFoundError(f"Image directory not found: {args.image_dir}")

    if not args.label_dir.exists():
        raise FileNotFoundError(f"Label directory not found: {args.label_dir}")

    image_lookup = find_images(args.image_dir)
    json_files = sorted(args.label_dir.rglob("*.json"))

    if not json_files:
        raise FileNotFoundError(f"No JSON files found in: {args.label_dir}")

    print("[INFO] AI Hub conversion started.")
    print(f"- image_dir: {args.image_dir}")
    print(f"- label_dir: {args.label_dir}")
    print(f"- output_dir: {args.output_dir}")
    print(f"- found images: {len(set(image_lookup.values()))}")
    print(f"- found json files: {len(json_files)}")
    print()

    out_img_dir = args.output_dir / "images"
    out_label_dir = args.output_dir / "labels"

    total_images = 0
    total_objects = 0
    total_ignored = 0
    failed_json = 0

    for idx, json_path in enumerate(json_files, start=1):
        print(f"[{idx}/{len(json_files)}] {json_path.name}")

        image_count, object_count, ignored_count = convert_one_json(
            json_path=json_path,
            image_lookup=image_lookup,
            out_img_dir=out_img_dir,
            out_label_dir=out_label_dir,
            copy_empty=args.copy_empty,
        )

        if image_count == 0:
            failed_json += 1

        total_images += image_count
        total_objects += object_count
        total_ignored += ignored_count

    write_dataset_yaml(args.output_dir)

    print()
    print("[DONE] AI Hub conversion completed.")
    print(f"- converted images: {total_images}")
    print(f"- converted objects: {total_objects}")
    print(f"- ignored objects: {total_ignored}")
    print(f"- failed or skipped json files: {failed_json}")
    print(f"- output images: {out_img_dir}")
    print(f"- output labels: {out_label_dir}")
    print(f"- yaml: {args.output_dir / 'aihub_yolo.yaml'}")


if __name__ == "__main__":
    main()