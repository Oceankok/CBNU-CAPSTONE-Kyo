# 선택적 기존 PostgreSQL 이관·복구 실행 순서

이 절차는 기존 PostgreSQL 데이터를 보존하며 새 기능을 반영하기로 선택한 경우에만 사용합니다. 이번 시연은 최신 PostgreSQL의 새 DB로 시작하는 것이 기본이며, 기존 개발용 SQLite·더미 데이터 이관은 제외합니다. #174의 이관·자동 복구는 필수 기능 PR의 병합이나 동료 프로그램 연결을 막는 선행 조건이 아닙니다.

새 DB의 첫 배포는 [서버 배포 안내](20261013_Server_Release_Setup.md)를 사용합니다. 기존 배포의 초기화 방지 검사를 우회하거나 기존 DB를 삭제하지 않습니다. 새 시연 DB는 별도 배포 폴더·Compose 프로젝트로 준비하며, 실제 서비스 교체 전에 포트와 worker 연결을 확인합니다. 이 문서의 준비 코드와 격리 검증은 실제 배포 DB나 Windows 예약 작업을 자동으로 변경하지 않습니다.

## 반영 순서

1. 독립 송신 버그 수정 #173을 반영합니다. 기능 PR은 #167 → #168 → #170 → #171 → #172 순서로 반영합니다. #169는 독립 문서 PR입니다.
2. 이 배포 준비 PR은 #172와 #173을 합친 기준입니다. 선행 변경을 다시 구현하지 않았으며, 선행 병합 후 최신 main을 반영해 최종 차이·CI를 확인합니다. 기능 브랜치를 강제 push하거나 승인 없이 병합하지 않습니다.
3. 최종 main의 CI가 성공하고 Release에서 API/web 이미지가 게시된 뒤 해당 main 커밋·이미지 digest를 사용합니다. PR 전용 로컬 이미지를 실제 서버 배포에 사용하지 않습니다.

## 배포 전 체크리스트

- [ ] 서버를 수동 시작해 기존 API·PostgreSQL·runner가 실행 중인지 확인합니다. 자동 시작은 다시 켜지 않습니다.
- [ ] AI·현장 담당과 송신·추론 중지를 맞추고 새 이벤트 접수를 멈춥니다.
- [ ] GPU worker가 기존 작업과 cleanup 보고를 마칠 때까지 기다린 후 worker를 중지합니다. `KyoGpuWorker`만 중지하고, Docker·API·runner까지 종료하는 전체 종료 바로가기는 사용하지 않습니다.
- [ ] 관리자·작업자의 화면 조작과 외부 API 쓰기를 중지합니다. `ClientsPaused`는 이 상태를 사람이 확인했다는 명시적 옵션입니다.
- [ ] 기존 백업 정책에 따라 필요 보관 미디어·동의서의 전체 snapshot을 확보합니다. 이관 자체는 DB만 변경하며 파일을 재인코딩·복사하지 않습니다.
- [ ] 실제 현재 스키마 버전과 선택한 이미지의 목표 버전을 확인합니다. 이 릴리스의 목표는 v14이며 기존 서버 기록은 v10입니다. 실행 시 검사와 다르면 진행하지 않습니다.
- [ ] 디스크 공간, 서버 전원, main CI·이미지 게시 상태와 production 승인자를 확인합니다.

이관 도구는 대기·실행 중 processing job 또는 미확인 worker cleanup이 하나라도 있으면 거절합니다. 카메라·현장·worker의 중지 확인을 생략하고 옵션만 켜지 않습니다.

## 실행 방법

권장 방법은 GitHub Actions의 Release → Run workflow에서 최종 main을 선택하는 것입니다. 기존 production 승인 절차를 유지합니다.

| 입력 | 값 |
| --- | --- |
| deploy | true |
| https / lan | 현재 배포와 같은 접속 모드 |
| migrate_database | true |
| clients_paused | 위 중지·drain을 완료한 뒤 true |
| expected_schema_version | 확인한 현재 버전, 기존 기록은 10 |
| target_schema_version | 선택한 이미지가 지원하는 버전, 이 릴리스는 14 |

일반 이미지 교체에서는 `migrate_database=false`를 유지합니다. DB 초기화 옵션을 재사용하지 않습니다. 서버 PC를 켜기만 하면 배포되는 구조로 바꾸지 않았습니다.

직접 실행할 때는 게시된 Release artifact의 정확한 40자리 commit과 두 이미지 digest를 `Release.ps1`의 기존 필수 인수로 전달하고 다음 인수를 추가합니다.

```powershell
-MigrateDatabase -ClientsPaused -ExpectedSchemaVersion 10 -TargetSchemaVersion 14
```

이 예시의 버전은 실제 검사와 일치해야 합니다. 비밀번호·토큰을 명령 인수로 받지 않으며 기존 보호된 `server.env`를 사용합니다. 이 문서에 실제 secret 값을 복사하지 않습니다.

## 실행되는 순서

1. 배포 잠금과 기존 release/project·설정 검증, 이미지 다운로드를 수행합니다.
2. 새 이미지의 `backend.db.upgrade inspect`로 현재 버전·처리 작업 상태를 읽기 전용 검사합니다. 여기서 실패하면 실행 중 서비스는 정지하지 않습니다.
3. API/web/maintenance와 해당 시 HTTPS gateway를 정지하고 실제 정지 여부를 확인합니다.
4. PostgreSQL custom-format dump를 생성해 고유 이름으로 보관하고 SHA-256을 기록합니다. Windows 텍스트 파이프로 바이너리를 전달하지 않습니다.
5. 새 이미지의 `backend.db.upgrade apply`를 실행합니다. v10 이상의 정확한 시작 버전과 현재 이미지의 목표 버전만 허용하고 seed 없이 이관합니다. 기존 테이블별 행 수 보존과 최종 스키마를 검사합니다.
6. 새 API/web을 기동해 양쪽 readiness와 해당 시 HTTPS를 확인합니다. 삭제 maintenance는 아직 기동하지 않습니다.
7. 새 release manifest를 확정한 뒤 maintenance를 기동합니다. 이 시점 이후에는 새 쓰기가 발생할 수 있으므로 자동으로 과거 DB를 덮어쓰지 않습니다.

`releases/<commit>/upgrade-state.json`에는 단계, 버전, 백업 경로와 checksum을 기록합니다. DB 행·토큰·비밀번호는 기록하지 않습니다.

## 실패별 복구

| 상태 | 처리 |
| --- | --- |
| 사전 검사 실패 | 기존 서비스·DB 유지, 버전·잔여 작업을 해결한 뒤 재실행합니다. |
| 서비스 정지·백업 실패 | 이관하지 않고 이전 이미지 기동을 시도합니다. |
| 이관 또는 새 이미지 readiness 실패 | 모든 서버 쓰기를 정지하고 백업 checksum을 확인한 뒤, 해당 Compose 프로젝트의 `kyo` DB를 백업 시점으로 복원하고 이전 이미지를 기동합니다. DB 전체를 백업 시점으로 되돌리는 작업이므로 외부 쓰기가 중지되어 있어야 합니다. |
| 백업 손상·DB 복원 실패 | 이전 이미지를 잘못된 DB에 연결하지 않고 서버 서비스를 정지 상태로 남깁니다. 백업과 로그를 보존한 채 수동 복구합니다. |
| manifest 확정 후 maintenance 기동 실패 | 새 DB·이미지·manifest를 유지하고 `ready_maintenance_failed`를 기록합니다. maintenance만 재기동합니다. 이미 시작된 새 쓰기를 잃지 않도록 과거 DB 자동 복원을 수행하지 않습니다. |

자동 복원은 이관 옵션으로 실행한 배포의 확정 전 실패에만 적용합니다. 일반 이미지 교체는 기존처럼 이미지 복구 경로를 사용합니다. 확정 이후의 과거 버전 복귀는 데이터·파일 변경을 함께 검토하는 별도 작업입니다.

## 성공 후 바로 확인할 항목

- [ ] API·웹 readiness와 로그인·로그아웃, 관리자·작업자 권한
- [ ] 카메라·node 연결, 카메라 그룹과 적용 PPE 조회
- [ ] worker 재시작·heartbeat 후 기존 얼굴 모델/CUDA 연결
- [ ] AI 프로그램을 재개하고 HTTP 이벤트 ID를 재전송해 중복이 생기지 않는지 확인
- [ ] 현장 송신·처리 조회·원본/참고 클립·방송 명령·비식별화 완료 URL 연결
- [ ] 관리자 화면에서 동의서·교육 게시·수동 병합을 연결한 뒤 검토·보관·철회/삭제
- [ ] production 승인·배포 결과와 backup/upgrade-state 경로 기록

구체적인 API 순서와 토큰 역할은 [백엔드 연결 안내](20261023_Backend_Integration_Handoff.md)에 정리했습니다. 실제 카메라·YOLO·스피커·모바일 접속 검증은 대응 프로그램과 장치가 연결된 뒤 수행합니다.

## 격리 재검증 명령

프로젝트 루트의 시스템 Python에서 실행합니다. 아래 Python 검증은 실제 개발 DB를 사용하지 않습니다.

```powershell
$env:PYTHONUTF8 = "1"
$env:PPE_TEST_POSTGRES_URL = ""
python -m unittest discover -s backend -t . -v
```

PowerShell 7에서 다음을 실행하면 실제 Docker 대신 가짜 Docker로 배포 실패 분기를 검증합니다.

```powershell
./deploy/tests/Release.Tests.ps1
./deploy/tests/Upgrade.Tests.ps1
./deploy/tests/Prepare-Docker.Tests.ps1
```

Docker와 ffprobe가 준비된 환경에서는 다음 명령으로 전용 임시 프로젝트의 PostgreSQL v10→v14 이관·실제 pg_dump/pg_restore, 실제 송신 루프의 HTTP 접수, 기존 웹/API 전체 시나리오를 검증합니다.

```powershell
$env:PYTHONUTF8 = "1"
python deploy/tests/validate_backend_integration.py
```

이 검증은 production 주소·DB·볼륨을 받지 않고 전용 테스트 리소스만 정리합니다. Windows Release 스크립트의 실패 분기는 가짜 Docker, DB 이관·dump 복원·송신 HTTP는 실제 격리 Docker로 나누어 검증합니다. 실제 Windows 서버 배포를 이미 수행했다고 간주하지 않습니다.
