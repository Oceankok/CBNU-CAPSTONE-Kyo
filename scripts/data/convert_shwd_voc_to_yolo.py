from __future__ import annotations

import argparse
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image


CLASS_MAP = {
    "helmet": 0,
    "hat": 0,
    "hardhat": 0,
    "hard_hat": 0,
    "person": 2,
    "worker": 2,
}

IGNORE_CLASSES = {
    "head",
    "no_helmet",
    "no-hardhat",
    "no_hat",
    "without_helmet",
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def normalize_name(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def find_images(image_dir: Path) -> dict[str, Path]:
    images = {}
    for p in image_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            images[p.name] = p
            images[p.stem] = p
    return images


def get_image_size_from_xml(root: ET.Element, image_path: Path | None) -> tuple[int, int]:
    size = root.find("size")
    if size is not None:
        w = size.findtext("width")
        h = size.findtext("height")
        if w and h:
            return int(float(w)), int(float(h))

    if image_path is None:
        raise ValueError("Image path is required when XML size is missing.")

    with Image.open(image_path) as img:
        return img.size


def xyxy_to_yolo(x1: float, y1: float, x2: float, y2: float, w: int, h: int) -> str | None:
    x1 = max(0.0, min(float(w), x1))
    y1 = max(0.0, min(float(h), y1))
    x2 = max(0.0, min(float(w), x2))
    y2 = max(0.0, min(float(h), y2))

    bw = x2 - x1
    bh = y2 - y1

    if bw <= 0 or bh <= 0:
        return None

    cx = x1 + bw / 2
    cy = y1 + bh / 2

    return f"{cx / w:.6f} {cy / h:.6f} {bw / w:.6f} {bh / h:.6f}"


def convert_xml(xml_path: Path, image_lookup: dict[str, Path], out_img_dir: Path, out_label_dir: Path) -> tuple[int, int]:
    root = ET.parse(xml_path).getroot()

    filename = root.findtext("filename") or xml_path.stem
    image_path = image_lookup.get(filename) or image_lookup.get(Path(filename).stem)

    if image_path is None:
        print(f"[WARN] image not found for {xml_path.name}: {filename}")
        return 0, 0

    img_w, img_h = get_image_size_from_xml(root, image_path)

    yolo_lines = []

    for obj in root.findall("object"):
        name = obj.findtext("name")
        if not name:
            continue

        label = normalize_name(name)

        if label in IGNORE_CLASSES:
            continue

        if label not in CLASS_MAP:
            print(f"[INFO] ignore unknown class '{label}' in {xml_path.name}")
            continue

        bndbox = obj.find("bndbox")
        if bndbox is None:
            continue

        xmin = bndbox.findtext("xmin")
        ymin = bndbox.findtext("ymin")
        xmax = bndbox.findtext("xmax")
        ymax = bndbox.findtext("ymax")

        if None in [xmin, ymin, xmax, ymax]:
            continue

        yolo_bbox = xyxy_to_yolo(
            float(xmin),
            float(ymin),
            float(xmax),
            float(ymax),
            img_w,
            img_h,
        )

        if yolo_bbox is None:
            continue

        cls_id = CLASS_MAP[label]
        yolo_lines.append(f"{cls_id} {yolo_bbox}")

    out_img_dir.mkdir(parents=True, exist_ok=True)
    out_label_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(image_path, out_img_dir / image_path.name)
    (out_label_dir / f"{image_path.stem}.txt").write_text(
        "\n".join(yolo_lines) + ("\n" if yolo_lines else ""),
        encoding="utf-8",
    )

    return 1, len(yolo_lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-dir", type=Path, required=True)
    parser.add_argument("--xml-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/shwd_yolo"))
    args = parser.parse_args()

    image_lookup = find_images(args.image_dir)

    out_img_dir = args.output_dir / "images"
    out_label_dir = args.output_dir / "labels"

    xml_files = sorted(args.xml_dir.rglob("*.xml"))

    total_images = 0
    total_objects = 0

    for idx, xml_path in enumerate(xml_files, start=1):
        print(f"[{idx}/{len(xml_files)}] {xml_path.name}")
        image_count, object_count = convert_xml(
            xml_path=xml_path,
            image_lookup=image_lookup,
            out_img_dir=out_img_dir,
            out_label_dir=out_label_dir,
        )
        total_images += image_count
        total_objects += object_count

    print("[DONE] SHWD conversion completed.")
    print(f"- images: {total_images}")
    print(f"- objects: {total_objects}")
    print(f"- output: {args.output_dir}")


if __name__ == "__main__":
    main()