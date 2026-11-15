# 연속 이벤트 규칙 기반 자동 확정

## 상태와 적용 범위

`continuous_rules_v1`은 기존 연속 이벤트의 관측 기록을 사용하는 별도 판정 방식입니다. 기본 설정은 계속 `off`이며 기존 설정에는 `decision_method=validated_model`이 적용됩니다. 기존 모델 점수 경로의 검증 어댑터 차단은 그대로 유지됩니다. 운영 설정 변경·배포·실영상 정확도 검증은 이 구현에 포함되지 않습니다.

규칙은 생산자가 보고한 미착용 의심 상태의 지속성과 증거 영상의 완결성을 검사합니다. 사람 탐지 점수나 `1 - 착용 점수`로 미착용 확률을 만들지 않습니다. 지속 시간·관측 횟수·영상 완결성만으로 실제 위반이 입증되는 것은 아닙니다. 가림, 원거리, 낮은 조명 등에서 발생하는 기존 탐지기의 오탐은 실영상 수동 판정과 비교해야 합니다.

권장 적용 순서는 이벤트 생성 수정 → 고정된 카메라·모델에서 `shadow` 기록과 실제 영상 수동 판정 비교 → 결과 보고 → 사용자 활성화 결정입니다. 아래 예제 임계값은 사용법 설명이며 검증된 운영 기준이 아닙니다.

## 설정 API

기존 관리자 전용 `GET/PUT /api/auto-decisions/settings`, `/settings/history`, `POST /settings/preview`를 사용합니다. 정책 JSON에 필드를 추가하므로 DB 스키마 변경은 없습니다.

```json
{
  "expected_version": 0,
  "mode": "shadow",
  "decision_method": "continuous_rules_v1",
  "rule_criteria": {
    "minimum_observed_seconds": 5.0,
    "minimum_frame_samples": 10
  },
  "threshold": 0.9,
  "model_versions": ["EXACT_MODEL_VERSION"],
  "ppe_types": ["helmet", "vest"],
  "camera_ids": ["CAM_01"],
  "zone_names": [],
  "validation_reference": null,
  "reason": "고정된 장비에서 실제 영상과 규칙 판정의 그림자 비교"
}
```

- `expected_version`은 GET 결과를 사용합니다. 설정 충돌은 기존처럼 409입니다.
- 모델·카메라·PPE는 실제 식별자를 명시합니다. 구역은 사건 생성 시점의 스냅샷을 사용합니다.
- `rule_criteria`에는 두 값을 명시해야 합니다. 지속 시간은 3~3600초, 관측 횟수는 2~100000회입니다. bool, NaN, 범위 밖 값은 거부합니다. 최소 허용 범위 자체가 정확도 검증 기준을 뜻하지 않습니다.
- 규칙 방식에서 `threshold`는 호환성 필드이며 판정에 사용하지 않습니다. `score_semantics=rule_conditions_not_a_probability`, `rule_evaluation.score=null`입니다.
- `enforce`를 저장하려면 `validation_reference`도 필요합니다. 이는 관리자가 남긴 시험 기록의 식별자이며 서버가 보고서 진위나 정확도를 인증했다는 뜻은 아닙니다. 이 변경에서는 운영 enforce를 설정하지 않습니다.
- `off`는 확정 및 그림자 기록을 쓰지 않습니다. `shadow`는 판정만 기록하고 `event_status`를 변경하지 않습니다. 정책 변경으로 과거 사건을 일괄 처리하지 않습니다.

## 판정 조건

아래 조건을 모두 만족해야 규칙 적격입니다.

1. 적용 범위에 포함된 안전모 또는 조끼 사건이며, 추적 ID와 사람 탐지·PPE 미탐지 기록이 있습니다.
2. 사건이 `closed`이고 전체 관측 시간과 관측 횟수가 지정한 기준 이상입니다.
3. 관측 공백 횟수·시간이 0이며 관측하지 않은 구간이 없습니다. 부동소수점 누적 오차는 최대 1마이크로초만 허용합니다.
4. 종료 사유가 `ppe_present`, `track_departed`, `primary_source_ended` 중 하나입니다. 관측 단절·정책 변경·불명확한 종료는 수동 검토에 남깁니다.
5. 모든 필수 카메라의 수집 보고와 요청 영상이 완결됐습니다. 마스킹 완료, 파일 존재, 처리 세대와 디코딩 검증, 전체 영상 구간 조건도 기존 finalization 검사를 통과해야 합니다.
6. 기존 검토가 없고 상태가 pending이며 병합·현재 중복 의심·미해제된 저장 의심 관계가 없습니다. 삭제·만료 영상은 제외합니다.

기존 단발 이벤트는 연속 관측 근거가 없어 제외합니다. 참조 카메라의 완결 영상은 추가 증거이지 카메라 간 동일인 식별이나 PPE 판정 융합 결과가 아닙니다. 새 추론 모델이나 프레임별 분류기는 추가하지 않았습니다.

기본 `run_multi → run_server`의 legacy 후보가 첫 qualification 구간의 시작·종료 시각을 유지하더라도 이를 전체 사건 구간으로 해석하지 않습니다. lifecycle/collection이 없는 후보는 `closed_continuous_event_required`, `continuous_observation_incomplete`, `whole_event_evidence_incomplete`, `abnormal_event_close_requires_review`, `minimum_observed_seconds_not_met`로 제외됩니다. 영상·신뢰도·검토 상태에 따라 다른 제외 사유도 추가될 수 있습니다. 기본 경로의 후보 생성 수정만으로 규칙 경로가 적용되는 것은 아니며, 실제 규칙 그림자 비교에는 명시적인 continuous 수집과 전체 관측 증거가 필요합니다. 종료 시각 확장이나 관측값 추정으로 조건을 충족시키지 않습니다.

## 실행과 이력

기존 사건 종료·영상 수신·마스킹 완료 hook에서 저장된 정책을 다시 읽어 평가합니다. 마지막 카메라 수집 보고가 늦게 도착한 경우에도 규칙을 실행합니다. 해당 보고는 먼저 커밋하고 별도 트랜잭션에서 판정하므로 판정 실패가 접수된 보고를 취소하지 않습니다. 동일 보고 재전송으로 실패한 판정을 재시도할 수 있습니다.

설정과 검토는 기존 공통 쓰기 잠금 아래에서 처리됩니다. 자동 확정은 사건 상태와 검토 행을 함께 저장하며, 중복 호출은 검토 행을 추가하지 않습니다. 사람이 먼저 검토한 사건은 덮어쓰지 않습니다.

검토에는 `reviewer_id=null`, `review_reason_code=automatic_policy_confirmed`, `second_review_needed=1`을 기록합니다. `review_comment` JSON의 `source=automatic_continuous_rules_v1`, 설정 버전·설정자·전체 정책·모델 버전·증거 fingerprint·실제 관측값·규칙 조건·한계로 자동 판정의 출처를 구분합니다.

원본 `review_only=true`와 공개 `ai_confidence=null`은 유지합니다. `review_only`는 원래 추론 근거에 검증된 미착용 점수가 없다는 뜻이며 최종 상태는 `event_status`로 판단해야 합니다. 기존 `PUT /api/events/{id}/review`로 번복하면 자동 판정이 검토 이력에 보존됩니다. 확정 건수는 기존 통계 생성 경로에서 집계하고, 오탐 번복은 기존 재집계 경로를 사용합니다. 통계·교육 자료 생성이나 보관 동의는 자동으로 실행하지 않습니다.

## 프론트 연결 계약

프론트 파일은 이 변경 범위에 포함하지 않습니다. 기존 설정 화면에는 방식 선택과 규칙 임계값 입력 연결이 필요합니다.

- GET으로 받은 `decision_method`, `rule_criteria`를 편집 상태와 PUT/preview 요청에 보존합니다. 규칙 방식에서 점수 임계값 대신 지속 시간·관측 횟수를 표시합니다.
- 이미 규칙 방식이 저장된 상태에서 구형 클라이언트가 `decision_method`를 생략한 채 shadow/enforce를 저장하면 422 `explicit_decision_method_required`로 거부합니다. OFF 전환은 허용합니다.
- `rule_evaluation.eligible`, `blocking_reasons`, 실제 관측값을 표시합니다. 미리보기와 그림자 결과의 `automatic_confirmation_allowed`는 계속 false이며, 적격 여부가 실제 확정됐다는 뜻은 아닙니다.
- 연속 이벤트 응답의 `auto_decision_enabled`는 상수 false 대신 현재 저장된 정책의 활성 상태와 사건 적용 범위를 반영합니다. 이 값 역시 사건 적격·확정 여부를 뜻하지 않습니다.
- 자동 판정의 JSON 근거를 확률처럼 표시하지 않고, 원본 검토 전용 표기와 최종 확정 상태를 구분합니다.

## 검증 경계

`backend.tests.test_auto_decision_rules`는 합성 영상 파일과 실제 PostgreSQL 테스트 스키마로 설정, 도착 순서, 관측·영상 제외 조건, OFF/shadow, 중복 실행, 실패 롤백, 관리자 우선권·번복·확정 건수 재집계를 검사합니다. 실제 모델 정확도와 영상 수동 비교는 별도 실증 대기입니다.
