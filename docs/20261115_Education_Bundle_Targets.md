# G1: 통합 교육 전체 대상과 상위 추천 분리

## 요구사항 근거

- 최초 `분기별 PPE 데이터 분석 및 교육 추천 알고리즘 설계.docx`는 교육 우선순위 산정을 설명하며 전체 대상의 3개 상한을 정의하지 않습니다.
- `20261113_Recommendation_Score_V2.md`와 `20261113_Education_Within_Zone_PPE_Share.md`는 당시 상위 3개 선정 정책을 유지했습니다. 과거 구현은 실제로 3개만 저장했습니다. 이를 처음부터 표시 전용 정책이었다고 해석하지 않습니다.
- [#239](https://github.com/Oceankok/CBNU-CAPSTONE-Kyo/issues/239)는 통합 교육을 별도 제안으로 시작했으며 [#240](https://github.com/Oceankok/CBNU-CAPSTONE-Kyo/issues/240)은 이를 별도 범위로 제외했습니다.
- 이후 [#244](https://github.com/Oceankok/CBNU-CAPSTONE-Kyo/issues/244), `multizone-education-api.md`, [PR #248](https://github.com/Oceankok/CBNU-CAPSTONE-Kyo/pull/248)은 같은 분기/PPE의 서로 다른 2–20구역을 명시했습니다. [PR #250](https://github.com/Oceankok/CBNU-CAPSTONE-Kyo/pull/250)은 해당 선택 UI를 연결했습니다.
- 이 후속 계약에 맞춰 전체 유효 대상을 저장하고 상위 3개 조회를 분리합니다. 통합 선택을 2–3개로 축소하지 않습니다.

## API와 저장 계약

추천 명시 생성과 통계 명시 갱신은 해당 분기의 모든 확정 사건 구역/PPE 그룹을 같은 transaction에서 점수순으로 저장합니다. 점수 v2 식, 반올림 전 정렬, 동률 기준, semantic ID, 분기 경계는 유지합니다. 새 schema나 시작 시 데이터 이관은 없습니다.

기존 `GET /api/recommendations?quarter=...`와 `POST /api/recommendations/generate?quarter=...`의 `items`는 상위 3개입니다. 관리자 강조 카드와 기존 단일 구역 편집 선택은 이 응답을 계속 사용할 수 있습니다.

새 관리자 `GET /api/education/bundles/targets?quarter=2026-Q1`은 `{quarter, generated_at, items}`를 반환합니다. 각 항목의 필드는 기존 추천 응답과 같습니다. 전체 순위 오름차순이며 통합 선택이 허용하지 않는 빈 구역명은 제외합니다. 따라서 순위에 빈 번호가 있을 수 있습니다. PPE가 달라도 전체 목록에 포함되며 선택 시 동일 PPE 조건을 유지해야 합니다. 전체 목록은 20개로 자르지 않습니다. 20개는 한 통합자료의 선택 상한입니다.

미생성·빈 캐시는 200과 `items: []`를 반환합니다. 인증/권한 오류는 401/403, 잘못된 분기는 400입니다. 조회는 생성이나 보충 저장을 하지 않습니다. 기존에 3개만 저장된 분기는 관리자가 기존 추천 생성 API로 명시 갱신한 뒤 전체 대상을 다시 읽어야 합니다. 기존 캐시의 완전성을 나타내는 별도 플래그는 없습니다. UI는 기존 캐시가 일부일 수 있음을 안내하고 명시적 갱신 수단을 제공해야 합니다. 추천 GET 오류나 화면 진입만으로 자동 생성하지 않습니다.

프론트는 상위 추천과 통합 선택 대상을 별도 state로 관리해야 합니다. 분기 변경, 추천 생성 또는 통계 갱신 후 전체 대상을 다시 읽습니다. 목록 간 요청 시점이 다를 수 있으므로 작성/게시 보호는 기존 preview snapshot과 409 재선택 계약을 사용합니다. 선택을 바꾸면 이전 snapshot을 재사용하지 않습니다. 관리자 응답을 작업자 화면에 전달하지 않습니다.

## 자료 보존과 작업자 영향

순위가 4위 이하로 내려가도 실제 구역/PPE/주제가 유효하면 추천 ID와 대상이 유지됩니다. 이전 단일 자료의 조회·편집 및 게시자료/초안 기록은 보존됩니다. 통합 snapshot에서 제외된 순위만 바뀌면 기존 초안을 게시할 수 있습니다. 건수·점수·주제 등 snapshot이 바뀌면 통합 초안의 게시가 409이며 초안은 그대로 남습니다. 게시된 내용은 통계 변화만으로 다시 쓰지 않습니다.

작업자는 기존대로 현재 배정 구역, 구역별 최신 추천 분기 및 동일 주제 조건으로 조회합니다. 전체 저장의 의도된 효과로 상위 3개 밖 구역에도 추천 카드가 나타날 수 있으며, 검토·게시한 단일 자료가 없으면 `material: null`입니다. 구역의 다음 분기 대상이 4위 이하라도 최신 분기는 그 분기로 바뀌며 이전 분기 자료는 숨깁니다. 순위 하락만으로 대상이 사라지지는 않습니다. 실제 대상 제거·주제 변경·배정 해제·게시 취소에 따른 숨김과 관리자/작업자 권한은 유지합니다. 통합 본문 전체 공유와 `assigned_zones` 제한도 동일합니다.

## 회귀 범위

`backend.tests.test_education_bundle_targets`는 실제 확정 사건 fixture → 추천/통계 생성 → 전체 대상 GET → preview/작성/게시/작업자 조회를 연결합니다. 4·20개 구역, 21개 선택 거절, 여러 PPE 때문에 top3에서 제외된 동일 PPE 4개 선택, API/저장/preview 대상 일치, 빈 구역 제외, 순위 변동 시 ID·단일/통합 게시자료·초안 보존, 통계 갱신 뒤 stale snapshot 거절, 실제 대상/주제 제거 및 최신 분기·배정 정책, 읽기 전용 GET과 권한을 확인합니다.

격리 검사는 운영 DB 변경이나 실제 브라우저/카메라 실증을 뜻하지 않습니다. FE 연결과 운영 기존 캐시의 명시 갱신은 별도 적용 단계입니다.

## 격리 검사 기록 (2026-11-15)

기준 main `ac9d254774ae5c1d0ebdc9019f249b7ddceb68e8`에 이 변경을 적용해 다음 집중 회귀를 실행했습니다.

```powershell
python -W ignore -X utf8 -m unittest backend.tests.test_education_bundle_targets backend.tests.test_recommendation_semantic_identity backend.tests.test_recommendation_score_v2 backend.tests.test_education_zone_share backend.tests.test_education_stats_refresh backend.tests.test_education_target_scope backend.tests.test_education_bundles backend.tests.test_worker_education backend.tests.test_openapi_contract -v
```

- `PPE_TEST_POSTGRES_URL=''`: 임시 SQLite DB, 63개 중 62개 통과·PostgreSQL 전용 1개 skip, 21.409초.
- `PPE_TEST_POSTGRES_URL=postgresql://kyo_test@127.0.0.1:55437/kyo_test_education_targets_20261115`: 조율된 임시 PostgreSQL의 전용 DB/UUID schema, 63개 모두 통과, 53.874초. 동시 게시 및 추천 갱신 대기자의 오래된 snapshot 거절 포함입니다.
- 신규 전체 대상 회귀 8개는 두 DB에서 모두 통과했습니다. `git diff --check`와 변경 Python 파일 compile 검사도 통과했습니다.
- 원시 로그는 이 작업 worktree의 무시되는 `.private_media/g1-validation/sqlite-tests.log`, `postgres-tests.log`에 보존합니다. 테스트 후 전용 DB의 `test_*` schema 잔여 0개를 확인했습니다.
- Windows sandbox 내부 첫 실행은 TestClient의 asyncio loopback socket 생성에서 중단됐습니다. 권한 확장 후 같은 격리 검사를 실행했으며 위 수치는 그 정상 실행 결과입니다.
- 이 기록은 백엔드 전체 검사, 브라우저 통합, 운영 캐시 갱신, 운영 DB/실카메라/방송 검증을 포함하지 않습니다. 프론트 연결은 별도 담당 PR과 결합해 검증해야 합니다.
