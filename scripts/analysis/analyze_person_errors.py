from __future__ import annotations

import argparse
import csv
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
from ultralytics import YOLO


PERSON_CLASS_ID = 2


@dataclass
class Box:
    cls: int
    conf: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def w(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def h(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.w * self.h


def yolo_to_xyxy(line: str, img_w: int, img_h: int) -> Box | None:
    parts = line.strip().split()
    if len(parts) != 5:
        return None

    cls = int(float(parts[0]))
    x, y, w, h = map(float, parts[1:])

    x1 = (x - w / 2) * img_w
    y1 = (y - h / 2) * img_h
    x2 = (x + w / 2) * img_w
    y2 = (y + h / 2) * img_h

    return Box(cls=cls, conf=1.0, x1=x1, y1=y1, x2=x2, y2=y2)


def load_gt_person_boxes(label_path: Path, img_w: int, img_h: int) -> list[Box]:
    if not label_path.exists():
        return []

    boxes: list[Box] = []

    for line in label_path.read_text(encoding="utf-8").splitlines():
        box = yolo_to_xyxy(line, img_w, img_h)
        if box is None:
            continue
        if box.cls == PERSON_CLASS_ID:
            boxes.append(box)

    return boxes


def iou(a: Box, b: Box) -> float:
    ix1 = max(a.x1, b.x1)
    iy1 = max(a.y1, b.y1)
    ix2 = min(a.x2, b.x2)
    iy2 = min(a.y2, b.y2)

    inter_w = max(0.0, ix2 - ix1)
    inter_h = max(0.0, iy2 - iy1)
    inter = inter_w * inter_h

    union = a.area + b.area - inter
    if union <= 0:
        return 0.0

    return inter / union


def is_small_object(box: Box, img_w: int, img_h: int, area_thr: float) -> bool:
    img_area = img_w * img_h
    return (box.area / img_area) < area_thr


def is_partial_candidate(box: Box, img_w: int, img_h: int, margin_ratio: float = 0.02) -> bool:
    margin_x = img_w * margin_ratio
    margin_y = img_h * margin_ratio

    return (
        box.x1 <= margin_x
        or box.y1 <= margin_y
        or box.x2 >= img_w - margin_x
        or box.y2 >= img_h - margin_y
    )


def max_overlap_with_other_person(target: Box, boxes: list[Box]) -> float:
    overlaps = []

    for other in boxes:
        if other is target:
            continue
        overlaps.append(iou(target, other))

    return max(overlaps) if overlaps else 0.0


def predict_person_boxes(model: YOLO, image_path: Path, imgsz: int, conf: float) -> list[Box]:
    results = model.predict(
        source=str(image_path),
        imgsz=imgsz,
        conf=conf,
        verbose=False,
        save=False,
    )

    pred_boxes: list[Box] = []

    if not results:
        return pred_boxes

    result = results[0]

    if result.boxes is None:
        return pred_boxes

    for b in result.boxes:
        cls = int(b.cls.item())
        if cls != PERSON_CLASS_ID:
            continue

        x1, y1, x2, y2 = b.xyxy[0].tolist()
        score = float(b.conf.item())

        pred_boxes.append(
            Box(
                cls=cls,
                conf=score,
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
            )
        )

    return pred_boxes


def match_boxes(gt_boxes: list[Box], pred_boxes: list[Box], match_iou_thr: float):
    matches = []
    used_preds = set()

    for gt_idx, gt in enumerate(gt_boxes):
        best_pred_idx = None
        best_iou = 0.0

        for pred_idx, pred in enumerate(pred_boxes):
            if pred_idx in used_preds:
                continue

            score = iou(gt, pred)
            if score > best_iou:
                best_iou = score
                best_pred_idx = pred_idx

        if best_pred_idx is not None and best_iou >= match_iou_thr:
            used_preds.add(best_pred_idx)
            matches.append((gt_idx, best_pred_idx, best_iou))
        else:
            matches.append((gt_idx, None, best_iou))

    unmatched_preds = [
        pred_idx for pred_idx in range(len(pred_boxes))
        if pred_idx not in used_preds
    ]

    return matches, unmatched_preds


def draw_box(img, box: Box, color, text: str):
    x1, y1, x2, y2 = map(int, [box.x1, box.y1, box.x2, box.y2])
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
    cv2.putText(
        img,
        text,
        (x1, max(20, y1 - 5)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        color,
        1,
        cv2.LINE_AA,
    )


def save_annotated_image(
    image_path: Path,
    output_path: Path,
    gt_boxes: list[Box],
    pred_boxes: list[Box],
    rows: list[dict],
):
    img = cv2.imread(str(image_path))
    if img is None:
        return

    for gt in gt_boxes:
        draw_box(img, gt, (0, 255, 0), "GT person")

    for pred in pred_boxes:
        draw_box(img, pred, (0, 0, 255), f"PRED person {pred.conf:.2f}")

    issue_types = sorted(set(row["issue_type"] for row in rows))
    title = ", ".join(issue_types[:3])

    cv2.putText(
        img,
        title,
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), img)


def analyze_image(
    model: YOLO,
    image_path: Path,
    label_dir: Path,
    output_img_dir: Path,
    imgsz: int,
    conf: float,
    match_iou_thr: float,
    loc_iou_thr: float,
    wide_ratio: float,
    narrow_ratio: float,
    small_area_thr: float,
) -> list[dict]:
    img = cv2.imread(str(image_path))
    if img is None:
        return []

    img_h, img_w = img.shape[:2]
    label_path = label_dir / f"{image_path.stem}.txt"

    gt_boxes = load_gt_person_boxes(label_path, img_w, img_h)
    pred_boxes = predict_person_boxes(model, image_path, imgsz, conf)

    matches, unmatched_preds = match_boxes(gt_boxes, pred_boxes, match_iou_thr)

    rows: list[dict] = []

    for gt_idx, pred_idx, best_iou in matches:
        gt = gt_boxes[gt_idx]
        crowded_iou = max_overlap_with_other_person(gt, gt_boxes)

        common = {
            "image": image_path.name,
            "gt_index": gt_idx,
            "pred_index": "" if pred_idx is None else pred_idx,
            "iou": f"{best_iou:.4f}",
            "gt_w": f"{gt.w:.2f}",
            "gt_h": f"{gt.h:.2f}",
            "gt_area_ratio": f"{gt.area / (img_w * img_h):.6f}",
            "pred_w": "",
            "pred_h": "",
            "width_ratio": "",
            "height_ratio": "",
            "pred_conf": "",
            "small_object": is_small_object(gt, img_w, img_h, small_area_thr),
            "partial_candidate": is_partial_candidate(gt, img_w, img_h),
            "crowded_candidate": crowded_iou >= 0.10,
            "max_person_overlap_iou": f"{crowded_iou:.4f}",
        }

        if pred_idx is None:
            rows.append(
                {
                    **common,
                    "issue_type": "FN_person",
                    "reason": "정답 person이 있지만 예측 person이 매칭되지 않음",
                }
            )
            continue

        pred = pred_boxes[pred_idx]
        width_ratio_value = pred.w / gt.w if gt.w > 0 else 0.0
        height_ratio_value = pred.h / gt.h if gt.h > 0 else 0.0

        base = {
            **common,
            "pred_w": f"{pred.w:.2f}",
            "pred_h": f"{pred.h:.2f}",
            "width_ratio": f"{width_ratio_value:.4f}",
            "height_ratio": f"{height_ratio_value:.4f}",
            "pred_conf": f"{pred.conf:.4f}",
        }

        if best_iou < loc_iou_thr:
            rows.append(
                {
                    **base,
                    "issue_type": "Localization_Error",
                    "reason": "객체는 찾았지만 IoU가 낮아 박스 정밀도 부족",
                }
            )

        if width_ratio_value >= wide_ratio or height_ratio_value >= wide_ratio:
            rows.append(
                {
                    **base,
                    "issue_type": "Too_Wide_Box",
                    "reason": "예측 박스가 정답 박스보다 과도하게 큼",
                }
            )

        if width_ratio_value <= narrow_ratio or height_ratio_value <= narrow_ratio:
            rows.append(
                {
                    **base,
                    "issue_type": "Too_Narrow_Box",
                    "reason": "예측 박스가 정답 박스보다 과도하게 작음",
                }
            )

        if is_small_object(gt, img_w, img_h, small_area_thr):
            rows.append(
                {
                    **base,
                    "issue_type": "Small_Object_Candidate",
                    "reason": "이미지 내 person 객체 크기가 작아 박스 정밀도 저하 가능",
                }
            )

        if is_partial_candidate(gt, img_w, img_h):
            rows.append(
                {
                    **base,
                    "issue_type": "Partial_or_Truncated_Candidate",
                    "reason": "person 박스가 이미지 경계에 가까워 상반신/부분 가림 사례일 가능성",
                }
            )

        if crowded_iou >= 0.10:
            rows.append(
                {
                    **base,
                    "issue_type": "Crowded_or_Overlapped_Person",
                    "reason": "다른 person 박스와 겹침이 있어 작업자 겹침 사례일 가능성",
                }
            )

    for pred_idx in unmatched_preds:
        pred = pred_boxes[pred_idx]
        rows.append(
            {
                "image": image_path.name,
                "gt_index": "",
                "pred_index": pred_idx,
                "iou": "0.0000",
                "gt_w": "",
                "gt_h": "",
                "gt_area_ratio": "",
                "pred_w": f"{pred.w:.2f}",
                "pred_h": f"{pred.h:.2f}",
                "width_ratio": "",
                "height_ratio": "",
                "pred_conf": f"{pred.conf:.4f}",
                "small_object": "",
                "partial_candidate": "",
                "crowded_candidate": "",
                "max_person_overlap_iou": "",
                "issue_type": "FP_person",
                "reason": "예측 person이 있지만 정답 person과 매칭되지 않음",
            }
        )

    if rows:
        save_annotated_image(
            image_path=image_path,
            output_path=output_img_dir / image_path.name,
            gt_boxes=gt_boxes,
            pred_boxes=pred_boxes,
            rows=rows,
        )

    return rows


def write_csv(rows: list[dict], output_csv: Path):
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "image",
        "issue_type",
        "reason",
        "gt_index",
        "pred_index",
        "iou",
        "gt_w",
        "gt_h",
        "gt_area_ratio",
        "pred_w",
        "pred_h",
        "width_ratio",
        "height_ratio",
        "pred_conf",
        "small_object",
        "partial_candidate",
        "crowded_candidate",
        "max_person_overlap_iou",
    ]

    with output_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(rows: list[dict], output_txt: Path):
    counts: dict[str, int] = {}

    for row in rows:
        issue_type = row["issue_type"]
        counts[issue_type] = counts.get(issue_type, 0) + 1

    output_txt.parent.mkdir(parents=True, exist_ok=True)

    with output_txt.open("w", encoding="utf-8") as f:
        f.write("# person error analysis summary\n\n")
        f.write(f"total_issue_rows: {len(rows)}\n\n")

        for issue_type, count in sorted(counts.items(), key=lambda x: x[0]):
            f.write(f"- {issue_type}: {count}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze person detection errors for PPE YOLO model."
    )

    parser.add_argument(
        "--model",
        type=Path,
        default=Path("runs/detect/train/weights/best.pt"),
        help="Path to trained YOLO model.",
    )
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=Path("data/merged/ppe_split/val/images"),
        help="Validation image directory.",
    )
    parser.add_argument(
        "--label-dir",
        type=Path,
        default=Path("data/merged/ppe_split/val/labels"),
        help="Validation label directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/person_error_analysis"),
        help="Output directory.",
    )
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--match-iou-thr", type=float, default=0.50)
    parser.add_argument("--loc-iou-thr", type=float, default=0.75)
    parser.add_argument("--wide-ratio", type=float, default=1.25)
    parser.add_argument("--narrow-ratio", type=float, default=0.80)
    parser.add_argument("--small-area-thr", type=float, default=0.02)

    args = parser.parse_args()

    if not args.model.exists():
        raise FileNotFoundError(f"Model not found: {args.model}")

    if not args.image_dir.exists():
        raise FileNotFoundError(f"Image directory not found: {args.image_dir}")

    if not args.label_dir.exists():
        raise FileNotFoundError(f"Label directory not found: {args.label_dir}")

    if args.output_dir.exists():
        shutil.rmtree(args.output_dir)

    output_img_dir = args.output_dir / "annotated"
    output_csv = args.output_dir / "person_error_report.csv"
    output_txt = args.output_dir / "summary.txt"

    image_paths = sorted(
        p for p in args.image_dir.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    )

    model = YOLO(str(args.model))

    all_rows: list[dict] = []

    for idx, image_path in enumerate(image_paths, start=1):
        print(f"[{idx}/{len(image_paths)}] {image_path.name}")

        rows = analyze_image(
            model=model,
            image_path=image_path,
            label_dir=args.label_dir,
            output_img_dir=output_img_dir,
            imgsz=args.imgsz,
            conf=args.conf,
            match_iou_thr=args.match_iou_thr,
            loc_iou_thr=args.loc_iou_thr,
            wide_ratio=args.wide_ratio,
            narrow_ratio=args.narrow_ratio,
            small_area_thr=args.small_area_thr,
        )

        all_rows.extend(rows)

    write_csv(all_rows, output_csv)
    write_summary(all_rows, output_txt)

    print("\n[DONE] person error analysis completed.")
    print(f"- CSV report: {output_csv}")
    print(f"- Summary: {output_txt}")
    print(f"- Annotated images: {output_img_dir}")


if __name__ == "__main__":
    main()