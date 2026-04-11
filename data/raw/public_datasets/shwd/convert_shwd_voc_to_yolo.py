from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

# 프로젝트 목적상 helmet, head만 사용
CLASS_MAP = {
    "helmet": 0,
    "head": 1,
    "person": 2,
    "hat": 0,
}

def voc_to_yolo_bbox(size, box):
    img_w, img_h = size
    xmin, ymin, xmax, ymax = box

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
        name = obj.find("name").text.strip()

        if name not in CLASS_MAP:
            continue

        class_id = CLASS_MAP[name]
        bndbox = obj.find("bndbox")

        xmin = float(bndbox.find("xmin").text)
        ymin = float(bndbox.find("ymin").text)
        xmax = float(bndbox.find("xmax").text)
        ymax = float(bndbox.find("ymax").text)

        x_center, y_center, width, height = voc_to_yolo_bbox(
            (img_w, img_h), (xmin, ymin, xmax, ymax)
        )
        labels.append((class_id, x_center, y_center, width, height))

    return labels

def convert_dataset(
    images_dir: str,
    annotations_dir: str,
    output_dir: str,
):
    images_dir = Path(images_dir)
    annotations_dir = Path(annotations_dir)
    output_dir = Path(output_dir)

    out_images = output_dir / "images"
    out_labels = output_dir / "labels"
    out_images.mkdir(parents=True, exist_ok=True)
    out_labels.mkdir(parents=True, exist_ok=True)

    image_exts = {".jpg", ".jpeg", ".png", ".bmp"}

    for img_path in images_dir.iterdir():
        if img_path.suffix.lower() not in image_exts:
            continue

        xml_path = annotations_dir / f"{img_path.stem}.xml"
        if not xml_path.exists():
            continue

        labels = parse_xml(xml_path)


        shutil.copy2(img_path, out_images / img_path.name)


        txt_path = out_labels / f"{img_path.stem}.txt"
        with txt_path.open("w", encoding="utf-8") as f:
            for class_id, x, y, w, h in labels:
                f.write(f"{class_id} {x:.6f} {y:.6f} {w:.6f} {h:.6f}\n")

    print(f"Done: {output_dir}")

if __name__ == "__main__":
    convert_dataset(
        images_dir="data/raw/public_datasets/shwd/images",
        annotations_dir="data/raw/public_datasets/shwd/annotations",
        output_dir="data/processed/shwd_yolo"
    )
