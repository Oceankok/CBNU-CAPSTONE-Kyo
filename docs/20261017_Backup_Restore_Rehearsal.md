# PostgreSQL·보관 미디어 백업 및 격리 복원 리허설

관련 이슈: #151, #129

## 범위

수동 명령으로 release 스택의 PostgreSQL과 동의가 확인된 비식별 보관 자료를 함께 백업합니다. 복원은 포트를 공개하지 않는 별도 PostgreSQL·미디어 volume에서 수행하고 결과를 확인한 뒤 테스트 자원을 삭제합니다. 기존 서버 DB나 volume을 덮어쓰는 기능은 제공하지 않습니다.

백업 대상 API 이미지에 `backend.backup.media`가 포함되어 있어야 합니다. 해당 변경을 병합·게시한 release부터 사용할 수 있습니다. 호스트 Python은 표준 라이브러리만 사용하며 컨테이너는 기존 API 의존성을 사용합니다. DB 스키마 변경이나 새 패키지 설치는 없습니다.

## 백업 자료와 보관 정책

- PostgreSQL 전체 DB를 `pg_dump -Fc`로 백업합니다. 이벤트·검토·계정 정보와 비밀번호/노드 토큰의 해시도 포함되므로 접근을 제한한 로컬 디스크에 저장합니다. Git, GitHub Actions artifact, 공개 미디어 경로에 업로드하지 않습니다.
- 미디어는 `ready` + `complete`, `decision=retain`, `consent_confirmed=1`, 만료 전 `retain_until`을 모두 만족하는 처리 결과만 포함합니다. 해당 파일이 없거나 처리 결과 디렉터리를 벗어나면 백업을 실패 처리합니다.
- 검토 중 자료, 미동의 자료, 원본, 영상 수신 버퍼, 모델, env 파일, ChatGPT/OAuth 자격 증명 파일은 포함하지 않습니다. 전체 작업 파일을 보전하는 백업이 아닙니다. 복원 시 제외된 미디어는 `missing` / `source_file_missing`으로 정리합니다.
- 기본 한도는 파일 1,000개, 파일당 100 MiB, 합계 2 GiB입니다. 초과 시 누락시키지 않고 실패 처리합니다.
- 백업 유효 기간은 **생성 후 24시간과 포함 자료의 가장 빠른 보관 만료 시각 중 이른 시각**입니다. 만료된 백업은 검증·복원할 수 없으며 `prune --apply`로 제거합니다. 정리 명령은 자동 예약되지 않으므로 운영자가 정기 실행해야 합니다.
- 보관 동의 철회나 삭제 결정이 나중에 이루어져도 기존 백업 파일이 즉시 지워지는 것은 아닙니다. 관련 백업은 별도로 폐기해야 합니다. 과거 DB에는 이후 삭제 결정을 알 수 없으므로 복원 결과를 운영에 재게시하기 전 최신 삭제·동의 기록을 대조해야 합니다. 이번 도구는 이를 대신하는 운영 복구/게시 기능을 제공하지 않습니다.
- `Release.ps1`이 배포 전에 남기는 DB 단독 dump는 이번 `snapshot-*` 묶음과 별개입니다. 이 명령의 `prune`이 해당 dump까지 삭제하지는 않습니다.

## 사전 조건

현재 release의 API·PostgreSQL이 실행 중이어야 합니다. `DeploymentRoot`는 checkout 밖의 기존 배포 경로이며 `server.env`, `current-release.json`, 해당 release Compose가 있어야 합니다. API·웹 이미지는 게시된 GHCR digest로 고정합니다. Docker 실행 계정에 해당 이미지를 pull할 권한과 백업 폴더 쓰기 권한이 필요합니다.

백업은 API·maintenance 및 실행 중인 web/gateway를 잠시 정지합니다. 영상 접수와 방송 명령 조회도 그동안 중단되므로 현장 프로그램의 일시 실패를 허용할 수 있는 시간에 수행합니다. 별도 DB 직접 쓰기 프로세스나 수동 관리 작업은 동시에 실행하지 않습니다. 배포와 백업은 같은 `release.lock`으로 동시 실행을 차단합니다. 강제 종료 시 원래 서비스의 재시작 여부와 lock의 PID를 직접 확인해야 합니다.

SHA-256은 파일 손상을 확인하는 용도이며 출처를 인증하는 서명은 아닙니다. **직접 생성하고 보호한 백업만 복원합니다.** `pg_restore`에 다른 사람이 제공한 dump를 입력하지 않습니다.

## 실행 방법

프로젝트 루트에서 실행합니다. 실제 서비스 중단과 백업 실행은 운영자의 승인을 받은 뒤 진행합니다.

```powershell
$deploymentRoot = Join-Path $env:USERPROFILE 'KyoServer'
python deploy/snapshot.py backup --deployment-root $deploymentRoot --allow-service-pause
```

명령이 반환한 `backup_directory`를 다음 명령에 사용합니다. `backups/snapshot-<UUID>`에는 `postgres.dump`, `media.tar`, `media.json`, `bundle.json` 네 파일이 생성됩니다. 서비스 정지 → DB dump → 미디어 선별/해시 생성 → 묶음 해시 생성 순서로 실행하고, 실패해도 원래 실행 중이던 서비스를 재시작하도록 시도합니다. 재시작 실패는 명령 실패로 표시하므로 컨테이너 상태를 확인합니다.

```powershell
$backupDirectory = Read-Host '반환된 backup_directory 전체 경로'
python deploy/snapshot.py verify --backup $backupDirectory
python deploy/snapshot.py restore-check --backup $backupDirectory
```

`verify`는 묶음 파일의 해시·유효 기간을 검사합니다. `restore-check`는 별도 `kyo-restore-<UUID>` 프로젝트와 `kyo_restore_<UUID>` DB를 생성하고 다음을 수행합니다.

1. PostgreSQL 17에 실제 `pg_restore`를 수행합니다.
2. 동일 release API 이미지에서 스키마 버전·미디어 해시·보관 조건·경로를 확인합니다. 심볼릭 링크나 예상하지 않은 tar 항목은 거부합니다.
3. 빈 미디어 volume에 포함 자료를 복원합니다. 원본 경로와 수신 버퍼·처리 작업은 정리하며, 대기 클립 요청은 `unavailable`로 변경합니다.
4. 현장 노드를 비활성화하고 대기/진행 중 명령을 `skipped`로 변경하여 과거 방송을 다시 실행하지 않습니다. 사용자 `auth_version`을 증가시켜 과거 Bearer token을 무효화합니다.
5. `isolated_restore: PASS`와 복원 자료/이벤트/사용자 수를 출력하고 테스트 컨테이너·volume을 제거합니다. 원래 서버는 변경하지 않습니다.

복원 리허설은 로그인 UI·GPU 추론·실제 방송·운영 서버 전환을 검증하지 않습니다. 현재 실행 중인 서버를 복원하는 별도 절차는 아직 포함하지 않습니다.

## 만료 백업 정리

```powershell
python deploy/snapshot.py prune --deployment-root $deploymentRoot
python deploy/snapshot.py prune --deployment-root $deploymentRoot --apply
```

첫 명령은 대상만 표시하고 두 번째 명령은 실제 삭제합니다. `snapshot-<UUID>` 형식이며 만료된 manifest와 정확히 네 파일만 있는 폴더만 삭제합니다. 알 수 없는 파일이 추가된 폴더는 자동 삭제하지 않습니다. 백업·검증·복원과 정리를 동시에 실행하지 않습니다. 동의 철회에 따른 조기 폐기는 관리자가 관련 묶음을 확인하여 별도로 처리합니다.

## 개발 검증

승인 후 다음을 실행합니다. 단위 테스트는 임시 SQLite 또는 `PPE_TEST_POSTGRES_URL`의 전용 테스트 DB 스키마를 사용합니다.

```powershell
python -m unittest backend.tests.test_backup -q
python deploy/tests/validate_snapshot.py
```

두 번째 명령은 로컬 검증용 API 이미지를 빌드하고 별도 Docker 프로젝트에서 합성 파일·테스트 DB를 생성합니다. 실제 dump/restore, 파일 내용 일치, 원본 제외, 노드 비활성화, 명령 재실행 차단, 서버 원본 유지 여부를 확인한 뒤 자체 컨테이너·volume을 정리합니다. 검증 프로세스에서만 로컬 이미지 태그를 허용하며 실제 백업 명령의 GHCR digest 제한은 유지합니다.

2026년 10월 17일 검증 결과:

- 새 백업 테스트 9개를 포함하여 SQLite·PostgreSQL 각각 전체 110개 테스트를 실행했습니다. 각각 106개 통과, 해당 DB에서 적용하지 않는 기존 테스트 4개는 skip했습니다.
- 별도 Docker 프로젝트에서 실제 `pg_dump`·`pg_restore`, 미디어 바이트 일치, 비공개 원본 제외, 계정 인증 버전 갱신, 노드/명령 비활성화, 원래 테스트 서버 자료 유지와 서비스 재시작을 확인했습니다.
- 리허설 컨테이너·volume은 정리했습니다. 실제 배포 서버의 백업·복구, 실제 계정 로그인과 현장 노드 재연결은 후속 배포 검증에서 진행합니다.
