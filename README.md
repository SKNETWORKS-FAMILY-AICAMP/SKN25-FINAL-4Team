# EMS Agent — 대화형 에너지 분석 AI 플랫폼

SK Networks AI Family 25기 4팀 파이널 프로젝트.  
공장 운영자가 자연어 질문만으로 에너지 데이터를 분석하고, 이상탐지·예측·KPI 보고서를 자동으로 산출하는 멀티 에이전트 시스템.

- **데이터**: Honda R&D Europe GmbH (독일 오펜바흐) — 81개 계량기, 2018~2024년
- **GitHub**: https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN25-FINAL-4Team

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| 💬 **대화형 질의** | 자연어로 에너지 데이터 질문 → LLM 답변 (OpenAI / Anthropic / Gemini 선택 가능) |
| 🚨 **이상탐지** | Isolation Forest + VMD-LSTM Residual 앙상블 (HIGH / MEDIUM / LOW) |
| 📈 **전력 예측** | Prophet · XGBoost · LSTM · VMD-LSTM 모델 비교 및 백테스트 |
| 📄 **KPI 보고서** | 월간 에너지 KPI 자동 생성 + PDF 출력 |
| 🔌 **계량기 토폴로지** | 81개 미터 에너지 흐름 시각화 (건물별 탭, Sankey 차트) |

---

## 시스템 구성

```
사용자 질문
    ↓
Orchestrator (의도 분류)
    ├── anomaly  → 이상탐지 Agent  → Critic → 답변
    ├── forecast → 예측 Agent      → Critic → 답변
    ├── report   → 보고서 Agent    → Critic → 답변 + PDF
    └── rag      → RAG Agent       → Critic → 답변

FastAPI (포트 8000)  ←→  React 대시보드 (Docker: 8080)
PostgreSQL + TimescaleDB + pgvector
```

---

## 사전 요구사항

- Docker 23.0 이상 + Docker Compose v2 (권장)
- 또는 Python 3.11 이상 + Node.js 18 이상 (로컬 직접 실행 시)
- PostgreSQL 접속 정보 (팀 NAS 서버)
- LLM API 키 (OpenAI / Anthropic / Gemini 중 하나)

---

## 설치 및 실행

### 1. 레포 클론 & 브랜치 전환

```bash
git clone https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN25-FINAL-4Team.git
cd SKN25-FINAL-4Team
git checkout app/ems-agent
```

### 2. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어 DB 접속 정보와 LLM API 키를 입력합니다:

```env
# DB
DB_HOST=YOUR_DB_HOST
DB_PORT=5432
DB_USER=YOUR_DB_USER
DB_PASSWORD=YOUR_DB_PASSWORD
DB_NAME=YOUR_DB_NAME
DATABASE_URL=postgresql://YOUR_DB_USER:YOUR_DB_PASSWORD@YOUR_DB_HOST:5432/YOUR_DB_NAME

# LLM 프로바이더 (openai | anthropic | gemini 중 선택)
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o

# 선택한 프로바이더의 키만 입력
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
```

---

## 실행 방법 A — Docker Compose (권장)

```bash
docker compose up --build
```

| 서비스 | 주소 |
|--------|------|
| 프론트엔드 | http://localhost:8080 |
| 백엔드 API | http://localhost:8000 |
| API 문서 (Swagger) | http://localhost:8000/docs |
| 헬스체크 | http://localhost:8000/health |

> **재빌드 시 속도**: BuildKit 캐시 마운트가 적용되어 있어, requirements가 변경되지 않으면 pip/npm 설치를 스킵합니다.

---

## 실행 방법 B — 로컬 직접 실행 (개발용)

### 백엔드

```bash
cd backend
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.backend.txt

cd src
uvicorn api.main:app --reload --port 8000
```

### 프론트엔드

```bash
cd frontend
npm install
npm run dev                      # http://localhost:5173
```

---

## API 엔드포인트

### 채팅
| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/chat` | 자연어 질의 (LangGraph 오케스트레이터) |

### 이상탐지
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/anomalies` | 이상탐지 결과 목록 (필터: severity, year, month) |
| GET | `/anomalies/summary` | 심각도별 건수 요약 |
| GET | `/anomalies/timeline` | 월별 이상탐지 추이 |
| GET | `/anomalies/types` | 이상 유형별 통계 |
| GET | `/anomalies/{id}/context` | 특정 이상 전후 컨텍스트 |
| POST | `/anomalies/run` | 백그라운드 이상탐지 실행 |
| GET | `/anomalies/run/status/{job_id}` | 실행 상태 조회 |

### 예측
| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/forecast/train/{model}` | 모델 학습 (prophet / xgboost / lstm / vmd_lstm) |
| GET | `/forecast/train/status` | 학습 상태 조회 |
| GET | `/forecast/predict/{model}` | 단일 모델 예측 |
| GET | `/forecast/compare` | 전체 모델 비교 예측 |
| GET | `/forecast/backtest` | 백테스트 (train_end, test_end, freq) |

### 보고서
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/report` | KPI 보고서 조회 + PDF 생성 |
| POST | `/report/aggregate` | 보고서 데이터 재집계 |

---

## 예측 모델 학습

예측 탭을 처음 사용하기 전에 모델을 학습시켜야 합니다.  
대시보드 → 예측 탭 → "전체 학습" 버튼을 클릭하거나 API로 직접 호출:

```bash
# prophet, xgboost, lstm 순차 학습
curl -X POST "http://localhost:8000/forecast/train/prophet"
curl -X POST "http://localhost:8000/forecast/train/xgboost"
curl -X POST "http://localhost:8000/forecast/train/lstm"
```

---

## 프로젝트 구조

```
SKN25-FINAL-4Team/
├── backend/
│   ├── Dockerfile
│   ├── requirements.backend.txt
│   ├── scripts/
│   │   └── ingest/sql/
│   │       └── reduced_view.sql      # TimescaleDB 뷰 (ZE/Z 미터 정규화)
│   └── src/
│       ├── agents/                   # LangGraph 멀티 에이전트
│       │   ├── orchestrator.py       # 의도 분류 + 라우팅
│       │   ├── anomaly_agent.py      # 이상탐지 해석
│       │   ├── forecast_agent.py     # 전력 예측
│       │   ├── reporting_agent.py    # KPI 보고서 + PDF
│       │   ├── rag_agent.py          # 프롬프트 주입형 RAG
│       │   └── state.py              # 공유 AgentState
│       ├── api/
│       │   ├── main.py               # FastAPI 진입점
│       │   ├── db.py                 # psycopg2 커넥션 풀
│       │   └── routers/              # chat / anomalies / forecast / report
│       ├── data/
│       │   └── loader.py             # DB 데이터 로더
│       ├── knowledge/                # 도메인 지식 및 메타데이터
│       │   ├── domain_knowledge.py   # 시스템 프롬프트용 상수
│       │   ├── embedding.py          # 문서 검색용 벡터 임베딩
│       │   └── meter_metadata.json   # 81개 미터 및 설비 그룹 정보
│       └── models/
│           ├── anomaly/
│           │   ├── ensemble.py       # Isolation Forest + Residual 앙상블
│           │   └── residual_model.py # VMD-LSTM 잔차 기반 이상탐지
│           └── forecasting/
│               ├── prophet_model.py
│               ├── xgboost_model.py
│               ├── lstm_model.py
│               └── vmd_lstm_model.py # VMD + LSTM + Attention
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf
│   └── src/
│       ├── components/
│       │   ├── DashboardPanel.jsx
│       │   ├── ChatPanel.jsx
│       │   ├── AnomalyPanel.jsx
│       │   ├── ForecastPanel.jsx
│       │   ├── ReportPanel.jsx
│       │   └── TopologyPanel.jsx     # 81개 미터 에너지 흐름 시각화
│       ├── data/
│       │   └── meterCatalog.js       # 계량기 메타데이터 (Gruner et al. 2025)
│       └── api/
│           └── client.js             # Axios API 클라이언트
├── .env.example
└── docker-compose.yml
```

---

## 문의

팀원 간 Discord 또는 Notion 프로젝트 채널 참고.
