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

### DB 초기화

```bash
python3 backend/db/init_db.py
python3 backend/db/check_db.py
```

### 프론트엔드 개발 서버

```bash
cd frontend
npm install
npm run dev
```

### 백엔드 API 서버

```bash
cd backend
uvicorn api.main:app --reload
```

---

## 문서

- [docs/20260407_PPE.md](docs/20260407_PPE.md) — 시스템 전체 설계
- [docs/20260411_Dashboard.md](docs/20260411_Dashboard.md) — 대시보드 프론트엔드 설계
- [docs/20260504_ServiceScope_Legal.md](docs/20260504_ServiceScope_Legal.md) — 서비스 범위 및 운영 원칙

---

## 브랜치 전략

- `main` — 통합 브랜치 (직접 push 금지)
- `Feat/<description>` — 기능 구현
- `Fix/<description>` — 버그 수정
- `Docs/<description>` — 문서 작업
- `Chore/<description>` — 설정·환경 작업
