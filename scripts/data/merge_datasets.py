from pathlib import Path
import shutil

def copy_dataset(src_images: Path, src_labels: Path, dst_images: Path, dst_labels: Path, prefix: str):
    dst_images.mkdir(parents=True, exist_ok=True)
    dst_labels.mkdir(parents=True, exist_ok=True)

    for img_path in src_images.iterdir():
        if not img_path.is_file():
            continue

        label_path = src_labels / f"{img_path.stem}.txt"
        if not label_path.exists():
            continue

        new_img_name = f"{prefix}_{img_path.name}"
        new_label_name = f"{prefix}_{img_path.stem}.txt"

        shutil.copy2(img_path, dst_images / new_img_name)
        shutil.copy2(label_path, dst_labels / new_label_name)

def main():
    merged_root = Path("data/merged/ppe_all")
    merged_images = merged_root / "images"
    merged_labels = merged_root / "labels"

    datasets = [
        ("construction", Path("data/processed/construction_ppe_filtered/train/images"), Path("data/processed/construction_ppe_filtered/train/labels")),
        ("construction_val", Path("data/processed/construction_ppe_filtered/valid/images"), Path("data/processed/construction_ppe_filtered/valid/labels")),
        ("construction_test", Path("data/processed/construction_ppe_filtered/test/images"), Path("data/processed/construction_ppe_filtered/test/labels")),
        ("shwd", Path("data/processed/shwd_yolo/images"), Path("data/processed/shwd_yolo/labels")),
        # ("aihub", Path("data/processed/aihub_yolo/images"), Path("data/processed/aihub_yolo/labels")),
    ]

    for prefix, img_dir, lbl_dir in datasets:
        if img_dir.exists() and lbl_dir.exists():
            copy_dataset(img_dir, lbl_dir, merged_images, merged_labels, prefix)

    print("Done:", merged_root)

if __name__ == "__main__":
    main()