"""
main.py

PPE 분석 시스템 초기 FastAPI 서버 파일.

현재 단계에서는 DB에 저장된 후보 이벤트를 조회하고,
담당자 검토 결과를 저장하는 최소 API 제공.
"""

from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.db.event_repository import (
    get_all_candidate_events,
    get_candidate_event_by_id,
    get_review_by_event_id,
    insert_event_review,
    get_quarterly_stats,
    get_education_recommendations,
)


app = FastAPI(
    title="PPE Analysis API",
    description="PPE 후보 이벤트 조회 및 담당자 검토 결과 저장 API",
    version="0.1.0",
)


class ReviewRequest(BaseModel):
    """
    담당자 검토 결과 저장 요청 모델.

    review_result 값:
        - confirmed: 확정 위반
        - false_positive: 오탐
        - hold: 판단 보류
    """

    reviewer_id: str = Field(..., example="admin01")
    review_result: str = Field(..., example="confirmed")
    review_reason_code: str = Field(..., example="confirmed_no_helmet")
    review_comment: str = Field(default="", example="실제 안전모 미착용")
    second_review_needed: bool = Field(default=False, example=False)


@app.get("/")
def read_root() -> dict[str, str]:
    """
    API 서버 상태 확인.
    """
    return {"message": "PPE Analysis API is running"}


@app.get("/api/events")
def read_events() -> dict:
    """
    후보 이벤트 전체 목록 조회.
    """
    events = get_all_candidate_events()

    return {
        "total": len(events),
        "items": events,
    }


@app.get("/api/events/{event_id}")
def read_event(event_id: str) -> dict:
    """
    후보 이벤트 단건 조회.

    Args:
        event_id (str):
            조회할 후보 이벤트 ID.

    Returns:
        dict:
            후보 이벤트 정보와 검토 결과 반환.
    """
    event = get_candidate_event_by_id(event_id)

    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    review = get_review_by_event_id(event_id)

    return {
        "event": event,
        "review": review,
    }


@app.post("/api/events/{event_id}/review")
def create_event_review(event_id: str, request: ReviewRequest) -> dict:
    """
    후보 이벤트 담당자 검토 결과 저장.

    Args:
        event_id (str):
            검토 대상 후보 이벤트 ID.
        request (ReviewRequest):
            담당자 검토 결과 요청 데이터.

    Returns:
        dict:
            저장된 검토 결과와 갱신된 후보 이벤트 반환.
    """
    event = get_candidate_event_by_id(event_id)

    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    if request.review_result not in {"confirmed", "false_positive", "hold"}:
        raise HTTPException(
            status_code=400,
            detail="review_result must be one of: confirmed, false_positive, hold",
        )

    existing_review = get_review_by_event_id(event_id)
    if existing_review is not None:
        raise HTTPException(status_code=409, detail="Review already exists")

    review_id = f"RV_{event_id.replace('EVT_', '')}"
    confirmed_violation = 1 if request.review_result == "confirmed" else 0

    review = {
        "review_id": review_id,
        "event_id": event_id,
        "reviewer_id": request.reviewer_id,
        "review_result": request.review_result,
        "review_reason_code": request.review_reason_code,
        "review_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "review_comment": request.review_comment,
        "confirmed_violation": confirmed_violation,
        "second_review_needed": 1 if request.second_review_needed else 0,
    }

    insert_event_review(review)

    saved_review = get_review_by_event_id(event_id)

    if request.review_result == "false_positive":
        return {
            "status": "ok",
            "message": "False positive event deleted",
            "event_id": event_id,
            "review_result": request.review_result,
        }

    updated_event = get_candidate_event_by_id(event_id)

    return {
        "status": "ok",
        "message": "Review saved successfully",
        "event": updated_event,
        "review": saved_review,
    }

@app.get("/api/stats")
def read_quarterly_stats(quarter: str = "2026-Q2") -> dict:
    """
    분기별 통계 조회.

    Args:
        quarter (str):
            조회할 분기. 예: 2026-Q2

    Returns:
        dict:
            분기별 요약 통계, PPE 유형별 통계, 구역별 통계, 추이 데이터.
    """
    stats = get_quarterly_stats(quarter)

    if stats is None:
        raise HTTPException(status_code=404, detail="Quarterly stats not found")

    return stats


@app.get("/api/recommendations")
def read_education_recommendations(quarter: str = "2026-Q2") -> dict:
    """
    분기별 교육 추천 조회.

    Args:
        quarter (str):
            조회할 분기. 예: 2026-Q2

    Returns:
        dict:
            교육 추천 목록과 점수 산정 근거.
    """
    recommendations = get_education_recommendations(quarter)

    if len(recommendations["items"]) == 0:
        raise HTTPException(
            status_code=404,
            detail="Education recommendations not found",
        )

    return recommendations