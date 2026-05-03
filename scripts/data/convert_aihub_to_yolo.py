from pathlib import Path
import json
import shutil
from PIL import Image


TARGET_CLASSES = {
    "안전모": 0,
    "helmet": 0,
    "hardhat": 0,
    "안전조끼": 1,
    "조끼": 1,
    "vest": 1,
    "안전복": 1,
    "사람": 2,
    "person": 2,
}

IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".bmp"]

def normalize_bbox_xyxy(bbox):
    if isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
        return float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])

    if isinstance(bbox, list) and len(bbox) == 2:
        if all(isinstance(pt, list) and len(pt) == 2 for pt in bbox):
            return float(bbox[0][0]), float(bbox[0][1]), float(bbox[1][0]), float(bbox[1][1])

    return None

def xyxy_to_yolo(img_w, img_h, x1, y1, x2, y2):
    x_center = ((x1 + x2) / 2.0) / img_w
    y_center = ((y1 + y2) / 2.0) / img_h
    width = (x2 - x1) / img_w
    height = (y2 - y1) / img_h
    return x_center, y_center, width, height

def extract_annotations(data):
    if "annotations" in data and isinstance(data["annotations"], list):
        return data["annotations"]
    if "label" in data and isinstance(data["label"], list):
        return data["label"]
    return []

def extract_class_name(ann):
    for key in ["class", "class_name", "label", "name", "category", "tagname"]:
        if key in ann and isinstance(ann[key], str):
            return ann[key].strip()
    return None

def find_image(image_dir: Path, stem: str):
    for ext in IMAGE_EXTS:
        candidate = image_dir / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None

def main():
    json_dir = Path("data/raw/public_datasets/aihub_safety_equipment/labels")
    image_dir = Path("data/raw/public_datasets/aihub_safety_equipment/images")
    output_dir = Path("data/processed/aihub_yolo")

    out_images = output_dir / "images"
    out_labels = output_dir / "labels"
    out_images.mkdir(parents=True, exist_ok=True)
    out_labels.mkdir(parents=True, exist_ok=True)

    for json_path in json_dir.rglob("*.json"):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        image_path = find_image(image_dir, json_path.stem)

        if image_path is None:
            continue

        with Image.open(image_path) as img:
            img_w, img_h = img.size

        anns = extract_annotations(data)
        lines = []

        for ann in anns:
            if not isinstance(ann, dict):
                continue

            class_name = extract_class_name(ann)
            if class_name not in TARGET_CLASSES:
                continue

            bbox = ann.get("bbox")
            if bbox is None:
                continue

            xyxy = normalize_bbox_xyxy(bbox)
            if xyxy is None:
                continue

            x1, y1, x2, y2 = xyxy
            if x2 <= x1 or y2 <= y1:
                continue

            class_id = TARGET_CLASSES[class_name]
            x, y, w, h = xyxy_to_yolo(img_w, img_h, x1, y1, x2, y2)
            lines.append(f"{class_id} {x:.6f} {y:.6f} {w:.6f} {h:.6f}")

        if not lines:
            continue

        shutil.copy2(image_path, out_images / image_path.name)
        (out_labels / f"{image_path.stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("Done:", output_dir)

if __name__ == "__main__":
    main()