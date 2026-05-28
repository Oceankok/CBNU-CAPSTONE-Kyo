from pathlib import Path
import shutil
import random

RANDOM_SEED = 42
TRAIN_RATIO = 0.7
VAL_RATIO = 0.2
TEST_RATIO = 0.1

def main():
    random.seed(RANDOM_SEED)

    src_root = Path("data/merged/ppe_all")
    src_images = src_root / "images"
    src_labels = src_root / "labels"

    dst_root = Path("data/merged/ppe_split")
    for split in ["train", "val", "test"]:
        (dst_root / split / "images").mkdir(parents=True, exist_ok=True)
        (dst_root / split / "labels").mkdir(parents=True, exist_ok=True)

    image_files = [p for p in src_images.iterdir() if p.is_file()]
    random.shuffle(image_files)

    total = len(image_files)
    train_end = int(total * TRAIN_RATIO)
    val_end = train_end + int(total * VAL_RATIO)

    split_map = {
        "train": image_files[:train_end],
        "val": image_files[train_end:val_end],
        "test": image_files[val_end:]
    }

    for split, files in split_map.items():
        for img_path in files:
            label_path = src_labels / f"{img_path.stem}.txt"
            if not label_path.exists():
                continue

            shutil.copy2(img_path, dst_root / split / "images" / img_path.name)
            shutil.copy2(label_path, dst_root / split / "labels" / label_path.name)

    print("Done:", dst_root)

if __name__ == "__main__":
    main()