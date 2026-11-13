# W16 기본 비활성 자동판정 기록 경로

## 현재 실제 상태
자동판정 운영활성화는 하지 않았다. 기본 런타임/검증 adapter가 없으며 HTTP/API키/환경변수/그림자 요청으로 켤 수 없다. 검증된 미착용 confidence가 없는 현 기본 모델은 여전히 review_only다. 임계값이나 정확도 수치를 새로 정하지 않는다. 아래 코드는 검증된 정책·모델·전체사건 검증기를 추후 연결할 때 사용할 기록 단계이며 W16 완성/활성화를 뜻하지 않는다.

## 준비한 처리
- lifecycle 종료, 로컬 media 비식별 결과 게시, 원격 processor 결과 게시, clip 접수 완료 transaction에 준비 훅. 현재 모두 runtime 없이 호출하므로 DB 조회·변경 없이 disabled 반환.
- 내부 Runtime을 명시적으로 전달하는 테스트/향후 신뢰통합에서만 writer 실행. PreviewRequest는 그 자체로 정책승인이 아니며 검증adapter가 정책 적합성·모델출처·전체시간 증거·무결성을 독립확인해야 한다. adapter 구현은 아직 없다. ValidationProof는 클라이언트에서 역직렬화하는 형식이 아니다.
- standalone attempt는 READ COMMITTED(PG) 및 공통 쓰기잠금 취득 후 최신 상태를 다시 읽는다. 완료훅은 호출자의 기존 쓰기잠금/transaction을 사용.
- pending/미검토, review_only 제외, 범위·점수·보존/만료/병합·완료증거 점검. 연속사건 closed, 관측공백/미설명시간0, 모든등록카메라 manifest coverage 완료 필요. 이것만으로 분류정확도를 보증하지 않아 trusted adapter의 추가 검증이 필수.
- 실제 쓰기는 기존 validate_review를 거치며 review 추가와 event confirmed를 하나의transaction으로 처리. 동의·보관을 새로승인/연장/우회하지 않고 원본도 보존정책대로 유지.
- reviewer_id는NULL(사람으로 위장하지 않음), reason automatic_policy_confirmed, comment에 버전/정책/점수/근거fingerprint/검증reference JSON 저장. 비밀·파일경로는 포함하지 않는다. 이 reviewer_id와 reason은 시스템판정 표시에 사용할 수 있으나 신규 FE는 별도 담당이다.
- second_review_needed=1을 유지하여 기존 관리자 PUT 재검토를 열어 둔다. 관리자번복 시 기존 history/오탐최종집계 경로를 그대로 사용한다. 자동판정은 기존 human review를 절대덮어쓰지 않는다.
- 경쟁하던 수동 POST도 lock취득 후 중복review를 확인하도록 보완. 이미다른판정이들어온경우409→재조회→기존PUT 재검토. 조용한중복row 생성없음.

## 검증과 한계
writer 테스트의 전체사건 validator는 **합성fixture**이며 실제 모델성능검증이 아니다. disabled/잘못된proof/legacy/공백/중복·동시요청/사람판정우선/관리자번복/retention불변/실패rollback을 검증한다. 기존 통계갱신 semantics는 바꾸지 않으며 확정직후 모든 캐시를 자동 재생성하지 않는다.

운영 연결에는 승인된 정책을 읽는 신뢰adapter, 실제점수 출처·클립검증, 실패재시도/작업스케줄, 성능·부하평가, UI연결, 제어된배포가 필요하다. 현재 훅은 일부준비가 덜된순서에서도 안전하게 disabled이며, future runtime 연결시 transaction안에서 네트워크/비싼추론을 수행하면 안 된다. 미리계산한 검증결과의 fresh성/무결성을 동일 transaction에서 확인하는 adapter가 필요하다. 기존 PG 통과후 이 writer를추가했으므로 새 PG검증없이는 이전결과를전용하지 않는다.
