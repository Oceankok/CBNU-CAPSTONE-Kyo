# W16 자동판정 안전 선행: 읽기전용 임계값 진단

이 API는 자동확정이나 영속 그림자 모드가 아니다. 사건·검토·통계·교육·보관 상태를 바꾸지 않는다. `automatic_confirmation_enabled`와 각 결과의 `automatic_confirmation_allowed`는 항상 false다. 기존 수신/판정/방송 흐름에 연결하지 않았다.

## 관리자 API
`POST /api/auto-decisions/preview`

```json
{"event_ids":["EVT_..."],"threshold":0.9,"model_versions":["explicit-model-version"],"ppe_types":["helmet"],"policy_label":"offline-trial-1"}
```

예시의0.9는 권고/기본값이 아니다. threshold(0~1), model_versions, ppe_types, policy_label은 모두 필수이며 임의 기본값이 없다. 최대20사건을 한 읽기 snapshot에서 검사한다. 중복/빈ID/불명확한 extra 필드는422, 없는사건은404, 미로그인401, 비관리자403.

응답 mode=read_only_preview, persisted=false, 요청한 policy와 items를 반환한다.
- event_id/model_version/ppe_type/observed_confidence/event_status/collection/evaluated_at
- structural_eligible: 현재 구현한 구조적 제외조건을 모두 통과했는지
- threshold_candidate: 구조조건을 통과하고 명시 임계값 이상인지
- blocking_reasons: review_only, submission_provenance_missing, verified_confidence_missing, already_reviewed_or_not_pending, model_out_of_scope, ppe_out_of_scope, merged_event_requires_review, media_deleted_or_expired, observation_interval_unverified, primary_redacted_video_missing, evidence_incomplete, requested_evidence_missing, continuous_event_not_closed, continuous_evidence_incomplete 등
- evidence_fingerprint: 이번 진단 입력의 SHA256. 영속 revision/권한토큰/후속 쓰기 허용증명이 아님. 파일 존재 여부·구조 제외사유·정책을 포함하지만 파일 바이트 무결성 검사나 캐시/동시성 토큰으로 사용하면 안 된다.

## 보수적 제약
- 추론이 검증 미착용 점수 없이 제출한 review_only, mode provenance 없는 과거사건은 제외. 그 숫자0은 실제 신뢰도로 표시하지 않는다.
- 연속사건은 종료와 모든 camera/segment 완료 검사를 재사용한다. 미수신/실패/미비식별/삭제/보관기한만료/파일누락은 제외.
- 사람 검토가 있거나 pending이 아니면 후보로 만들지 않는다. 수동 병합 원본/대표도 제외.
- 현재 legacy 일반사건은 유효한 timezone 포함 관측종료시각과 전체 요청/미디어 상태를 검사한다. 이 경로에는 연속사건의 frame/duration proof가 없으므로 '연속수집 전체검증과 동등'하다는 뜻이 아니다.
- model_versions는 사용자가 실험을 위해 지정한 **검색 범위**다. 모델 정확도 검증·운영 allowlist 등록·시연 활성화 승인으로 해석하면 안 된다.
- ROI 경계/정책변경/새카메라/새모델/상충라벨/겹친사람/의심중복 등 특이사항 검사 전체가 아직 연결되지 않았다. 따라서 threshold_candidate=true를 화면에서 '자동확정' 또는 '확정 가능'으로 표현하지 않는다.
- 점수는 보정 확률이 아니다. 후처리 decision score는 model confidence와 별도이며 이 API는 미정 후처리점수를 생성하지 않는다.
- 응답 뒤 증거상태가 바뀔 수 있다. 실제 자동 쓰기를 구현할 때 동일 transaction에서 다시 검사하고 사람판정/증거세대/정책버전 비교 및 이력을 보존해야 한다.

## 다음 단계
영속 shadow 기록과 관리자 판단 대조집계, 모델별 검증근거/임계값 승인, 정책 및 증거 revision, 자동/사람 판정 이력, 통계 재계산, 부분증거·특이사항 정책을 연결해야 W16을 완료할 수 있다. 사용자 확정 A(사건 종료후 전체구간)와 정확도 검증전OFF는 바꾸지 않는다.
