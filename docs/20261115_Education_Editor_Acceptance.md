# 교육 편집 충돌 및 통합 대상 선택 검증

2026년 11월 15일, 기준 커밋 `ac9d254774ae5c1d0ebdc9019f249b7ddceb68e8`에서 수정했습니다. Refs #240, #244.

## 변경 내용

- F1: 단일 자료 저장·AI 생성 대기 중 입력, 취소, 다른 자료 편집, 화면 닫기를 잠급니다. 저장 요청의 초안 revision을 비교하여 편집기가 해제·재생성된 뒤 도착한 응답이 부모에 보존된 새 초안을 지우지 않습니다. 저장 실패 시 입력을 유지하고 조작을 다시 허용합니다.
- F2: 추천 범위 오류와 자료 버전·상태 충돌을 오류 코드로 구분합니다. 버전 충돌 시 입력을 보존하고 최신 제목·본문·체크리스트·상태·버전을 표시합니다. 관리자가 최신 서버본으로 시작하거나 현재 입력에 최신 버전을 적용한 뒤 별도의 저장 버튼으로 재시도합니다. 조회 실패·자료 누락·게시 상태에서는 재저장을 막습니다. 같은 자료의 게시 차단은 객체 참조가 아닌 material_id를 사용합니다.
- F3: 통합 자료 목록의 게시·취소 충돌은 작업한 행을 최신 상세로 갱신합니다. 다른 편집기의 필드에는 반영하지 않습니다. 목록에서 최신 내용을 확인한 뒤 별도 게시 확인을 수행하며, 상세 조회 실패는 해당 행에서 다시 조회할 수 있습니다. 현재 편집한 자료의 충돌은 기존 초안 비교 화면으로 연결됩니다.
- G1 프론트: 상위 3개 추천 카드·단일 자료 편집과 전체 통합 대상 선택을 분리합니다. `/api/education/bundles/targets`를 읽고 같은 PPE의 서로 다른 2–20개 구역을 선택합니다. 구캐시의 완전성을 추정하지 않으며 명시적인 추천 갱신 수단을 제공합니다. 갱신 결과 적용은 별도 선택으로 처리하고, 선택과 snapshot을 무효화하면서 입력을 보존합니다. 통합 생성의 범위 409 뒤 조회한 snapshot도 명시적인 미리보기 조회 전에는 사용할 수 없습니다.

G1 백엔드는 별도 PR [#254](https://github.com/Oceankok/CBNU-CAPSTONE-Kyo/pull/254), 커밋 `dc198a43bc75af7837188b56f0ef11fd3406b669`에 있습니다. 본 변경의 대상 API 사용은 해당 백엔드와 결합해야 합니다. 응답의 `generated_at`은 미생성 시 null을 허용합니다. 대상 읽기 실패로 생성 POST를 호출하지 않습니다. 기존 상위 추천 최초 조회의 404 → 최초 생성 정책은 유지합니다.

## 검사 방법과 결과

원래 감사의 `reports/audit-20261115/reproduce_frontend.cjs`를 공유 소스에서 실행해 F1–F3의 기존 결과를 재확인했습니다. 이 검사는 TSX 처리 함수와 React 훅/API 대역을 사용하며 브라우저 검사가 아닙니다.

새 검사는 Playwright 1.65.1과 Microsoft Edge 157.0.4341.46 headless에서 실제 React, React DOM, ConfirmButton, 교육 API client와 컴포넌트를 실행합니다. HTTP 응답만 route 대역으로 제공합니다. 서버는 127.0.0.1:5187에서 시작·종료되며 Vite 운영 설정·환경 파일·backend proxy를 읽지 않습니다. 외부 origin 요청은 차단합니다.

| 검사 | 결과 |
|---|---|
| 기준 컴포넌트로 핵심 F1/F2/F3 브라우저 검사 | 예상한 3건 실패: 대기 입력 잠금 없음, 최신 서버본 검토 없음, 목록 충돌 갱신 없음 |
| 수정 컴포넌트·페이지 브라우저 시나리오 | 최종 25개 통과 |
| `npm run lint` | 오류·경고 없음 |
| `npm run build` | TypeScript 및 Vite 빌드 통과 |
| `scripts/schema31.check.ts` | 통과 |
| `scripts/schema31.interactions.check.ts` | 통과 |
| `scripts/educationBundles.check.ts` | 통합 API·편집기 계약 통과 |
| `git diff --check` | 통과 |

브라우저 검사에는 지연 응답 중 입력·취소·전환 차단, 저장 실패, 편집기 해제·재생성, 버전 충돌의 두 가지 명시 선택, 최신 게시 상태, 상세 조회 실패·재조회, 편집기 없이 실행한 목록 게시/취소, 다른 초안 보존, 현재 통합 편집 충돌, 20구역 상한·PPE 제한, 빈 대상/400/401/403/503 읽기 무생성, 명시 갱신·snapshot 재조회, 분기 전환 후 늦은 응답, 상위 추천에서 빠진 단일 초안 보존이 포함됩니다.

최종 실행에서 24개가 통과했고 1개는 검사 선택자가 본문이 아닌 `<b>` 레이블만 선택하여 실패했습니다. 선택자만 수정하고 그 1개를 `--last-failed`로 재실행하여 통과했습니다. 이 과정에서 제품 코드는 변경하지 않았고 이미 통과한 24개는 반복 실행하지 않았습니다. [검사별 결과와 소스 해시](education-acceptance-20261115/results.json), [기준 코드의 실패 로그](education-acceptance-20261115/baseline-red.log)에 근거를 남겼습니다.

## 재실행

`frontend` 디렉터리에서 실행합니다. Windows의 설치된 Edge를 사용하는 경우:

```powershell
npm ci
$env:PLAYWRIGHT_CHANNEL = 'msedge'
npm run test:education:browser
npm run lint
npm run build
node --experimental-strip-types scripts/schema31.check.ts
node --experimental-strip-types scripts/schema31.interactions.check.ts
node --experimental-strip-types scripts/educationBundles.check.ts
```

CI 또는 Chromium을 쓰는 환경은 `npx playwright install --with-deps chromium`을 실행하고 `PLAYWRIGHT_CHANNEL`을 설정하지 않습니다. JSON 결과는 `frontend/test-results/education.json`, 실패 trace는 같은 디렉터리에 생성됩니다. 테스트 서버는 재사용하지 않으므로 5187 포트는 비어 있어야 합니다.

기준 코드 실패 검사는 별도 작업본에 현재 검사 파일을 준비한 뒤 두 편집기 파일만 위 기준 SHA의 내용으로 교체하고 다음 필터를 사용합니다. 본 검사에서는 본인 worktree만 임시 교체하고 finally에서 복원했습니다.

```powershell
npm run test:education:browser -- --reporter=line --grep 'delayed save locks|version conflict requires explicit resolution: 내 입력|list publish refreshes row and preserves other draft=false'
```

## 남은 검증

이 기록은 코드 반영 준비 및 격리 브라우저 검사 근거입니다. 실제 backend/인증/DB에 연결한 두 관리자 브라우저 경합과 PR #254 결합 검사는 수행하지 않았습니다. 운영 DB·실카메라·방송·자동 확정·호스트 서비스를 사용하거나 변경하지 않았고 merge/deploy도 수행하지 않았습니다. 실제 배포 화면 검증은 별도 통합·현장 실증 대상으로 남깁니다. 이슈는 종료하지 않습니다.
