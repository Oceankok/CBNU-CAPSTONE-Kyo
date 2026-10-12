# PostgreSQL 전환 — #129

## 기반 및 범위

`Chore/postgresql-migration`은 다중 구역 PR #130과 API Docker PR #131을 로컬에서 통합한 브랜치입니다. 두 PR이 main에 병합된 뒤 PostgreSQL 변경만 후속 PR로 제출합니다. 기존 개발 DB는 변경하지 않았습니다.

`PPE_DATABASE_URL`이 설정되면 PostgreSQL을 사용하고, 비어 있으면 기존 `PPE_DB_PATH` SQLite를 사용합니다. 잘못된 URL·연결 실패에서 SQLite로 자동 전환하지 않습니다. DB 교체로 로그인·이벤트·미디어 API 응답 형태를 변경하지 않습니다.

## 구현

- psycopg 3 연결과 바인딩·행 접근을 공통화합니다. 현재 TEXT·0/1 플래그·JSON 문자열 저장 형태를 유지합니다.
- PostgreSQL v7 기준 스키마와 명시적 시연 seed를 추가합니다. 외래키 참조 순서와 epoch 시각의 DOUBLE PRECISION을 반영합니다.
- PostgreSQL은 `app_schema_version`, SQLite는 기존 `PRAGMA user_version`으로 버전을 관리합니다. SQLite의 과거 단계별 마이그레이션을 PostgreSQL에 그대로 실행하지 않습니다.
- PostgreSQL 초기화는 새 DB에 v7 기준 스키마를 원자적으로 생성합니다. 버전이 다른 DB·버전 기록 없이 기존 테이블이 있는 DB는 별도 마이그레이션을 요구합니다.
- PostgreSQL seed는 기본 비활성화이며 `PPE_DB_SEED=true`일 때만 시연 카메라·이벤트·설정을 생성합니다. 기존 SQLite 파일의 자료·계정은 자동 이관하지 않습니다.
- SQLite의 직렬화된 쓰기 임계 영역을 PostgreSQL 트랜잭션 advisory lock으로 대체합니다. 미디어 게시·삭제·검토·장비 확정 등의 기존 경쟁 조건 방지 의도를 유지합니다. 초기에는 스키마 단위로 직렬화하고, 처리량 확장이 필요할 때 사건·노드별 잠금으로 세분화합니다.
- 주차 계산은 SQLite `%W`의 월요일·0주 기준을 유지합니다. ISO 주차로 임의 변경하지 않습니다.
- 기존 Docker 설정에 `compose.postgres.yaml`을 덧붙여 DB를 선택합니다. PostgreSQL 포트는 외부에 공개하지 않습니다.

## 설치·실행 계획

패키지 설치·PostgreSQL 실행·테스트는 별도 승인 후 수행합니다. 네이티브 연결에는 `backend/requirements-postgres.txt`를 설치합니다. Docker API 의존성에도 해당 드라이버를 추가했습니다.

Compose에서는 `.env`의 `PPE_POSTGRES_PASSWORD`와 `PPE_DATABASE_URL=postgresql://kyo:<URL 인코딩한 비밀번호>@postgres:5432/kyo`를 동일한 계정으로 설정합니다. 실제 값을 Git·로그·API 응답에 기록하지 않습니다.

```powershell
Set-Location C:\Users\robin\Projects\CBNU-CAPSTONE-Kyo
docker compose -f compose.yaml -f compose.postgres.yaml config --quiet
docker compose -f compose.yaml -f compose.postgres.yaml build api
docker compose -f compose.yaml -f compose.postgres.yaml --profile tools run --rm db-init
docker compose -f compose.yaml -f compose.postgres.yaml up -d api maintenance
```

SQLite와 PostgreSQL을 동시에 켜거나 동일 API 주소에서 번갈아 사용하지 않습니다. PostgreSQL 전환 후 기존 SQLite 데이터·사용자·미디어를 자동 복사하지 않으므로 테스트용 신규 계정을 준비합니다. 실제 배포 전 DB 백업·복구와 후속 스키마 마이그레이션 절차를 추가 검증합니다.

## 테스트 구조

- 기본 백엔드 회귀 테스트는 기존 격리 SQLite 환경을 유지합니다.
- `PPE_TEST_POSTGRES_URL`은 `kyo_test`로 시작하는 별도 DB만 허용합니다. 각 fixture는 UUID schema를 생성하고 완료 후 해당 schema만 정리합니다.
- 같은 API 회귀 테스트를 PostgreSQL에도 적용합니다. SQLite v3~v6 역사적 이관 테스트는 SQLite job에서 수행하고 PostgreSQL job에서는 명시적으로 제외합니다.
- PostgreSQL 전용 초기화 재실행·주차 경계·동시 쓰기 잠금 검증을 추가했습니다.
- CI는 GitHub 호스팅 runner의 `services: postgres`를 사용합니다. self-hosted 배포 runner는 추가하지 않습니다.
- 실제 GPU·방송·외부 이미지 분석을 실행하지 않습니다.

## 2026-10-12 검증 결과

- 시스템 Python에 psycopg·psycopg-binary 3.3.6과 tzdata를 설치했습니다.
- SQLite: 전체 75개 중 72개 통과, PostgreSQL 전용 테스트 3개를 제외했습니다.
- PostgreSQL 17: 전체 75개 중 71개 통과, SQLite v3~v6 과거 이관 전용 테스트 4개를 제외했습니다. 해당 4개는 SQLite 실행에서 통과했습니다.
- PostgreSQL 초기화 재실행·연도 경계 주차 계산·동시 쓰기 잠금을 확인했습니다. 기존 API 회귀 테스트는 임시 schema에서 수행했습니다.
- PostgreSQL Compose 설정·API 이미지 빌드·초기화·readiness·로그인·장비 조회·maintenance를 확인했습니다. 처리 요청 3종은 503으로 차단됐고 미디어·후보를 생성하지 않았습니다.
- API·maintenance 컨테이너 재생성 후 계정·처리 결과 및 비공개 저장소의 검증용 파일이 유지됐습니다. 실제 영상 처리 품질 검증은 아닙니다.
- 검증용 PostgreSQL 컨테이너·저장소와 Compose 프로젝트를 종료 후 정리했습니다. 빌드 이미지·캐시는 유지했습니다. 기존 개발 DB·미디어·GPU·방송·외부 VLM은 사용하지 않았습니다.
- GitHub PostgreSQL CI job은 구성했지만 이 브랜치의 PR을 아직 생성하지 않아 실제 Actions 실행은 별도입니다.

## 별도 후속 작업

호스트 GPU worker·영상 전송·HTTPS·프론트 컨테이너·CD는 이번 DB 전환과 별도입니다. SQLite 전용 `check_db.py`·`check_seed_events.py` 및 과거 수동 DB 스크립트는 PostgreSQL 운영 도구로 사용하지 않습니다. 서버 readiness와 새 초기화 경로를 사용합니다.

근거: [psycopg 트랜잭션](https://www.psycopg.org/psycopg3/docs/basic/transactions.html), [PostgreSQL advisory lock](https://www.postgresql.org/docs/current/functions-admin.html)
