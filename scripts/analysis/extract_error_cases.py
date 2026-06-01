import argparse
import csv
from pathlib import Path

import cv2


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def yolo_to_xyxy(box, img_w, img_h):
    cls_id, x, y, w, h, conf = box

    cx = x * img_w
    cy = y * img_h
    bw = w * img_w
    bh = h * img_h

    x1 = cx - bw / 2
    y1 = cy - bh / 2
    x2 = cx + bw / 2
    y2 = cy + bh / 2

    return [x1, y1, x2, y2]


def box_area(xyxy):
    x1, y1, x2, y2 = xyxy
    return max(0, x2 - x1) * max(0, y2 - y1)


def iou(box_a, box_b):
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    inter = box_area([ix1, iy1, ix2, iy2])
    union = box_area(box_a) + box_area(box_b) - inter

    if union <= 0:
        return 0.0

    return inter / union


def load_yolo_txt(txt_path, has_conf=False, conf_thr=0.0):
    boxes = []

    if not txt_path.exists():
        return boxes

    with open(txt_path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()

            if len(parts) < 5:
                continue

            cls_id = int(float(parts[0]))
            x = float(parts[1])
            y = float(parts[2])
            w = float(parts[3])
            h = float(parts[4])

            if has_conf:
                conf = float(parts[5]) if len(parts) >= 6 else 1.0

                # 핵심 수정: confidence 낮은 예측 박스는 오류 분석에서 제외
                if conf < conf_thr:
                    continue
            else:
                conf = 1.0

            boxes.append([cls_id, x, y, w, h, conf])

    return boxes


def draw_boxes(image, gt_boxes, pred_boxes, class_names):
    img_h, img_w = image.shape[:2]

    # GT: green
    for box in gt_boxes:
        cls_id = box[0]
        x1, y1, x2, y2 = map(int, yolo_to_xyxy(box, img_w, img_h))
        name = class_names[cls_id] if cls_id < len(class_names) else str(cls_id)

        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 180, 0), 2)
        cv2.putText(
            image,
            f"GT:{name}",
            (x1, max(15, y1 - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 180, 0),
            1,
        )

    # Pred: red
    for box in pred_boxes:
        cls_id = box[0]
        conf = box[5]
        x1, y1, x2, y2 = map(int, yolo_to_xyxy(box, img_w, img_h))
        name = class_names[cls_id] if cls_id < len(class_names) else str(cls_id)

        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 220), 2)
        cv2.putText(
            image,
            f"P:{name} {conf:.2f}",
            (x1, min(img_h - 5, y2 + 15)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 0, 220),
            1,
        )

    return image


def collect_images(images_dir):
    images_dir = Path(images_dir)

    return sorted(
        p for p in images_dir.rglob("*")
        if p.suffix.lower() in IMAGE_EXTS
    )


def save_annotated_image(
    image_path,
    image,
    gt_boxes,
    pred_boxes,
    class_names,
    out_dir,
    error_types,
):
    annotated = draw_boxes(image.copy(), gt_boxes, pred_boxes, class_names)

    for error_type in error_types:
        save_dir = out_dir / error_type
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / image_path.name
        cv2.imwrite(str(save_path), annotated)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--images", required=True, help="validation image directory")
    parser.add_argument("--gt-labels", required=True, help="ground truth label directory")
    parser.add_argument("--pred-labels", required=True, help="prediction label directory")
    parser.add_argument("--out", default="runs/detect/error_cases")

    parser.add_argument(
        "--classes",
        nargs="+",
        default=["helmet", "vest", "person"],
        help="class names in YOLO id order",
    )

    parser.add_argument(
        "--target-classes",
        nargs="*",
        default=None,
        help="optional class filter, e.g. --target-classes person vest",
    )

    parser.add_argument(
        "--conf-thr",
        type=float,
        default=0.25,
        help="ignore prediction boxes below this confidence",
    )

    parser.add_argument(
        "--iou-thr",
        type=float,
        default=0.5,
        help="IoU threshold for correct detection",
    )

    parser.add_argument(
        "--loc-iou-min",
        type=float,
        default=0.1,
        help="minimum IoU to classify unmatched GT as localization_error instead of false_negative",
    )

    parser.add_argument(
        "--wide-ratio",
        type=float,
        default=1.5,
        help="pred area / gt area above this value is too_wide",
    )

    parser.add_argument(
        "--narrow-ratio",
        type=float,
        default=0.67,
        help="pred area / gt area below this value is too_narrow",
    )

    args = parser.parse_args()

    images_dir = Path(args.images)
    gt_dir = Path(args.gt_labels)
    pred_dir = Path(args.pred_labels)
    out_dir = Path(args.out)

    class_names = args.classes

    if args.target_classes:
        target_class_ids = {
            class_names.index(name)
            for name in args.target_classes
            if name in class_names
        }
    else:
        target_class_ids = None

    for category in [
        "false_negative",
        "false_positive",
        "localization_error",
        "too_wide",
        "too_narrow",
    ]:
        (out_dir / category).mkdir(parents=True, exist_ok=True)

    report_rows = []
    image_files = collect_images(images_dir)

    print(f"[INFO] images_dir: {images_dir}")
    print(f"[INFO] gt_labels: {gt_dir}")
    print(f"[INFO] pred_labels: {pred_dir}")
    print(f"[INFO] out_dir: {out_dir}")
    print(f"[INFO] class_names: {class_names}")
    print(f"[INFO] conf_thr: {args.conf_thr}")
    print(f"[INFO] found images: {len(image_files)}")

    if not image_files:
        print("[WARN] No images found. Check --images path.")

    pred_txt_count = len(list(pred_dir.glob("*.txt"))) if pred_dir.exists() else 0
    print(f"[INFO] prediction txt count: {pred_txt_count}")

    if pred_txt_count == 0:
        print("[WARN] No prediction txt files found. Check --pred-labels path.")

    for image_path in image_files:
        image = cv2.imread(str(image_path))

        if image is None:
            print(f"[WARN] Failed to read image: {image_path}")
            continue

        img_h, img_w = image.shape[:2]
        stem = image_path.stem

        gt_txt = gt_dir / f"{stem}.txt"
        pred_txt = pred_dir / f"{stem}.txt"

        gt_boxes = load_yolo_txt(gt_txt, has_conf=False)
        pred_boxes = load_yolo_txt(
            pred_txt,
            has_conf=True,
            conf_thr=args.conf_thr,
        )

        if target_class_ids is not None:
            gt_boxes = [b for b in gt_boxes if b[0] in target_class_ids]
            pred_boxes = [b for b in pred_boxes if b[0] in target_class_ids]

        if not gt_boxes and not pred_boxes:
            continue

        gt_xyxy = [yolo_to_xyxy(b, img_w, img_h) for b in gt_boxes]
        pred_xyxy = [yolo_to_xyxy(b, img_w, img_h) for b in pred_boxes]

        matched_gt = set()
        matched_pred = set()
        image_error_types = set()

        # 1. 같은 클래스끼리 IoU 기준 매칭
        pairs = []

        for gi, gt_box in enumerate(gt_boxes):
            for pi, pred_box in enumerate(pred_boxes):
                if gt_box[0] != pred_box[0]:
                    continue

                score = iou(gt_xyxy[gi], pred_xyxy[pi])
                pairs.append((score, gi, pi))

        pairs.sort(reverse=True)

        for score, gi, pi in pairs:
            if score < args.iou_thr:
                continue

            if gi in matched_gt or pi in matched_pred:
                continue

            matched_gt.add(gi)
            matched_pred.add(pi)

            gt_area = box_area(gt_xyxy[gi])
            pred_area = box_area(pred_xyxy[pi])

            if gt_area <= 0:
                continue

            area_ratio = pred_area / gt_area

            cls_id = gt_boxes[gi][0]
            cls_name = class_names[cls_id] if cls_id < len(class_names) else str(cls_id)

            if area_ratio >= args.wide_ratio:
                image_error_types.add("too_wide")
                report_rows.append([
                    image_path.name,
                    cls_name,
                    "too_wide",
                    round(score, 4),
                    round(area_ratio, 4),
                    round(pred_boxes[pi][5], 4),
                ])

            elif area_ratio <= args.narrow_ratio:
                image_error_types.add("too_narrow")
                report_rows.append([
                    image_path.name,
                    cls_name,
                    "too_narrow",
                    round(score, 4),
                    round(area_ratio, 4),
                    round(pred_boxes[pi][5], 4),
                ])

        # 2. 매칭되지 않은 GT → FN 또는 localization_error
        for gi, gt_box in enumerate(gt_boxes):
            if gi in matched_gt:
                continue

            cls_id = gt_box[0]
            cls_name = class_names[cls_id] if cls_id < len(class_names) else str(cls_id)

            best_iou = 0.0
            best_conf = 0.0

            for pi, pred_box in enumerate(pred_boxes):
                if pred_box[0] != cls_id:
                    continue

                score = iou(gt_xyxy[gi], pred_xyxy[pi])

                if score > best_iou:
                    best_iou = score
                    best_conf = pred_box[5]

            if args.loc_iou_min <= best_iou < args.iou_thr:
                error_type = "localization_error"
            else:
                error_type = "false_negative"

            image_error_types.add(error_type)

            report_rows.append([
                image_path.name,
                cls_name,
                error_type,
                round(best_iou, 4),
                "",
                round(best_conf, 4),
            ])

        # 3. 매칭되지 않은 Pred → FP
        for pi, pred_box in enumerate(pred_boxes):
            if pi in matched_pred:
                continue

            cls_id = pred_box[0]
            cls_name = class_names[cls_id] if cls_id < len(class_names) else str(cls_id)

            best_iou = 0.0

            for gi, gt_box in enumerate(gt_boxes):
                if gt_box[0] != cls_id:
                    continue

                score = iou(pred_xyxy[pi], gt_xyxy[gi])
                best_iou = max(best_iou, score)

            if best_iou < args.iou_thr:
                image_error_types.add("false_positive")

                report_rows.append([
                    image_path.name,
                    cls_name,
                    "false_positive",
                    round(best_iou, 4),
                    "",
                    round(pred_box[5], 4),
                ])

        if image_error_types:
            save_annotated_image(
                image_path=image_path,
                image=image,
                gt_boxes=gt_boxes,
                pred_boxes=pred_boxes,
                class_names=class_names,
                out_dir=out_dir,
                error_types=image_error_types,
            )

    csv_path = out_dir / "error_report.csv"

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "image",
            "class",
            "error_type",
            "iou",
            "area_ratio",
            "confidence",
        ])
        writer.writerows(report_rows)

    counts = {}
    class_counts = {}

    for row in report_rows:
        cls_name = row[1]
        error_type = row[2]

        counts[error_type] = counts.get(error_type, 0) + 1

        key = (cls_name, error_type)
        class_counts[key] = class_counts.get(key, 0) + 1

    summary_path = out_dir / "summary.txt"

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("Error Case Summary\n")
        f.write("==================\n\n")

        f.write(f"confidence_threshold: {args.conf_thr}\n")
        f.write(f"iou_threshold: {args.iou_thr}\n")
        f.write(f"total_error_rows: {len(report_rows)}\n\n")

        f.write("[By Error Type]\n")
        for key, value in sorted(counts.items()):
            f.write(f"{key}: {value}\n")

        f.write("\n[By Class and Error Type]\n")
        for (cls_name, error_type), value in sorted(class_counts.items()):
            f.write(f"{cls_name} / {error_type}: {value}\n")

    print(f"[DONE] error report saved: {csv_path}")
    print(f"[DONE] summary saved: {summary_path}")
    print(f"[DONE] error images saved under: {out_dir}")


if __name__ == "__main__":
    main()