import argparse
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from tqdm import tqdm


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def load_yaml(data_yaml_path: Path):
    with open(data_yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    names = data.get("names", [])
    if isinstance(names, dict):
        names = [names[k] for k in sorted(names.keys(), key=lambda x: int(x))]

    root = data.get("path", None)
    if root is None:
        root = data_yaml_path.parent
    else:
        root = Path(root)
        if not root.is_absolute():
            root = (data_yaml_path.parent / root).resolve()

    return data, root, names


def resolve_split_paths(split_value, root: Path):
    if split_value is None:
        return []

    if isinstance(split_value, list):
        values = split_value
    else:
        values = [split_value]

    resolved = []

    for value in values:
        p = Path(value)
        if not p.is_absolute():
            p = (root / p).resolve()

        if p.is_file() and p.suffix.lower() == ".txt":
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    img_path = Path(line)
                    if not img_path.is_absolute():
                        candidate1 = (root / img_path).resolve()
                        candidate2 = (p.parent / img_path).resolve()

                        if candidate1.exists():
                            img_path = candidate1
                        else:
                            img_path = candidate2

                    resolved.append(img_path)

        elif p.is_dir():
            for ext in IMAGE_EXTS:
                resolved.extend(p.rglob(f"*{ext}"))
                resolved.extend(p.rglob(f"*{ext.upper()}"))

    return sorted(set(resolved))


def label_path_for_image(image_path: Path):
    parts = list(image_path.parts)

    if "images" in parts:
        idx = len(parts) - 1 - parts[::-1].index("images")
        parts[idx] = "labels"
        return Path(*parts).with_suffix(".txt")

    # fallback
    candidate = image_path.parent.parent / "labels" / f"{image_path.stem}.txt"
    if candidate.exists():
        return candidate

    candidate = image_path.parent / "labels" / f"{image_path.stem}.txt"
    return candidate


def read_yolo_label(label_path: Path):
    if not label_path.exists():
        return None

    rows = []
    with open(label_path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue

            parts = raw.split()
            rows.append((line_no, parts, raw))

    return rows


def image_quality_stats(image_path: Path):
    img = cv2.imread(str(image_path))

    if img is None:
        return None

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    brightness = float(hsv[:, :, 2].mean())
    contrast = float(gray.std())
    blur_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    return {
        "width": w,
        "height": h,
        "brightness": brightness,
        "contrast": contrast,
        "blur_var": blur_var,
        "file_size_kb": image_path.stat().st_size / 1024,
    }


def is_valid_bbox(x, y, bw, bh, eps=1e-6):
    if bw <= 0 or bh <= 0:
        return False

    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < bw <= 1 and 0 < bh <= 1):
        return False

    x1 = x - bw / 2
    y1 = y - bh / 2
    x2 = x + bw / 2
    y2 = y + bh / 2

    if x1 < -eps or y1 < -eps or x2 > 1 + eps or y2 > 1 + eps:
        return False

    return True


def copy_problem_sample(image_path: Path, issue_name: str, out_dir: Path, copied_counter: Counter, max_samples: int):
    if copied_counter[issue_name] >= max_samples:
        return

    dst_dir = out_dir / "samples" / issue_name
    dst_dir.mkdir(parents=True, exist_ok=True)

    dst_path = dst_dir / image_path.name

    try:
        shutil.copy2(image_path, dst_path)
        copied_counter[issue_name] += 1
    except Exception:
        pass


def save_hist(df, column, out_path, title, xlabel, bins=50):
    if df.empty or column not in df.columns:
        return

    values = df[column].dropna()
    if values.empty:
        return

    plt.figure(figsize=(8, 5))
    plt.hist(values, bins=bins)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def save_bar(series, out_path, title, xlabel, ylabel):
    if series.empty:
        return

    plt.figure(figsize=(10, 5))
    series.plot(kind="bar")
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="YOLO data yaml path, e.g. configs/merged_ppe.yaml")
    parser.add_argument("--out", default="reports/data_quality", help="Output report directory")

    parser.add_argument("--dark-thr", type=float, default=45.0, help="Brightness threshold for dark images")
    parser.add_argument("--bright-thr", type=float, default=235.0, help="Brightness threshold for too bright images")
    parser.add_argument("--low-contrast-thr", type=float, default=25.0, help="Contrast threshold")
    parser.add_argument("--blur-thr", type=float, default=80.0, help="Laplacian variance threshold for blurry images")

    parser.add_argument("--small-bbox-area-ratio", type=float, default=0.0025, help="Small bbox ratio threshold")
    parser.add_argument("--tiny-bbox-px", type=float, default=400.0, help="Tiny bbox pixel area threshold")

    parser.add_argument("--max-samples", type=int, default=30, help="Max copied samples per issue type")
    args = parser.parse_args()

    data_yaml_path = Path(args.data).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    data, root, names = load_yaml(data_yaml_path)
    num_classes = len(names)

    print(f"[INFO] data yaml: {data_yaml_path}")
    print(f"[INFO] dataset root: {root}")
    print(f"[INFO] classes: {names}")

    image_rows = []
    bbox_rows = []
    issue_rows = []

    issue_counts = Counter()
    class_counts = Counter()
    copied_counter = Counter()

    split_images = {}

    for split in ["train", "val", "test"]:
        image_paths = resolve_split_paths(data.get(split), root)
        split_images[split] = image_paths
        print(f"[INFO] {split}: {len(image_paths)} images")

    for split, image_paths in split_images.items():
        for image_path in tqdm(image_paths, desc=f"Inspect {split}"):
            image_path = Path(image_path)
            label_path = label_path_for_image(image_path)

            img_stats = image_quality_stats(image_path)

            if img_stats is None:
                issue_name = "unreadable_image"
                issue_counts[issue_name] += 1
                issue_rows.append({
                    "split": split,
                    "image_path": str(image_path),
                    "label_path": str(label_path),
                    "issue": issue_name,
                    "detail": "cv2.imread failed",
                })
                continue

            width = img_stats["width"]
            height = img_stats["height"]

            image_issue_list = []

            if img_stats["brightness"] < args.dark_thr:
                image_issue_list.append("dark_image")
            if img_stats["brightness"] > args.bright_thr:
                image_issue_list.append("too_bright_image")
            if img_stats["contrast"] < args.low_contrast_thr:
                image_issue_list.append("low_contrast_image")
            if img_stats["blur_var"] < args.blur_thr:
                image_issue_list.append("blurry_image")

            label_rows = read_yolo_label(label_path)

            if label_rows is None:
                image_issue_list.append("missing_label_file")
                label_obj_count = 0
            elif len(label_rows) == 0:
                image_issue_list.append("empty_label_file")
                label_obj_count = 0
            else:
                label_obj_count = len(label_rows)

            image_rows.append({
                "split": split,
                "image_path": str(image_path),
                "label_path": str(label_path),
                "width": width,
                "height": height,
                "brightness": img_stats["brightness"],
                "contrast": img_stats["contrast"],
                "blur_var": img_stats["blur_var"],
                "file_size_kb": img_stats["file_size_kb"],
                "label_obj_count": label_obj_count,
                "issues": ",".join(image_issue_list),
            })

            for issue_name in image_issue_list:
                issue_counts[issue_name] += 1
                issue_rows.append({
                    "split": split,
                    "image_path": str(image_path),
                    "label_path": str(label_path),
                    "issue": issue_name,
                    "detail": "",
                })
                copy_problem_sample(image_path, issue_name, out_dir, copied_counter, args.max_samples)

            if label_rows is None:
                continue

            for line_no, parts, raw in label_rows:
                bbox_issue_list = []

                if len(parts) < 5:
                    issue_name = "invalid_label_format"
                    issue_counts[issue_name] += 1
                    issue_rows.append({
                        "split": split,
                        "image_path": str(image_path),
                        "label_path": str(label_path),
                        "issue": issue_name,
                        "detail": f"line {line_no}: {raw}",
                    })
                    continue

                try:
                    cls_id = int(float(parts[0]))
                    x, y, bw, bh = map(float, parts[1:5])
                except ValueError:
                    issue_name = "invalid_label_value"
                    issue_counts[issue_name] += 1
                    issue_rows.append({
                        "split": split,
                        "image_path": str(image_path),
                        "label_path": str(label_path),
                        "issue": issue_name,
                        "detail": f"line {line_no}: {raw}",
                    })
                    continue

                if cls_id < 0 or cls_id >= num_classes:
                    bbox_issue_list.append("class_id_out_of_range")

                valid_bbox = is_valid_bbox(x, y, bw, bh)
                if not valid_bbox:
                    bbox_issue_list.append("invalid_bbox")

                bbox_area_ratio = bw * bh
                bbox_area_px = bbox_area_ratio * width * height
                bbox_w_px = bw * width
                bbox_h_px = bh * height
                aspect_ratio = bw / bh if bh > 0 else np.nan

                if valid_bbox:
                    if bbox_area_ratio < args.small_bbox_area_ratio:
                        bbox_issue_list.append("small_bbox_ratio")
                    if bbox_area_px < args.tiny_bbox_px:
                        bbox_issue_list.append("tiny_bbox_px")

                class_name = names[cls_id] if 0 <= cls_id < num_classes else "UNKNOWN"

                if 0 <= cls_id < num_classes:
                    class_counts[class_name] += 1

                bbox_rows.append({
                    "split": split,
                    "image_path": str(image_path),
                    "label_path": str(label_path),
                    "line_no": line_no,
                    "class_id": cls_id,
                    "class_name": class_name,
                    "x": x,
                    "y": y,
                    "w": bw,
                    "h": bh,
                    "bbox_w_px": bbox_w_px,
                    "bbox_h_px": bbox_h_px,
                    "bbox_area_ratio": bbox_area_ratio,
                    "bbox_area_px": bbox_area_px,
                    "aspect_ratio": aspect_ratio,
                    "issues": ",".join(bbox_issue_list),
                })

                for issue_name in bbox_issue_list:
                    issue_counts[issue_name] += 1
                    issue_rows.append({
                        "split": split,
                        "image_path": str(image_path),
                        "label_path": str(label_path),
                        "issue": issue_name,
                        "detail": f"line {line_no}: {raw}",
                    })
                    copy_problem_sample(image_path, issue_name, out_dir, copied_counter, args.max_samples)

    image_df = pd.DataFrame(image_rows)
    bbox_df = pd.DataFrame(bbox_rows)
    issue_df = pd.DataFrame(issue_rows)

    image_csv = out_dir / "image_stats.csv"
    bbox_csv = out_dir / "bbox_stats.csv"
    issue_csv = out_dir / "issues.csv"
    class_csv = out_dir / "class_distribution.csv"
    summary_txt = out_dir / "summary.txt"
    summary_json = out_dir / "summary.json"

    image_df.to_csv(image_csv, index=False, encoding="utf-8-sig")
    bbox_df.to_csv(bbox_csv, index=False, encoding="utf-8-sig")
    issue_df.to_csv(issue_csv, index=False, encoding="utf-8-sig")

    total_class_count = sum(class_counts.values())

    if total_class_count > 0:
        class_df = pd.DataFrame([
            {
                "class_name": class_name,
                "count": count,
                "ratio": count / total_class_count,
            }
            for class_name, count in class_counts.items()
        ]).sort_values("count", ascending=False)
    else:
        print("[WARN] No valid class objects were counted.")
        print("[WARN] Check label paths, empty label files, class IDs, and names in data yaml.")

        class_df = pd.DataFrame(columns=[
            "class_name",
            "count",
            "ratio",
        ])

    class_df.to_csv(class_csv, index=False, encoding="utf-8-sig")

    plots_dir = out_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    save_hist(image_df, "brightness", plots_dir / "brightness_hist.png", "Brightness Distribution", "Brightness")
    save_hist(image_df, "contrast", plots_dir / "contrast_hist.png", "Contrast Distribution", "Contrast")
    save_hist(image_df, "blur_var", plots_dir / "blur_hist.png", "Blur Variance Distribution", "Laplacian Variance")

    if not bbox_df.empty:
        save_hist(bbox_df, "bbox_area_ratio", plots_dir / "bbox_area_ratio_hist.png", "BBox Area Ratio Distribution", "BBox Area Ratio")
        save_hist(bbox_df, "bbox_area_px", plots_dir / "bbox_area_px_hist.png", "BBox Pixel Area Distribution", "BBox Area Pixel")
        save_bar(bbox_df["class_name"].value_counts(), plots_dir / "class_distribution.png", "Class Distribution", "Class", "Count")

    if not issue_df.empty:
        save_bar(issue_df["issue"].value_counts(), plots_dir / "issue_counts.png", "Issue Counts", "Issue", "Count")

    if not image_df.empty:
        plt.figure(figsize=(8, 5))
        plt.scatter(image_df["width"], image_df["height"], s=10)
        plt.title("Image Size Distribution")
        plt.xlabel("Width")
        plt.ylabel("Height")
        plt.tight_layout()
        plt.savefig(plots_dir / "image_size_scatter.png", dpi=150)
        plt.close()

    summary = {
        "data_yaml": str(data_yaml_path),
        "dataset_root": str(root),
        "classes": names,
        "num_images": int(len(image_df)),
        "num_bboxes": int(len(bbox_df)),
        "split_image_counts": {k: len(v) for k, v in split_images.items()},
        "class_counts": dict(class_counts),
        "issue_counts": dict(issue_counts),
        "thresholds": {
            "dark_thr": args.dark_thr,
            "bright_thr": args.bright_thr,
            "low_contrast_thr": args.low_contrast_thr,
            "blur_thr": args.blur_thr,
            "small_bbox_area_ratio": args.small_bbox_area_ratio,
            "tiny_bbox_px": args.tiny_bbox_px,
        },
        "outputs": {
            "image_stats_csv": str(image_csv),
            "bbox_stats_csv": str(bbox_csv),
            "issues_csv": str(issue_csv),
            "class_distribution_csv": str(class_csv),
            "plots_dir": str(plots_dir),
            "samples_dir": str(out_dir / "samples"),
        },
    }

    with open(summary_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    with open(summary_txt, "w", encoding="utf-8") as f:
        f.write("YOLO Dataset Quality Report\n")
        f.write("=" * 40 + "\n\n")

        f.write(f"Data YAML: {data_yaml_path}\n")
        f.write(f"Dataset root: {root}\n")
        f.write(f"Classes: {names}\n\n")

        f.write("[Split Image Counts]\n")
        for split, count in summary["split_image_counts"].items():
            f.write(f"- {split}: {count}\n")
        f.write("\n")

        f.write(f"Total images: {len(image_df)}\n")
        f.write(f"Total bboxes: {len(bbox_df)}\n\n")

        f.write("[Class Counts]\n")
        for class_name, count in class_counts.most_common():
            f.write(f"- {class_name}: {count}\n")
        f.write("\n")

        f.write("[Issue Counts]\n")
        for issue_name, count in issue_counts.most_common():
            f.write(f"- {issue_name}: {count}\n")
        f.write("\n")

        if not image_df.empty:
            f.write("[Image Quality Summary]\n")
            f.write(str(image_df[["brightness", "contrast", "blur_var", "label_obj_count"]].describe()))
            f.write("\n\n")

        if not bbox_df.empty:
            f.write("[BBox Summary]\n")
            f.write(str(bbox_df[["bbox_w_px", "bbox_h_px", "bbox_area_ratio", "bbox_area_px", "aspect_ratio"]].describe()))
            f.write("\n\n")

    print("\n[DONE] Dataset quality report saved.")
    print(f"- Summary: {summary_txt}")
    print(f"- Image stats: {image_csv}")
    print(f"- BBox stats: {bbox_csv}")
    print(f"- Issues: {issue_csv}")
    print(f"- Plots: {plots_dir}")
    print(f"- Problem samples: {out_dir / 'samples'}")


if __name__ == "__main__":
    main()