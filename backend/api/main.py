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
    get_candidate_event_by_id,
    get_candidate_events,
    get_review_by_event_id,
    insert_event_review,
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
def read_events(
    ppe_type: str | None = None,
    event_status: str | None = None,
    zone_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_confidence: float | None = None,
) -> dict:
    """
    후보 이벤트 목록 조회.

    Query Parameters:
        ppe_type:
            PPE 유형 필터.
        event_status:
            이벤트 상태 필터.
        zone_name:
            구역 이름 필터.
        date_from:
            조회 시작 날짜.
        date_to:
            조회 종료 날짜.
        min_confidence:
            최소 AI 신뢰도 필터.

    Returns:
        dict:
            조건에 맞는 후보 이벤트 목록 반환.
    """
    events = get_candidate_events(
        ppe_type=ppe_type,
        event_status=event_status,
        zone_name=zone_name,
        date_from=date_from,
        date_to=date_to,
        min_confidence=min_confidence,
    )

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

    updated_event = get_candidate_event_by_id(event_id)
    saved_review = get_review_by_event_id(event_id)

    return {
        "status": "ok",
        "event": updated_event,
        "review": saved_review,
    }