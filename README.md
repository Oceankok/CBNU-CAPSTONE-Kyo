# CBNU-CAPSTONE-Kyo

> AI 기반 산업현장 PPE 분석 및 안전교육 추천 시스템
> 충북대학교 캡스톤 디자인 프로젝트 (팀 kyó)

---

## 프로젝트 개요

금속·기계 제조 공장에서 CCTV 영상을 분석하여 PPE(개인 보호 장비) 미착용 상황을 탐지하고, 담당자 검토를 거쳐 분기별 위반 통계를 집계한 뒤 안전교육 우선순위를 추천하는 시스템이다.

- **1차 목적**: PPE 미착용 즉시 시정 알림 (안전 확보)
- **2차 목적**: 분기별 경향 분석 및 교육 우선순위 추천

---

## 팀 구성

| 팀원 | GitHub ID | 담당 |
|---|---|---|
| 김순겸 (팀장) | Oceankok | AI 모델 (YOLO, OpenCV), 시스템 통합 |
| 김재환 | robinjh | 백엔드 API, DB 스키마, 교육 추천 로직 |
| 유현우 | yhw1737 | 어드민 대시보드 프론트엔드 |
| 오재식 | ohjaesik | 데이터 수집, 라벨링, 문서화, 데모 시나리오 |

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| PPE 탐지 | Ultralytics YOLO + OpenCV |
| 백엔드/API | Python + FastAPI |
| 데이터베이스 | SQLite (캡스톤), PostgreSQL (실서비스 검토) |
| 프론트엔드 | React + TypeScript (Vite) + Recharts |
| 차트 | Recharts |

---

## 실행 방법

> 요구 사항: **Python 3.12 이상** (3.9에서는 인증 코드가 실행되지 않음), **Node.js 20.19 이상**
> 상세 설정: [인증·현장 방송](docs/20261003_Auth_Broadcast_Setup.md) · [얼굴 비식별화(GPU)](docs/20261004_Media_Privacy_Setup.md)

### 1. 백엔드 (저장소 루트에서 실행)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

python backend/db/init_db.py                                   # 테이블·시드 생성 (기존 데이터 유지)
python -m backend.auth.manage_users admin01 --role admin --name 관리자
python -m backend.auth.manage_users worker01 --role worker --name 작업자 --zone "프레스 구역"

export PPE_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"   # Windows: $env:PPE_JWT_SECRET = ...
python -m uvicorn backend.api.main:app --reload
```

- `cd backend && uvicorn api.main:app`처럼 `backend/` 안에서 실행하면 import 오류가 난다. 반드시 저장소 루트에서 `backend.api.main:app`으로 실행한다.
- `PPE_JWT_SECRET`이 없으면 로그인이 503을 반환한다. 키를 바꾸면 기존 로그인이 풀리므로 같은 값을 재사용한다.
- 얼굴 비식별화는 CUDA GPU가 필요하다. `onnxruntime-gpu`는 macOS용 배포판이 없어 **Mac에서는 `backend/requirements.txt` 설치가 실패**한다. Mac에서는 해당 줄을 빼고 설치한다:
  `grep -v '^onnxruntime-gpu' backend/requirements.txt | pip install -r /dev/stdin`

### 2. 프론트엔드

```bash
cd frontend
npm install
npm run dev                         # http://localhost:5173 (API는 Vite proxy로 localhost:8000 전달)
VITE_AUTH_MOCK=true npm run dev     # 백엔드 없이 화면 확인 (admin01 / worker01, 비밀번호 아무거나)
npm run dev:mobile                  # 같은 Wi-Fi의 휴대폰에서 터미널의 Network 주소로 접속
```

- 백엔드 CORS가 `localhost:5173`만 허용하므로 5173 포트가 사용 중이면 dev 서버가 시작되지 않는다 (`lsof -iTCP:5173 -sTCP:LISTEN`으로 확인).

### 3. 테스트

```bash
python -m unittest discover -s backend -t .   # 백엔드 (저장소 루트)
cd frontend && npm test && npm run build      # 프론트
```

PR마다 GitHub Actions(CI)가 위 테스트와 빌드를 자동으로 실행한다.

---

## 문서

- [docs/20260407_PPE.md](docs/20260407_PPE.md) — 시스템 전체 설계
- [docs/20260411_Dashboard.md](docs/20260411_Dashboard.md) — 대시보드 프론트엔드 설계
- [docs/20260504_ServiceScope_Legal.md](docs/20260504_ServiceScope_Legal.md) — 서비스 범위 및 운영 원칙
- [docs/20261003_Auth_Broadcast_Setup.md](docs/20261003_Auth_Broadcast_Setup.md) — 인증·현장 PC 방송 설정
- [docs/20261004_Media_Privacy_Setup.md](docs/20261004_Media_Privacy_Setup.md) — 얼굴 비식별화·자료 보관
- [docs/20261005_Zone_Equipment_PPE_Policy.md](docs/20261005_Zone_Equipment_PPE_Policy.md) — 구역·장비별 PPE 정책

---

## 브랜치 전략

- `main` — 통합 브랜치 (직접 push 금지)
- `Feat/<description>` — 기능 구현
- `Fix/<description>` — 버그 수정
- `Docs/<description>` — 문서 작업
- `Chore/<description>` — 설정·환경 작업
