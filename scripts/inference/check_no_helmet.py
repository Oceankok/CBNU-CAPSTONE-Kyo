from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import pandas as pd
import yaml
from tqdm import tqdm
from ultralytics import YOLO


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
Box = Tuple[float, float, float, float]


# -----------------------------
# YAML / dataset path utilities
# -----------------------------

def load_yolo_yaml(data_yaml_path: Path):
    with data_yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    names = data.get("names", [])
    if isinstance(names, dict):
        names = {int(k): v for k, v in names.items()}
    else:
        names = {i: v for i, v in enumerate(names)}

    root = Path(data.get("path", data_yaml_path.parent))
    if not root.is_absolute():
        root = (data_yaml_path.parent / root).resolve()

    return data, root, names


def resolve_split_images(split_value, root: Path) -> List[Path]:
    if split_value is None:
        return []

    if isinstance(split_value, list):
        values = split_value
    else:
        values = [split_value]

    image_paths: List[Path] = []

    for value in values:
        p = Path(value)
        if not p.is_absolute():
            p = (root / p).resolve()

        if p.is_file() and p.suffix.lower() == ".txt":
            with p.open("r", encoding="utf-8") as f:
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

                    if img_path.suffix.lower() in IMAGE_EXTS:
                        image_paths.append(img_path)

        elif p.is_dir():
            for ext in IMAGE_EXTS:
                image_paths.extend(p.rglob(f"*{ext}"))
                image_paths.extend(p.rglob(f"*{ext.upper()}"))

    return sorted(set(image_paths))


def label_path_for_image(image_path: Path) -> Path:
    """
    일반 YOLO 구조:
    .../train/images/abc.jpg -> .../train/labels/abc.txt
    .../images/train/abc.jpg -> .../labels/train/abc.txt
    둘 다 최대한 대응.
    """
    parts = list(image_path.parts)

    if "images" in parts:
        idx = len(parts) - 1 - parts[::-1].index("images")
        parts[idx] = "labels"
        return Path(*parts).with_suffix(".txt")

    candidate = image_path.parent.parent / "labels" / f"{image_path.stem}.txt"
    if candidate.exists():
        return candidate

    return image_path.parent / f"{image_path.stem}.txt"


def read_yolo_label(label_path: Path) -> List[Dict[str, Any]]:
    rows = []

    if not label_path.exists():
        return rows

    with label_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue

            parts = raw.split()
            if len(parts) < 5:
                rows.append(
                    {
                        "line_no": line_no,
                        "raw": raw,
                        "valid": False,
                        "class_id": None,
                        "x": None,
                        "y": None,
                        "w": None,
                        "h": None,
                    }
                )
                continue

            try:
                class_id = int(float(parts[0]))
                x, y, w, h = map(float, parts[1:5])
                valid = True
            except ValueError:
                class_id = None
                x = y = w = h = None
                valid = False

            rows.append(
                {
                    "line_no": line_no,
                    "raw": raw,
                    "valid": valid,
                    "class_id": class_id,
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                }
            )

    return rows


def yolo_to_xyxy(row: Dict[str, Any], image_w: int, image_h: int) -> Optional[Box]:
    if not row["valid"]:
        return None

    x = row["x"]
    y = row["y"]
    w = row["w"]
    h = row["h"]

    x1 = (x - w / 2) * image_w
    y1 = (y - h / 2) * image_h
    x2 = (x + w / 2) * image_w
    y2 = (y + h / 2) * image_h

    return float(x1), float(y1), float(x2), float(y2)


# -----------------------------
# Detection / post-processing
# -----------------------------

def normalize_model_names(names: Any) -> Dict[int, str]:
    if isinstance(names, dict):
        return {int(k): str(v) for k, v in names.items()}

    return {i: str(v) for i, v in enumerate(names)}


def get_class_id_by_aliases(names: Dict[int, str], aliases: List[str]) -> Optional[int]:
    normalized_aliases = {a.lower().replace("_", "").replace("-", "").replace(" ", "") for a in aliases}

    for class_id, name in names.items():
        normalized_name = name.lower().replace("_", "").replace("-", "").replace(" ", "")
        if normalized_name in normalized_aliases:
            return class_id

    return None


def box_center(box: Box) -> Tuple[float, float]:
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2, (y1 + y2) / 2


def center_inside(inner_box: Box, outer_box: Box) -> bool:
    cx, cy = box_center(inner_box)
    x1, y1, x2, y2 = outer_box

    return x1 <= cx <= x2 and y1 <= cy <= y2


def box_iou(box_a: Box, box_b: Box) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h

    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)

    union = area_a + area_b - inter_area
    if union <= 0:
        return 0.0

    return inter_area / union


def make_head_region(
    person_box: Box,
    head_ratio: float = 0.38,
    x_expand_ratio: float = 0.12,
    y_expand_ratio: float = 0.08,
) -> Box:
    """
    person bbox 상단 영역을 helmet 탐색 영역으로 사용.
    """
    x1, y1, x2, y2 = person_box

    w = x2 - x1
    h = y2 - y1

    hx1 = x1 - w * x_expand_ratio
    hy1 = y1 - h * y_expand_ratio
    hx2 = x2 + w * x_expand_ratio
    hy2 = y1 + h * head_ratio

    return hx1, hy1, hx2, hy2


def extract_detections(result: Any, names: Dict[int, str]) -> List[Dict[str, Any]]:
    detections = []

    if result.boxes is None:
        return detections

    for box in result.boxes:
        cls_id = int(box.cls[0].item())
        conf = float(box.conf[0].item())
        xyxy = box.xyxy[0].detach().cpu().tolist()

        detections.append(
            {
                "class_id": cls_id,
                "class_name": names.get(cls_id, f"unknown_{cls_id}"),
                "confidence": conf,
                "box": tuple(float(v) for v in xyxy),
            }
        )

    return detections


def predict_worker_helmet_states(
    result: Any,
    model_names: Any,
    person_conf_thr: float = 0.40,
    helmet_conf_thr: float = 0.35,
    helmet_iou_thr: float = 0.01,
    head_ratio: float = 0.38,
) -> List[Dict[str, Any]]:
    """
    각 person에 대해 helmet 존재 여부를 후처리로 판단.
    """
    names = normalize_model_names(model_names)

    person_id = get_class_id_by_aliases(
        names,
        aliases=["person", "worker", "human"],
    )
    helmet_id = get_class_id_by_aliases(
        names,
        aliases=["helmet", "hardhat", "hard_hat", "safety helmet"],
    )

    if person_id is None:
        raise ValueError(f"'person' class not found in model.names: {names}")

    if helmet_id is None:
        raise ValueError(f"'helmet' class not found in model.names: {names}")

    detections = extract_detections(result, names)

    persons = [
        det for det in detections
        if det["class_id"] == person_id and det["confidence"] >= person_conf_thr
    ]

    helmets = [
        det for det in detections
        if det["class_id"] == helmet_id and det["confidence"] >= helmet_conf_thr
    ]

    worker_states = []

    for person_idx, person in enumerate(persons, start=1):
        person_box = person["box"]
        head_region = make_head_region(person_box, head_ratio=head_ratio)

        matched_helmet = None
        best_score = 0.0
        best_iou = 0.0
        best_center_inside = False

        for helmet in helmets:
            helmet_box = helmet["box"]

            center_ok = center_inside(helmet_box, head_region)
            iou_score = box_iou(helmet_box, head_region)
            iou_ok = iou_score >= helmet_iou_thr

            if center_ok or iou_ok:
                score = helmet["confidence"] + iou_score

                if score > best_score:
                    best_score = score
                    best_iou = iou_score
                    best_center_inside = center_ok
                    matched_helmet = helmet

        has_helmet = matched_helmet is not None

        worker_states.append(
            {
                "worker_id": person_idx,
                "person_box": list(person_box),
                "person_confidence": person["confidence"],
                "head_region": list(head_region),
                "has_helmet": has_helmet,
                "status": "SAFE_HELMET" if has_helmet else "NO_HELMET",
                "matched_helmet_box": list(matched_helmet["box"]) if matched_helmet else None,
                "matched_helmet_confidence": matched_helmet["confidence"] if matched_helmet else None,
                "helmet_match_iou": best_iou if matched_helmet else None,
                "helmet_center_inside_head_region": best_center_inside if matched_helmet else None,
            }
        )

    return worker_states


# -----------------------------
# Visualization
# -----------------------------

def clamp_box(box: Box, image_w: int, image_h: int) -> Tuple[int, int, int, int]:
    x1, y1, x2, y2 = box

    x1 = int(max(0, min(image_w - 1, round(x1))))
    y1 = int(max(0, min(image_h - 1, round(y1))))
    x2 = int(max(0, min(image_w - 1, round(x2))))
    y2 = int(max(0, min(image_h - 1, round(y2))))

    return x1, y1, x2, y2


def draw_label(
    image: np.ndarray,
    text: str,
    x: int,
    y: int,
    color: Tuple[int, int, int],
):
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.55
    thickness = 2

    (tw, th), baseline = cv2.getTextSize(text, font, font_scale, thickness)

    y_text = max(th + baseline + 4, y)
    cv2.rectangle(
        image,
        (x, y_text - th - baseline - 4),
        (x + tw + 6, y_text + baseline),
        color,
        -1,
    )
    cv2.putText(
        image,
        text,
        (x + 3, y_text - 4),
        font,
        font_scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )


def save_annotated_image(
    image_path: Path,
    label_rows: List[Dict[str, Any]],
    label3_id: int,
    worker_states: List[Dict[str, Any]],
    out_path: Path,
):
    image = cv2.imread(str(image_path))
    if image is None:
        return

    image_h, image_w = image.shape[:2]

    # 기존 label 3 bbox 표시
    for row in label_rows:
        if row["valid"] and row["class_id"] == label3_id:
            box = yolo_to_xyxy(row, image_w, image_h)
            if box is None:
                continue

            x1, y1, x2, y2 = clamp_box(box, image_w, image_h)
            cv2.rectangle(image, (x1, y1), (x2, y2), (128, 0, 255), 2)
            draw_label(image, f"GT class {label3_id}", x1, y1, (128, 0, 255))

    # 예측 person / head region / status 표시
    for state in worker_states:
        person_box = tuple(state["person_box"])
        head_region = tuple(state["head_region"])

        px1, py1, px2, py2 = clamp_box(person_box, image_w, image_h)
        hx1, hy1, hx2, hy2 = clamp_box(head_region, image_w, image_h)

        if state["status"] == "NO_HELMET":
            color = (0, 0, 255)
        else:
            color = (0, 180, 0)

        cv2.rectangle(image, (px1, py1), (px2, py2), color, 2)
        draw_label(
            image,
            f"Pred {state['status']} P:{state['person_confidence']:.2f}",
            px1,
            py1,
            color,
        )

        cv2.rectangle(image, (hx1, hy1), (hx2, hy2), (255, 180, 0), 1)

        if state["matched_helmet_box"] is not None:
            helmet_box = tuple(state["matched_helmet_box"])
            mx1, my1, mx2, my2 = clamp_box(helmet_box, image_w, image_h)
            cv2.rectangle(image, (mx1, my1), (mx2, my2), (255, 0, 0), 2)
            draw_label(
                image,
                f"helmet {state['matched_helmet_confidence']:.2f}",
                mx1,
                my1,
                (255, 0, 0),
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), image)


def safe_rel_name(image_path: Path, root: Path, split: str) -> str:
    try:
        rel = image_path.relative_to(root)
    except ValueError:
        rel = image_path.name

    rel_text = str(rel).replace("\\", "__").replace("/", "__")
    return f"{split}__{rel_text}"


# -----------------------------
# Main
# -----------------------------

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--model",
        required=True,
        help="Trained YOLO model path, e.g. runs/ppe/final_ppe_baseline-2/weights/best.pt",
    )
    parser.add_argument(
        "--data",
        required=True,
        help="YOLO dataset yaml path, e.g. configs/merged_ppe.yaml",
    )
    parser.add_argument(
        "--out",
        default="reports/no_helmet_prediction_vs_label3",
        help="Output report directory",
    )

    parser.add_argument(
        "--splits",
        nargs="+",
        default=["val", "test"],
        help="Dataset splits to inspect. Default: val test",
    )

    parser.add_argument(
        "--label3-id",
        type=int,
        default=3,
        help="Existing no_helmet label class id to compare. Default: 3",
    )

    parser.add_argument(
        "--predict-conf",
        type=float,
        default=0.25,
        help="YOLO predict confidence. Keep this low enough for post-processing. Default: 0.25",
    )
    parser.add_argument(
        "--person-conf-thr",
        type=float,
        default=0.40,
        help="Person confidence threshold for post-processing. Default: 0.40",
    )
    parser.add_argument(
        "--helmet-conf-thr",
        type=float,
        default=0.35,
        help="Helmet confidence threshold for post-processing. Default: 0.35",
    )
    parser.add_argument(
        "--helmet-iou-thr",
        type=float,
        default=0.01,
        help="Helmet/head-region IoU threshold. Default: 0.01",
    )
    parser.add_argument(
        "--head-ratio",
        type=float,
        default=0.38,
        help="Top ratio of person bbox used as head region. Default: 0.38",
    )

    parser.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="Optional max number of images per split for quick test.",
    )
    parser.add_argument(
        "--save-samples",
        action="store_true",
        help="Save annotated sample images by match category.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=100,
        help="Max annotated samples per category. Default: 100",
    )

    args = parser.parse_args()

    model_path = Path(args.model).resolve()
    data_yaml_path = Path(args.data).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    data, dataset_root, dataset_names = load_yolo_yaml(data_yaml_path)

    print(f"[INFO] model: {model_path}")
    print(f"[INFO] data yaml: {data_yaml_path}")
    print(f"[INFO] dataset root: {dataset_root}")
    print(f"[INFO] dataset names: {dataset_names}")
    print(f"[INFO] splits: {args.splits}")
    print(f"[INFO] compare label id: {args.label3_id}")

    model = YOLO(str(model_path))
    model_names = normalize_model_names(model.names)

    print(f"[INFO] model names: {model_names}")

    rows = []
    worker_rows = []
    sample_counts = Counter()

    for split in args.splits:
        if split not in data:
            print(f"[WARN] split '{split}' not found in yaml. Skipped.")
            continue

        image_paths = resolve_split_images(data.get(split), dataset_root)

        if args.max_images is not None:
            image_paths = image_paths[: args.max_images]

        print(f"[INFO] {split}: {len(image_paths)} images")

        for image_path in tqdm(image_paths, desc=f"Inspect {split}"):
            label_path = label_path_for_image(image_path)
            label_rows = read_yolo_label(label_path)

            label3_rows = [
                row for row in label_rows
                if row["valid"] and row["class_id"] == args.label3_id
            ]

            has_label3 = len(label3_rows) > 0
            label3_count = len(label3_rows)

            try:
                results = model.predict(
                    source=str(image_path),
                    conf=args.predict_conf,
                    verbose=False,
                )
            except Exception as e:
                rows.append(
                    {
                        "split": split,
                        "image_path": str(image_path),
                        "label_path": str(label_path),
                        "error": str(e),
                        "has_label3": has_label3,
                        "label3_count": label3_count,
                        "pred_no_helmet": None,
                        "pred_no_helmet_count": None,
                        "num_pred_person": None,
                        "match_type": "error",
                    }
                )
                continue

            if len(results) == 0:
                worker_states = []
            else:
                worker_states = predict_worker_helmet_states(
                    result=results[0],
                    model_names=model.names,
                    person_conf_thr=args.person_conf_thr,
                    helmet_conf_thr=args.helmet_conf_thr,
                    helmet_iou_thr=args.helmet_iou_thr,
                    head_ratio=args.head_ratio,
                )

            pred_no_helmet_workers = [
                state for state in worker_states
                if state["status"] == "NO_HELMET"
            ]

            pred_no_helmet = len(pred_no_helmet_workers) > 0
            pred_no_helmet_count = len(pred_no_helmet_workers)
            num_pred_person = len(worker_states)

            if pred_no_helmet and has_label3:
                match_type = "TP_pred_no_helmet_and_label3"
            elif pred_no_helmet and not has_label3:
                match_type = "FP_pred_no_helmet_but_no_label3"
            elif (not pred_no_helmet) and has_label3:
                match_type = "FN_label3_but_not_pred_no_helmet"
            else:
                match_type = "TN_no_pred_no_helmet_and_no_label3"

            rows.append(
                {
                    "split": split,
                    "image_path": str(image_path),
                    "label_path": str(label_path),
                    "error": "",
                    "has_label3": has_label3,
                    "label3_count": label3_count,
                    "pred_no_helmet": pred_no_helmet,
                    "pred_no_helmet_count": pred_no_helmet_count,
                    "num_pred_person": num_pred_person,
                    "match_type": match_type,
                }
            )

            for state in worker_states:
                worker_rows.append(
                    {
                        "split": split,
                        "image_path": str(image_path),
                        "label_path": str(label_path),
                        "has_label3_in_image": has_label3,
                        "label3_count_in_image": label3_count,
                        "worker_id": state["worker_id"],
                        "status": state["status"],
                        "has_helmet": state["has_helmet"],
                        "person_confidence": state["person_confidence"],
                        "matched_helmet_confidence": state["matched_helmet_confidence"],
                        "helmet_match_iou": state["helmet_match_iou"],
                        "helmet_center_inside_head_region": state["helmet_center_inside_head_region"],
                        "person_box": json.dumps(state["person_box"], ensure_ascii=False),
                        "head_region": json.dumps(state["head_region"], ensure_ascii=False),
                        "matched_helmet_box": json.dumps(state["matched_helmet_box"], ensure_ascii=False),
                    }
                )

            if args.save_samples:
                if sample_counts[match_type] < args.sample_limit:
                    sample_name = safe_rel_name(image_path, dataset_root, split)
                    out_img_path = out_dir / "samples" / match_type / sample_name

                    save_annotated_image(
                        image_path=image_path,
                        label_rows=label_rows,
                        label3_id=args.label3_id,
                        worker_states=worker_states,
                        out_path=out_img_path,
                    )

                    # txt 라벨도 같이 복사
                    if label_path.exists():
                        out_label_path = out_img_path.with_suffix(".txt")
                        shutil.copy2(label_path, out_label_path)

                    sample_counts[match_type] += 1

    image_df = pd.DataFrame(rows)
    worker_df = pd.DataFrame(worker_rows)

    image_csv = out_dir / "image_level_no_helmet_vs_label3.csv"
    worker_csv = out_dir / "worker_level_no_helmet_predictions.csv"
    summary_txt = out_dir / "summary.txt"
    summary_json = out_dir / "summary.json"

    image_df.to_csv(image_csv, index=False, encoding="utf-8-sig")
    worker_df.to_csv(worker_csv, index=False, encoding="utf-8-sig")

    if image_df.empty:
        print("[WARN] No image rows were generated.")
        return

    valid_df = image_df[image_df["error"] == ""].copy()

    tp = int((valid_df["match_type"] == "TP_pred_no_helmet_and_label3").sum())
    fp = int((valid_df["match_type"] == "FP_pred_no_helmet_but_no_label3").sum())
    fn = int((valid_df["match_type"] == "FN_label3_but_not_pred_no_helmet").sum())
    tn = int((valid_df["match_type"] == "TN_no_pred_no_helmet_and_no_label3").sum())

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    accuracy = (tp + tn) / max(1, tp + fp + fn + tn)

    match_counts = valid_df["match_type"].value_counts().to_dict()

    summary = {
        "model": str(model_path),
        "data_yaml": str(data_yaml_path),
        "dataset_root": str(dataset_root),
        "splits": args.splits,
        "label3_id": args.label3_id,
        "num_images": int(len(valid_df)),
        "match_counts": match_counts,
        "confusion_matrix_image_level": {
            "TP_pred_no_helmet_and_label3": tp,
            "FP_pred_no_helmet_but_no_label3": fp,
            "FN_label3_but_not_pred_no_helmet": fn,
            "TN_no_pred_no_helmet_and_no_label3": tn,
        },
        "metrics_image_level": {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "accuracy": accuracy,
        },
        "thresholds": {
            "predict_conf": args.predict_conf,
            "person_conf_thr": args.person_conf_thr,
            "helmet_conf_thr": args.helmet_conf_thr,
            "helmet_iou_thr": args.helmet_iou_thr,
            "head_ratio": args.head_ratio,
        },
        "outputs": {
            "image_csv": str(image_csv),
            "worker_csv": str(worker_csv),
            "summary_txt": str(summary_txt),
            "samples_dir": str(out_dir / "samples"),
        },
    }

    with summary_json.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    with summary_txt.open("w", encoding="utf-8") as f:
        f.write("NO_HELMET Prediction vs Existing Label 3 Report\n")
        f.write("=" * 60 + "\n\n")

        f.write(f"Model: {model_path}\n")
        f.write(f"Data YAML: {data_yaml_path}\n")
        f.write(f"Dataset root: {dataset_root}\n")
        f.write(f"Splits: {args.splits}\n")
        f.write(f"Compared label id: {args.label3_id}\n\n")

        f.write("[Image-level Confusion Matrix]\n")
        f.write(f"TP  pred_no_helmet=True  & label3=True : {tp}\n")
        f.write(f"FP  pred_no_helmet=True  & label3=False: {fp}\n")
        f.write(f"FN  pred_no_helmet=False & label3=True : {fn}\n")
        f.write(f"TN  pred_no_helmet=False & label3=False: {tn}\n\n")

        f.write("[Image-level Metrics]\n")
        f.write(f"Precision: {precision:.4f}\n")
        f.write(f"Recall   : {recall:.4f}\n")
        f.write(f"F1       : {f1:.4f}\n")
        f.write(f"Accuracy : {accuracy:.4f}\n\n")

        f.write("[Match Type Counts]\n")
        for k, v in match_counts.items():
            f.write(f"- {k}: {v}\n")
        f.write("\n")

        f.write("[Thresholds]\n")
        f.write(f"predict_conf    : {args.predict_conf}\n")
        f.write(f"person_conf_thr : {args.person_conf_thr}\n")
        f.write(f"helmet_conf_thr : {args.helmet_conf_thr}\n")
        f.write(f"helmet_iou_thr  : {args.helmet_iou_thr}\n")
        f.write(f"head_ratio      : {args.head_ratio}\n\n")

        f.write("[Outputs]\n")
        f.write(f"- Image-level CSV : {image_csv}\n")
        f.write(f"- Worker-level CSV: {worker_csv}\n")
        f.write(f"- Samples         : {out_dir / 'samples'}\n")

    print("\n[DONE] Report saved.")
    print(f"- Summary: {summary_txt}")
    print(f"- Image-level CSV: {image_csv}")
    print(f"- Worker-level CSV: {worker_csv}")
    if args.save_samples:
        print(f"- Annotated samples: {out_dir / 'samples'}")

    print("\n[Image-level Confusion Matrix]")
    print(f"TP: {tp}")
    print(f"FP: {fp}")
    print(f"FN: {fn}")
    print(f"TN: {tn}")

    print("\n[Image-level Metrics]")
    print(f"Precision: {precision:.4f}")
    print(f"Recall   : {recall:.4f}")
    print(f"F1       : {f1:.4f}")
    print(f"Accuracy : {accuracy:.4f}")


if __name__ == "__main__":
    main()