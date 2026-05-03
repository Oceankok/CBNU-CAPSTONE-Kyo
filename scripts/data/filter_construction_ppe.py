from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data" / "raw" / "public_datasets" / "construction_ppe"
OUT = ROOT / "data" / "processed" / "construction_ppe_filtered"

KEEP = {
    0: 0,  # helmet
    2: 1,  # vest
    6: 2,  # person
    7: 3,  # no_helmet
}

def find_layout(base: Path) -> str:
    # layout A: train/images, train/labels
    if (base / "train" / "images").exists() and (base / "train" / "labels").exists():
        return "split_first"

    # layout B: images/train, labels/train
    if (base / "images" / "train").exists() and (base / "labels" / "train").exists():
        return "images_first"

    # valid 대신 val만 있는 경우도 같이 허용
    if (base / "images" / "val").exists() and (base / "labels" / "val").exists():
        return "images_first"

    raise FileNotFoundError(
        f"Unknown Construction-PPE structure under: {base}\n"
        f"Expected either:\n"
        f"1) train/images + train/labels\n"
        f"2) images/train + labels/train"
    )

def get_dirs(base: Path, layout: str, split_names: list[str]):
    for split in split_names:
        if layout == "split_first":
            img_dir = base / split / "images"
            lbl_dir = base / split / "labels"
        else:
            img_dir = base / "images" / split
            lbl_dir = base / "labels" / split

        if img_dir.exists() and lbl_dir.exists():
            return img_dir, lbl_dir
    return None, None

def process_split(img_dir: Path, lbl_dir: Path, out_split_dir: Path):
    out_img_dir = out_split_dir / "images"
    out_lbl_dir = out_split_dir / "labels"
    out_img_dir.mkdir(parents=True, exist_ok=True)
    out_lbl_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for img_path in img_dir.iterdir():
        if not img_path.is_file():
            continue

        label_path = lbl_dir / f"{img_path.stem}.txt"
        if not label_path.exists():
            continue

        new_lines = []
        with label_path.open("r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) != 5:
                    continue

                old_class = int(parts[0])
                if old_class not in KEEP:
                    continue

                new_class = KEEP[old_class]
                new_lines.append(" ".join([str(new_class)] + parts[1:]))

        if not new_lines:
            continue

        shutil.copy2(img_path, out_img_dir / img_path.name)
        (out_lbl_dir / f"{img_path.stem}.txt").write_text(
            "\n".join(new_lines) + "\n",
            encoding="utf-8"
        )
        count += 1

    print(f"{out_split_dir.name}: {count} files processed")

def main():
    if not BASE.exists():
        raise FileNotFoundError(f"Construction-PPE base folder not found: {BASE}")

    layout = find_layout(BASE)
    print(f"Found Construction-PPE root: {BASE}")
    print(f"Detected layout: {layout}")

    split_aliases = {
        "train": ["train"],
        "val": ["val", "valid"],
        "test": ["test"],
    }

    for out_split, aliases in split_aliases.items():
        img_dir, lbl_dir = get_dirs(BASE, layout, aliases)
        if img_dir is None or lbl_dir is None:
            print(f"[SKIP] split not found: {aliases}")
            continue

        process_split(img_dir, lbl_dir, OUT / out_split)

    print("Done:", OUT)

if __name__ == "__main__":
    main()