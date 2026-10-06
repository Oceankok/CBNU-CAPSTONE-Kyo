# 작업자 교육 조회 API (#111 1단계)

`GET /api/worker/education`을 추가합니다. 관리자용 `GET /api/recommendations`와 역할 제한은 유지합니다. 이번 범위는 기존 교육 주제의 안전한 조회이며 AI 자료 생성·번역·게시 UI는 포함하지 않습니다.

## 요청 및 응답

작업자 Bearer 인증을 사용합니다. 미인증·만료·비활성 계정은 401, 관리자 계정은 403을 반환합니다. 조회 구역은 서버가 로그인 계정의 현재 `zone_name`에서 결정합니다. 클라이언트의 구역·분기 입력으로 변경하지 않습니다.

해당 구역에 추천이 존재하는 가장 최근 분기를 사용합니다. 최신 분기는 `quarter` 기준이며 생성 시각 기준이 아닙니다. 다른 구역만 최신 분기의 추천을 가지고 있더라도 해당 구역의 최근 자료를 반환합니다. 이 기본 동작은 #111의 분기 기준 논의에 대한 구현 제안입니다.

```json
{
  "quarter": "2026-Q2",
  "zone_name": "프레스 구역",
  "items": [
    {
      "recommendation_id": "ER_example",
      "ppe_type": "helmet",
      "zone_name": "프레스 구역",
      "education_topic": "안전모 착용 기준 교육",
      "material": null
    }
  ],
  "empty_reason": null
}
```

순서는 기존 추천 순위와 ID 기준으로 고정합니다. 응답 필드는 허용 목록으로 선택하며 점수·위반 건수·집계 근거를 반환하지 않습니다. 자료 본문은 아직 생성하지 않으므로 `material=null`로 제공합니다.

구역이 미지정이면 200과 `quarter=null`, `zone_name=null`, `items=[]`, `empty_reason=zone_unassigned`를 반환합니다. 담당 구역의 추천이 없으면 `quarter=null`, 해당 `zone_name`, `items=[]`, `empty_reason=recommendations_unavailable`을 반환합니다. 전체 구역 추천으로 대체하지 않습니다.

## 프론트 연동

작업자 교육 카드에서 이 API를 호출하고 응답의 실제 분기를 표시합니다. 구역 미지정은 담당 구역 지정 안내, 추천 없음은 자료 준비 안내를 표시합니다. `material=null`일 때 본문이나 번역된 자료가 있는 것으로 표시하지 않습니다. 화면 변경은 프론트 담당 PR로 진행합니다.

## 검증

격리 DB와 합성 추천 데이터로 인증·역할, 구역 범위 제한, 최신 구역 분기 선택, 민감 필드 제외 및 빈 목록을 검증하는 테스트를 추가합니다. 테스트 실행은 승인을 받은 후 진행합니다.

```powershell
python -m unittest backend.tests.test_worker_education -v
```
