from __future__ import annotations

import argparse
import csv
import shutil
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


PRIORITY_ISSUES = {
    "Localization_Error": 1,
    "Too_Wide_Box": 2,
    "Too_Narrow_Box": 3,
    "Partial_or_Truncated_Candidate": 4,
    "Crowded_or_Overlapped_Person": 5,
    "FN_person": 6,
    "FP_person": 7,
    "Small_Object_Candidate": 8,
}


@dataclass
class ReviewTarget:
    image: str
    issue_count: int
    issue_types: str
    main_issue: str
    max_severity: float
    needs_review_reason: str


def safe_float(value, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def calculate_row_severity(row: pd.Series) -> float:
    issue_type = str(row.get("issue_type", ""))

    iou = safe_float(row.get("iou"), 0.0)
    width_ratio = safe_float(row.get("width_ratio"), 1.0)
    height_ratio = safe_float(row.get("height_ratio"), 1.0)
    overlap_iou = safe_float(row.get("max_person_overlap_iou"), 0.0)

    if issue_type == "Localization_Error":
        return 1.0 - iou

    if issue_type == "Too_Wide_Box":
        return max(width_ratio, height_ratio) - 1.0

    if issue_type == "Too_Narrow_Box":
        return 1.0 - min(width_ratio, height_ratio)

    if issue_type == "Crowded_or_Overlapped_Person":
        return overlap_iou

    if issue_type == "FN_person":
        return 1.0

    if issue_type == "FP_person":
        return 0.8

    if issue_type == "Partial_or_Truncated_Candidate":
        return 0.6

    if issue_type == "Small_Object_Candidate":
        area_ratio = safe_float(row.get("gt_area_ratio"), 0.0)
        return max(0.0, 0.02 - area_ratio)

    return 0.0


def decide_main_issue(issue_types: list[str]) -> str:
    sorted_issues = sorted(
        issue_types,
        key=lambda issue: PRIORITY_ISSUES.get(issue, 999),
    )
    return sorted_issues[0] if sorted_issues else "Unknown"


def build_review_reason(main_issue: str) -> str:
    reasons = {
        "Localization_Error": "person 박스 위치 또는 크기 정밀도 확인 필요",
        "Too_Wide_Box": "person 예측 박스가 실제 신체 외곽보다 넓게 잡혔는지 확인 필요",
        "Too_Narrow_Box": "person 예측 박스가 실제 신체 일부만 포함했는지 확인 필요",
        "Partial_or_Truncated_Candidate": "상반신, 이미지 경계, 부분 가림 기준 확인 필요",
        "Crowded_or_Overlapped_Person": "여러 작업자 겹침 상황에서 개별 박스 분리 여부 확인 필요",
        "FN_person": "실제 person 라벨이 있는데 모델이 탐지하지 못한 원인 확인 필요",
        "FP_person": "person이 아닌 객체를 person으로 예측했는지 확인 필요",
        "Small_Object_Candidate": "작은 person 객체의 라벨 누락 또는 박스 정밀도 확인 필요",
    }
    return reasons.get(main_issue, "person 라벨 검토 필요")


def build_review_targets(df: pd.DataFrame, top_k: int | None = None) -> list[ReviewTarget]:
    targets: list[ReviewTarget] = []

    df = df.copy()
    df["row_severity"] = df.apply(calculate_row_severity, axis=1)

    grouped = df.groupby("image")

    for image_name, group in grouped:
        issue_types = sorted(set(str(v) for v in group["issue_type"].dropna()))
        main_issue = decide_main_issue(issue_types)

        targets.append(
            ReviewTarget(
                image=image_name,
                issue_count=len(group),
                issue_types=", ".join(issue_types),
                main_issue=main_issue,
                max_severity=float(group["row_severity"].max()),
                needs_review_reason=build_review_reason(main_issue),
            )
        )

    targets.sort(
        key=lambda t: (
            PRIORITY_ISSUES.get(t.main_issue, 999),
            -t.issue_count,
            -t.max_severity,
            t.image,
        )
    )

    if top_k is not None and top_k > 0:
        return targets[:top_k]

    return targets


def copy_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def write_review_csv(targets: list[ReviewTarget], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "image",
                "issue_count",
                "main_issue",
                "issue_types",
                "max_severity",
                "needs_review_reason",
                "review_result",
                "label_fix_needed",
                "memo",
            ],
        )
        writer.writeheader()

        for target in targets:
            writer.writerow(
                {
                    "image": target.image,
                    "issue_count": target.issue_count,
                    "main_issue": target.main_issue,
                    "issue_types": target.issue_types,
                    "max_severity": f"{target.max_severity:.4f}",
                    "needs_review_reason": target.needs_review_reason,
                    "review_result": "",
                    "label_fix_needed": "",
                    "memo": "",
                }
            )


def write_review_guide(output_path: Path) -> None:
    guide = """# person 라벨 검토 기준

## 목적

person 클래스의 낮은 mAP50-95 원인을 확인하고, 라벨 수정이 필요한 이미지를 선별한다.

## 최종 라벨 기준

- person 박스는 실제로 보이는 신체 외곽에 맞춘다.
- 가려진 신체 부위는 과도하게 추정하지 않는다.
- 상반신만 보이는 경우에도 보이는 신체 영역만 라벨링한다.
- 설비, 배경, 도구, 그림자는 person 박스에 포함하지 않는다.
- 여러 작업자가 겹쳐 있어도 가능한 경우 개별 person 박스로 분리한다.
- 사람인지 명확하지 않은 먼 객체는 보류하거나 라벨 기준에 따라 제외한다.

## 오류 유형별 검토 방법

### Localization_Error

객체는 찾았지만 예측 박스와 정답 박스가 정밀하게 맞지 않은 사례이다.

확인할 내용:
- 정답 라벨이 너무 넓거나 좁지 않은가
- 예측 박스가 사람 외곽을 벗어나 배경까지 포함하는가
- 상반신/부분 가림 라벨 기준이 일관적인가

### Too_Wide_Box

예측 person 박스가 정답보다 과도하게 넓은 사례이다.

확인할 내용:
- 정답 라벨이 너무 좁게 잡힌 것은 아닌가
- 예측 박스가 주변 설비나 다른 사람을 포함했는가
- 실제 사람 외곽 기준으로 라벨을 수정해야 하는가

### Too_Narrow_Box

예측 person 박스가 정답보다 과도하게 좁은 사례이다.

확인할 내용:
- 정답 라벨이 실제 보이는 신체보다 넓게 잡힌 것은 아닌가
- 예측이 상체나 일부 신체만 잡았는가
- 가려진 부분을 정답 라벨에서 과도하게 추정했는가

### Partial_or_Truncated_Candidate

상반신만 보이거나 이미지 경계에 걸친 작업자 후보이다.

확인할 내용:
- 보이는 신체 외곽 기준으로 라벨링되었는가
- 이미지 밖으로 나간 신체까지 추정하지 않았는가
- 상반신 작업자의 라벨 기준이 다른 이미지와 일관적인가

### Crowded_or_Overlapped_Person

작업자 여러 명이 겹친 후보이다.

확인할 내용:
- 여러 사람을 하나의 person 박스로 묶지 않았는가
- 개별 작업자 박스를 분리할 수 있는가
- 심한 가림으로 분리가 불가능한 경우 보류할 것인가

### FN_person

정답 person이 있지만 모델이 탐지하지 못한 사례이다.

확인할 내용:
- 라벨이 실제 사람인지 확인
- 너무 작거나 흐릿해 학습 대상에서 제외해야 하는지 확인
- 부분 가림이 심한지 확인

### FP_person

예측 person이 있지만 정답 person과 매칭되지 않은 사례이다.

확인할 내용:
- 실제 사람인데 라벨이 누락된 것은 아닌가
- 사람이 아닌 객체를 person으로 오탐했는가
- 라벨 추가가 필요한가, 모델 오탐으로 분류할 것인가

## 검토 결과 작성 기준

review_targets.csv의 `review_result`에는 다음 중 하나를 작성한다.

- label_fix: 라벨 수정 필요
- model_error: 라벨은 맞고 모델 예측 오류
- exclude_candidate: 학습 제외 후보
- ok: 문제 없음
- unclear: 판단 보류

`label_fix_needed`에는 yes/no를 작성한다.
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(guide, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build review set for person label correction based on person_error_report.csv"
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("outputs/person_error_analysis/person_error_report.csv"),
        help="person_error_report.csv path",
    )
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=Path("data/merged/ppe_split/val/images"),
        help="Validation raw image directory",
    )
    parser.add_argument(
        "--label-dir",
        type=Path,
        default=Path("data/merged/ppe_split/val/labels"),
        help="Validation label directory",
    )
    parser.add_argument(
        "--annotated-dir",
        type=Path,
        default=Path("outputs/person_error_analysis/annotated"),
        help="Annotated image directory generated by analyze_person_errors.py",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/person_label_review"),
        help="Output directory for review package",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=80,
        help="Number of images to include in review set. Use 0 for all.",
    )

    args = parser.parse_args()

    if not args.report.exists():
        raise FileNotFoundError(f"Report file not found: {args.report}")

    df = pd.read_csv(args.report)

    required_cols = {"image", "issue_type"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise ValueError(f"Missing required columns in report: {missing_cols}")

    top_k = None if args.top_k == 0 else args.top_k
    targets = build_review_targets(df, top_k=top_k)

    if args.output_dir.exists():
        shutil.rmtree(args.output_dir)

    raw_out = args.output_dir / "raw_images"
    label_out = args.output_dir / "labels"
    annotated_out = args.output_dir / "annotated_images"
    issue_out = args.output_dir / "by_issue_type"

    copied_raw = 0
    copied_label = 0
    copied_annotated = 0

    for target in targets:
        image_name = target.image
        label_name = f"{Path(image_name).stem}.txt"

        if copy_if_exists(args.image_dir / image_name, raw_out / image_name):
            copied_raw += 1

        if copy_if_exists(args.label_dir / label_name, label_out / label_name):
            copied_label += 1

        if copy_if_exists(args.annotated_dir / image_name, annotated_out / image_name):
            copied_annotated += 1
            copy_if_exists(
                args.annotated_dir / image_name,
                issue_out / target.main_issue / image_name,
            )

    write_review_csv(targets, args.output_dir / "review_targets.csv")
    write_review_guide(args.output_dir / "review_guide.md")

    print("[DONE] person label review set created.")
    print(f"- targets: {len(targets)}")
    print(f"- copied raw images: {copied_raw}")
    print(f"- copied labels: {copied_label}")
    print(f"- copied annotated images: {copied_annotated}")
    print(f"- output: {args.output_dir}")
    print()
    print("Next:")
    print(f"1. Open {args.output_dir / 'review_targets.csv'}")
    print(f"2. Review images in {args.output_dir / 'annotated_images'}")
    print("3. Fill review_result and label_fix_needed columns")
    print("4. Fix labels only for images marked as label_fix")


if __name__ == "__main__":
    main()