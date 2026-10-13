# GPU 없이 실행하는 시연 데이터

관련 이슈: #136

일반 초기화 데이터와 분리하여 이벤트·검토·통계·교육 화면을 확인합니다. 인물 없는 자체 제작 그림과 H.264 1초 클립을 사용합니다. 실제 AI 탐지나 얼굴 비식별화 정확도 검증 자료가 아닙니다. 모델과 GPU는 필요하지 않습니다.

## Windows 실행

프로젝트 루트의 PowerShell에서 다음을 실행합니다. `PPE_DB_PATH`는 반드시 전용 파일 `kyo_demo.db`를 지정합니다. 기존 개발 DB는 변경하지 않습니다.

```powershell
$env:PPE_DATABASE_URL = ''
$env:PPE_DB_PATH = Join-Path (Get-Location) '.private_media/demo/kyo_demo.db'
$env:PPE_DB_SEED = 'false'
$env:PPE_PROCESSING_MODE = 'disabled'
$env:PPE_JWT_SECRET = python -c "import secrets; print(secrets.token_urlsafe(48))"
python -m backend.db.seed_demo
python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

생성 명령에서 8자 이상의 시연 계정 비밀번호를 입력합니다. 계정은 `DEMO_admin`, `DEMO_worker01`, `DEMO_worker02`이며 입력한 비밀번호를 공유합니다. 작업자 01은 두 구역, 작업자 02는 한 구역에 배정됩니다.

프론트는 기존 실행 방법을 사용합니다. 기본 로컬 API 연결을 사용하는 경우 별도 터미널에서 다음을 실행합니다.

```powershell
cd frontend
npm run dev
```

## 포함한 상태

| 이벤트 | 상태 및 자료 |
| --- | --- |
| DEMO_EVT_01 | 미검토, 이미지 |
| DEMO_EVT_02 | 미검토, 이미지와 영상 |
| DEMO_EVT_03 | 미검토, 서로 다른 카메라의 참고 이미지 2개 |
| DEMO_EVT_04 / 05 / 11 / 12 | 위반 확정 |
| DEMO_EVT_06 | 오탐 |
| DEMO_EVT_07 | 보류 |
| DEMO_EVT_08 | 원본 클립 확보 불가로 검토 불가 |
| DEMO_EVT_09 | 처리 중 표시용 자료 |
| DEMO_EVT_10 | 처리 실패 표시용 자료 |

2026년 2·3·4분기 통계와 교육 추천을 생성합니다. 처리 중·실패 상태는 화면 확인을 위한 정적 데이터이며 GPU 작업을 자동 실행하지 않습니다. 서로 다른 카메라의 참고 자료 연결 예시를 제공하며 자동 사건 병합은 수행하지 않습니다.

## 다시 생성

서버를 종료하고 **위 환경변수가 유지된 터미널**에서 실행합니다.

```powershell
python -m backend.db.seed_demo --reset
```

예약된 `DEMO_` 이벤트와 시연 계정, 해당 이벤트의 시연 미디어를 다시 생성합니다. 이전 시연 계정 토큰은 무효화되므로 다시 로그인합니다. 다른 이벤트는 삭제하지 않습니다. 일반 개발 DB를 대상으로 실행하면 거부합니다. 파일 이름 확인만으로 실행 대상을 구분하므로 실제 운영 DB를 시연 파일 이름으로 지정하면 안 됩니다.

PostgreSQL에서는 별도로 생성한 **`kyo_demo` 데이터베이스**를 `PPE_DATABASE_URL`에 지정한 뒤 같은 명령을 실행합니다. 기본 운영 데이터베이스에서는 실행하지 않습니다. DB 생성·접속 정보 설정은 기존 PostgreSQL 실행 문서를 따릅니다.
