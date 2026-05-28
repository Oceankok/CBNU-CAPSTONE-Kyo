from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data" / "raw" / "public_datasets" / "shwd"
OUT = ROOT / "data" / "processed" / "shwd_yolo"

CLASS_MAP = {
    "helmet": 0,
    "hat": 0,
    "head": 2,
    "person": 2,
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}

def find_dir(base: Path, preferred_names: list[str], required_ext: str | None = None) -> Path:
    for name in preferred_names:
        p = base / name
        if p.exists() and p.is_dir():
            return p

    for p in base.rglob("*"):
        if not p.is_dir():
            continue
        files = [x for x in p.iterdir() if x.is_file()]
        if not files:
            continue

        if required_ext is None:
            return p

        if any(x.suffix.lower() == required_ext.lower() for x in files):
            return p

    raise FileNotFoundError(f"Could not find target folder under {base}")

def voc_to_yolo_bbox(img_w, img_h, xmin, ymin, xmax, ymax):
    x_center = ((xmin + xmax) / 2.0) / img_w
    y_center = ((ymin + ymax) / 2.0) / img_h
    width = (xmax - xmin) / img_w
    height = (ymax - ymin) / img_h
    return x_center, y_center, width, height

def parse_xml(xml_path: Path):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    size_node = root.find("size")
    img_w = int(size_node.find("width").text)
    img_h = int(size_node.find("height").text)

    labels = []

    for obj in root.findall("object"):
        name = obj.find("name").text.strip().lower()
        if name not in CLASS_MAP:
            continue

        class_id = CLASS_MAP[name]
        bndbox = obj.find("bndbox")

        xmin = float(bndbox.find("xmin").text)
        ymin = float(bndbox.find("ymin").text)
        xmax = float(bndbox.find("xmax").text)
        ymax = float(bndbox.find("ymax").text)

        x, y, w, h = voc_to_yolo_bbox(img_w, img_h, xmin, ymin, xmax, ymax)
        labels.append((class_id, x, y, w, h))

    return labels

def main():
    if not BASE.exists():
        raise FileNotFoundError(f"SHWD base folder not found: {BASE}")

    images_dir = find_dir(BASE, ["images", "JPEGImages", "Images"])
    annotations_dir = find_dir(BASE, ["annotations", "Annotations"], required_ext=".xml")

    print(f"Found SHWD images: {images_dir}")
    print(f"Found SHWD annotations: {annotations_dir}")

    out_images = OUT / "images"
    out_labels = OUT / "labels"
    out_images.mkdir(parents=True, exist_ok=True)
    out_labels.mkdir(parents=True, exist_ok=True)

    count = 0
    for img_path in images_dir.iterdir():
        if img_path.suffix.lower() not in IMAGE_EXTS:
            continue

        xml_path = annotations_dir / f"{img_path.stem}.xml"
        if not xml_path.exists():
            continue

        labels = parse_xml(xml_path)
        if not labels:
            continue

        shutil.copy2(img_path, out_images / img_path.name)

        txt_path = out_labels / f"{img_path.stem}.txt"
        with txt_path.open("w", encoding="utf-8") as f:
            for class_id, x, y, w, h in labels:
                f.write(f"{class_id} {x:.6f} {y:.6f} {w:.6f} {h:.6f}\n")

        count += 1

    print(f"Done: {OUT} ({count} files)")

if __name__ == "__main__":
    main()