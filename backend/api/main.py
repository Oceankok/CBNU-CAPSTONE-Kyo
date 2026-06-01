"""
main.py

PPE 분석 시스템 초기 FastAPI 서버 파일.

현재 단계에서는 DB에 저장된 후보 이벤트를 조회하고,
담당자 검토 결과를 저장하는 최소 API 제공.
"""

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.db.event_repository import (
    get_all_candidate_events,
    get_candidate_event_by_id,
    get_review_by_event_id,
    insert_event_review,
    update_event_review,
    get_quarterly_stats,
    get_education_recommendations,
    generate_quarterly_stats,
    generate_education_recommendations,
    get_broadcast_settings,
    save_broadcast_settings,
)


app = FastAPI(
    title="PPE Analysis API",
    description="PPE 후보 이벤트 조회 및 담당자 검토 결과 저장 API",
    version="0.1.0",
)

# Allow the Vite dev server to call the API without CORS errors
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated event media files
_STORAGE_DIR = Path(__file__).resolve().parents[2] / "storage"
_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

app.mount(
    "/storage",
    StaticFiles(directory=str(_STORAGE_DIR)),
    name="storage",
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


class BroadcastTemplate(BaseModel):
    """
    경고 방송 메시지 템플릿.
    """

    ppe_type: str = Field(..., example="helmet")
    zone_name: str = Field(default="", example="프레스 구역")
    language: str = Field(..., example="ko")
    message: str = Field(
        ...,
        example="해당 작업 구역의 작업자는 안전모 착용 상태를 확인해 주세요.",
    )


class BroadcastSettingsRequest(BaseModel):
    """
    경고 방송 설정 저장 요청 모델.
    """

    enabled: bool = Field(default=True, example=True)
    default_language: str = Field(default="ko", example="ko")
    cooldown_sec: int = Field(default=30, example=30)
    templates: list[BroadcastTemplate] = Field(default_factory=list)


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


@app.put("/api/events/{event_id}/review")
def update_existing_event_review(event_id: str, request: ReviewRequest) -> dict:
    """
    보류 또는 2차 검토 대상 이벤트의 기존 검토 결과를 갱신함.

    Args:
        event_id (str):
            재검토 대상 후보 이벤트 ID.
        request (ReviewRequest):
            재검토 결과 요청 데이터.

    Returns:
        dict:
            갱신된 검토 결과와 후보 이벤트 정보.
            false_positive인 경우 삭제 처리 결과 반환.
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

    if existing_review is None:
        raise HTTPException(status_code=409, detail="Review does not exist")

    is_hold_event = event["event_status"] == "hold"
    needs_second_review = existing_review["second_review_needed"] == 1

    if not is_hold_event and not needs_second_review:
        raise HTTPException(
            status_code=409,
            detail="Event is not eligible for re-review",
        )

    confirmed_violation = 1 if request.review_result == "confirmed" else 0

    review = {
        "review_id": existing_review["review_id"],
        "event_id": event_id,
        "reviewer_id": request.reviewer_id,
        "review_result": request.review_result,
        "review_reason_code": request.review_reason_code,
        "review_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "review_comment": request.review_comment,
        "confirmed_violation": confirmed_violation,
        "second_review_needed": 1 if request.second_review_needed else 0,
    }

    try:
        update_event_review(review)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if request.review_result == "false_positive":
        return {
            "status": "ok",
            "message": "False positive event deleted after re-review",
            "event_id": event_id,
            "review_result": request.review_result,
        }

    updated_event = get_candidate_event_by_id(event_id)
    updated_review = get_review_by_event_id(event_id)

    return {
        "status": "ok",
        "message": "Review updated successfully",
        "event": updated_event,
        "review": updated_review,
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


@app.post("/api/stats/generate")
def create_quarterly_stats(quarter: str = "2026-Q2") -> dict:
    """
    후보 이벤트와 검토 결과를 기반으로 분기별 통계를 생성함.

    Args:
        quarter (str):
            생성할 분기. 예: 2026-Q2

    Returns:
        dict:
            생성된 분기별 통계 데이터.
    """
    try:
        return generate_quarterly_stats(quarter)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@app.post("/api/recommendations/generate")
def create_education_recommendations(quarter: str = "2026-Q2") -> dict:
    """
    확정 위반 통계를 기반으로 교육 추천 데이터를 생성함.

    Args:
        quarter (str):
            생성할 분기. 예: 2026-Q2

    Returns:
        dict:
            생성된 교육 추천 데이터.
    """
    try:
        recommendations = generate_education_recommendations(quarter)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if len(recommendations["items"]) == 0:
        raise HTTPException(
            status_code=404,
            detail="No confirmed events found for recommendation generation",
        )

    return recommendations


@app.get("/api/broadcast/settings")
def read_broadcast_settings() -> dict:
    """
    경고 방송 설정 조회.

    Returns:
        dict:
            경고 방송 사용 여부, 기본 언어, cooldown, 메시지 템플릿 목록.
    """
    return get_broadcast_settings()


@app.put("/api/broadcast/settings")
def update_broadcast_settings(request: BroadcastSettingsRequest) -> dict:
    """
    경고 방송 설정 저장.

    Args:
        request (BroadcastSettingsRequest):
            경고 방송 설정 요청 데이터.

    Returns:
        dict:
            저장된 경고 방송 설정.
    """
    return save_broadcast_settings(request.model_dump())