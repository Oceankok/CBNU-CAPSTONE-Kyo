# W12 / W14 프론트 인계: 백엔드 계약과 재현 증거

프론트 파일은 수정하지 않았다. 아래 테스트는 BE 계약을 검증할 뿐 W12/W14 해결을 의미하지 않는다.

## W12: 요약 창은 신규 유입 카운터가 아님

`GET /api/events/summary?recent_limit=50`은 전체 현재 건수(`by_status.pending`)와 최근 pending 최대50건(`recent_pending`)을 별도로 반환한다. 65건일 때 건수65, 목록50을 확인했다. 가장 최근 한 건을 confirmed로 바꾸면 건수64가 되고 기존51번째 사건이 목록에 들어온다. 이때 새 사건을 생성하지 않았는데도 목록 차집합은1이므로 차집합을 '신규1건'으로 표시하면 틀릴 수 있다.

현재 `usePendingEvents.ts`의 known 집합 무한 누적도 프론트 책임이다. 임의 eviction만 하면 과거 사건 재등장을 신규로 잘못 알릴 수 있으므로 단순 크기 제한을 정답으로 삼지 않는다. 선택지는 새 사건 개수 대신 '목록 변경' 알림을 사용하거나 서버측 등록 순서 cursor/보존/재시작 계약을 별도로 만드는 것이다. 현재 응답에 그런 cursor는 없다. 도착 수와 현재 pending 총수 차이도 같은 값이 아니다. exact count 정책을 임의로 정하지 않았다.

## W14: 추천 없음과 인증/서버 실패 구별

- GET `/api/recommendations?quarter=...`의 추천 없음: 404 + `Education recommendations not found`.
- 이 GET은 생성 함수를 호출하지 않는다.
- 미인증 GET은401이며 읽기/생성 함수 모두 호출하지 않는다.
- 새 FE는 위 '실제 없음'에 한해서 생성 여부를 처리하고401/403/5xx/네트워크 오류를 데이터 없음으로 바꾸면 안 된다.
- POST generate의 모든4xx가 '확정 위반 없음'은 아니다. 실제 생성 결과 없음은404 + `No confirmed events found for recommendation generation`이다.400은 유효성/생성 전제조건 오류일 수 있으므로 서버 detail을 구분한다.
- 재시도/오래된 분기 응답·unmount 뒤 응답은 현재 분기 편집기를 덮어쓰지 않도록 FE에서 검증한다.

검증 파일: `backend/tests/test_frontend_handoff_contracts.py`.
