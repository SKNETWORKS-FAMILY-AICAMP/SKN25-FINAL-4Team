# EMS Agent — 대화형 에너지 분석 AI 플랫폼

SK Networks AI Family 25기 4팀 파이널 프로젝트.  
공장 운영자가 자연어 질문만으로 에너지 데이터를 분석하고, 이상탐지·예측·KPI 보고서를 자동으로 산출하는 멀티 에이전트 시스템.

- **데이터**: Honda R&D Europe GmbH (독일 오펜바흐) — 81개 계량기, 2018~2024년
- **GitHub**: https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN25-FINAL-4Team

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| 💬 **대화형 질의** | 자연어로 에너지 데이터 질문 → GPT-4o 답변 |
| 🚨 **이상탐지** | Z-score·Isolation Forest·LSTM 앙상블 (HIGH/MEDIUM/LOW) |
| 📈 **전력 예측** | Prophet·XGBoost 기반 단기 예측 (1일~1주) |
| 📄 **KPI 보고서** | 월간 에너지 KPI 자동 생성 + PDF 출력 |
| 🗺️ **계량기 토폴로지** | 81개 미터 에너지 흐름 시각화 |

---

## 시스템 구성

```
사용자 질문
    ↓
Orchestrator (의도 분류)
    ├── anomaly  → 이상탐지 Agent → Critic → 답변
    ├── forecast → 예측 Agent    → Critic → 답변
    ├── report   → 보고서 Agent  → Critic → 답변 + PDF
    └── rag      → RAG Agent    → Critic → 답변

FastAPI (포트 8000)  ←→  React 대시보드 (포트 5173 / Docker: 80)
PostgreSQL + TimescaleDB + pgvector
```

---

## 사전 요구사항

- Python 3.11 이상
- Node.js 18 이상 (프론트엔드 로컬 실행 시)
- PostgreSQL 접속 정보 (팀 NAS 서버)
- OpenAI API 키 ([platform.openai.com/api-keys](https://platform.openai.com/api-keys) 에서 발급)

---

## 설치 및 실행

### 1. 레포 클론 & 브랜치 전환

```bash
git clone https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN25-FINAL-4Team.git
cd SKN25-FINAL-4Team
git checkout keun/ems-agent
```

### 2. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어 팀에서 공유받은 DB 접속 정보와 본인 OpenAI API 키를 입력합니다:

```env
DB_HOST=121.134.46.24
DB_PORT=5432
DB_USER=           # 팀 공유 정보 입력
DB_PASSWORD=       # 팀 공유 정보 입력
DB_NAME=SKN25
DATABASE_URL=postgresql://USER:PASSWORD@121.134.46.24:5432/SKN25  # USER·PASSWORD 교체

OPENAI_API_KEY=    # https://platform.openai.com/api-keys 에서 발급
```

---

## 실행 방법 A — 로컬 직접 실행 (개발용)

### 백엔드

```bash
# 가상환경 생성 (최초 1회)
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 패키지 설치
pip install -r requirements.txt

# 서버 실행 (src 디렉터리에서 실행해야 함)
cd src
uvicorn api.main:app --reload --port 8000
```

백엔드 정상 실행 확인: http://localhost:8000/health

### 프론트엔드

```bash
cd frontend
npm install
npm run dev
```

브라우저에서 http://localhost:5173 접속

---

## 실행 방법 B — Docker Compose (권장)

```bash
# 루트 디렉터리에서
docker compose up --build
```

| 서비스 | 주소 |
|--------|------|
| 프론트엔드 | http://localhost |
| 백엔드 API | http://localhost/api |
| API 문서 | http://localhost/api/docs |

---

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/chat` | 자연어 질의 (메인 에이전트) |
| GET | `/anomalies` | 이상탐지 결과 조회 |
| GET | `/anomalies/summary` | 심각도별 건수 요약 |
| GET | `/anomalies/timeline` | 월별 이상탐지 추이 |
| POST | `/anomalies/run` | 이상탐지 백그라운드 실행 |
| GET | `/report` | KPI 보고서 조회 |
| GET | `/forecast` | 전력 예측 결과 |
| POST | `/forecast/train` | 예측 모델 학습 |

---

## 이상탐지 실행 방법

대시보드 → 이상탐지 탭에서 기간 설정 후 실행하거나, API로 직접 호출:

```bash
# 특정 기간 이상탐지 실행 (백그라운드)
curl -X POST "http://localhost:8000/anomalies/run?start=2022-07-01&end=2022-08-01"

# 실행 상태 확인 (응답의 job_id 사용)
curl "http://localhost:8000/anomalies/run/status/{job_id}"
```

---

## 예측 모델 학습

예측 기능을 사용하려면 먼저 모델을 학습시켜야 합니다:

```bash
curl -X POST "http://localhost:8000/forecast/train"
```

학습 완료 후 대시보드 예측 탭에서 사용 가능합니다.

---

## 프로젝트 구조

```
final/
├── src/
│   ├── agents/          # LangGraph 에이전트
│   │   ├── orchestrator.py   # 의도 분류 + 라우팅
│   │   ├── anomaly_agent.py  # 이상탐지 해석
│   │   ├── forecast_agent.py # 전력 예측
│   │   ├── reporting_agent.py# KPI 보고서 + PDF
│   │   ├── rag_agent.py      # 온톨로지 RAG
│   │   └── state.py          # 공유 상태 정의
│   ├── api/             # FastAPI 백엔드
│   ├── data/            # DB 로더
│   ├── models/
│   │   ├── anomaly/     # 이상탐지 모델 (통계·IsoForest·LSTM)
│   │   └── forecasting/ # 예측 모델 (Prophet·XGBoost·LSTM)
│   └── knowledge/       # 에너지 도메인 온톨로지 (OWL)
├── frontend/            # React 대시보드
├── .env.example         # 환경변수 템플릿
├── docker-compose.yml
└── requirements.backend.txt
```

---

## 문의

팀원 간 Discord 또는 Notion 프로젝트 채널 참고.
