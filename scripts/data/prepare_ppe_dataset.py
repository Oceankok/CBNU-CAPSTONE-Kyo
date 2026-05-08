from pathlib import Path
import random
import shutil

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VALID_CLASS_IDS = {0, 1, 2}

SRC_ROOT = Path("data/merged/ppe_all")
OUT_ROOT = Path("data/merged/ppe_split")

SRC_IMAGES = SRC_ROOT / "images"
SRC_LABELS = SRC_ROOT / "labels"


def clean_label(label_path: Path) -> list[str]:
    if not label_path.exists():
        return []

    cleaned = []

    for line in label_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue

        parts = line.split()
        if len(parts) != 5:
            continue

        try:
            cls = int(parts[0])
            x, y, w, h = map(float, parts[1:])
        except ValueError:
            continue

        if cls not in VALID_CLASS_IDS:
            continue

        if not all(0.0 <= v <= 1.0 for v in [x, y, w, h]):
            continue

        if w <= 0 or h <= 0:
            continue

        cleaned.append(f"{cls} {x:.6f} {y:.6f} {w:.6f} {h:.6f}")

    return cleaned


def main():
    images = sorted(
        p for p in SRC_IMAGES.iterdir()
        if p.suffix.lower() in IMAGE_EXTS
    )

    random.seed(42)
    random.shuffle(images)

    n = len(images)
    train_end = int(n * 0.7)
    val_end = train_end + int(n * 0.2)

    splits = {
        "train": images[:train_end],
        "val": images[train_end:val_end],
        "test": images[val_end:],
    }

    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)

    for split in splits:
        (OUT_ROOT / split / "images").mkdir(parents=True, exist_ok=True)
        (OUT_ROOT / split / "labels").mkdir(parents=True, exist_ok=True)

    for split, split_images in splits.items():
        for img_path in split_images:
            label_path = SRC_LABELS / f"{img_path.stem}.txt"

            shutil.copy2(img_path, OUT_ROOT / split / "images" / img_path.name)

            cleaned = clean_label(label_path)
            out_label = OUT_ROOT / split / "labels" / f"{img_path.stem}.txt"
            out_label.write_text(
                "\n".join(cleaned) + ("\n" if cleaned else ""),
                encoding="utf-8"
            )

        print(f"{split}: {len(split_images)} images")

    print("Done. Cleaned labels only contain class ids 0, 1, 2.")


if __name__ == "__main__":
    main()