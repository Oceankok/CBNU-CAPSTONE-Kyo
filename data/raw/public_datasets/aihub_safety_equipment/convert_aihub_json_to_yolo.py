from pathlib import Path
import json
import shutil
from PIL import Image

# 프로젝트 핵심만 우선 사용
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

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}

def normalize_bbox_xyxy(bbox):
    """
    AI Hub 예시:
    bbox: [x, y, x, y]
    또는 [[x1, y1], [x2, y2]]
    """
    if isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
        x1, y1, x2, y2 = bbox
        return float(x1), float(y1), float(x2), float(y2)

    if (
        isinstance(bbox, list)
        and len(bbox) == 2
        and all(isinstance(pt, list) and len(pt) == 2 for pt in bbox)
    ):
        (x1, y1), (x2, y2) = bbox
        return float(x1), float(y1), float(x2), float(y2)

    return None

def xyxy_to_yolo(img_w, img_h, x1, y1, x2, y2):
    x_center = ((x1 + x2) / 2.0) / img_w
    y_center = ((y1 + y2) / 2.0) / img_h
    width = (x2 - x1) / img_w
    height = (y2 - y1) / img_h
    return x_center, y_center, width, height

def extract_class_name(ann):
    candidate_keys = ["class", "class_name", "label", "name", "category", "tagname"]
    for key in candidate_keys:
        if key in ann and isinstance(ann[key], str):
            return ann[key].strip()
    return None

def extract_annotations(obj):
    # 실제 데이터 구조 차이를 흡수
    if isinstance(obj, dict):
        if "annotations" in obj and isinstance(obj["annotations"], list):
            return obj["annotations"]
        if "annotations" in obj and isinstance(obj["annotations"], dict):
            for v in obj["annotations"].values():
                if isinstance(v, list):
                    return v
        if "label" in obj and isinstance(obj["label"], list):
            return obj["label"]
    return []

def convert_one(json_path: Path, image_root: Path, out_images: Path, out_labels: Path):
    data = json.loads(json_path.read_text(encoding="utf-8"))

    image_name = None
    if "image" in data and isinstance(data["image"], dict):
        image_name = data["image"].get("filename")

    if image_name is None:
        image_name = json_path.stem + ".jpg"

    image_path = None
    for ext in IMAGE_EXTS:
        candidate = image_root / (Path(image_name).stem + ext)
        if candidate.exists():
            image_path = candidate
            break

    if image_path is None:
        # filename 그대로 찾기
        candidate = image_root / image_name
        if candidate.exists():
            image_path = candidate

    if image_path is None:
        print(f"[WARN] image not found for {json_path.name}")
        return

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
        return

    shutil.copy2(image_path, out_images / image_path.name)
    txt_path = out_labels / f"{image_path.stem}.txt"
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def convert_dataset(json_dir: str, image_dir: str, output_dir: str):
    json_dir = Path(json_dir)
    image_dir = Path(image_dir)
    output_dir = Path(output_dir)

    out_images = output_dir / "images"
    out_labels = output_dir / "labels"
    out_images.mkdir(parents=True, exist_ok=True)
    out_labels.mkdir(parents=True, exist_ok=True)

    for json_path in json_dir.rglob("*.json"):
        convert_one(json_path, image_dir, out_images, out_labels)

    print(f"Done: {output_dir}")

if __name__ == "__main__":
    convert_dataset(
        json_dir="data/raw/public_datasets/aihub_safety_equipment/labels",
        image_dir="data/raw/public_datasets/aihub_safety_equipment/images",
        output_dir="data/processed/aihub_yolo"
    )
