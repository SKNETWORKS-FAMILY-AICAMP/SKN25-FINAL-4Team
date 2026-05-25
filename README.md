# EMS Agent — 전력 수요 예측 AI 플랫폼

SK Networks AI Family 25기 4팀 파이널 프로젝트.  
공장 운영자를 위한 **전력 수요 예측** 중심의 에너지 분석 시스템.  
다중 모델(Prophet·XGBoost·LSTM·VMD-LSTM)로 수요를 예측하고, 이를 기준으로 이상탐지·KPI 보고서를 보조 산출하며, 자연어 질의로 결과를 해석한다.

- **데이터**: Honda R&D Europe GmbH (독일 오펜바흐) — 81개 계량기, 2018~2024년
- **GitHub**: https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN25-FINAL-4Team

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| 📈 **전력 수요 예측** *(주력)* | Prophet · XGBoost · LSTM · VMD-LSTM 4종 다중 모델 예측 · 모델 비교 · 백테스트 검증 |
| 💬 **대화형 질의** | 자연어로 예측·에너지 데이터 질문 → LLM 답변 (OpenAI / Anthropic / Gemini 선택 가능) |
| 📄 **KPI 보고서** | 월간 에너지 KPI 자동 생성 + PDF 출력 · 냉방-외기온 상관 차트 |
| 📅 **일일 보고서** | 하루 단위 KPI + 시간대별 전력 프로파일 + AI 요약 · 매일 자동 생성 스케줄러 |
| 🚨 **이상탐지** *(보조)* | 예측 잔차(VMD-LSTM) + Isolation Forest 앙상블 기반 이상 진단 (HIGH / MEDIUM / LOW) |
| 🔌 **계량기 토폴로지** | 81개 미터 에너지 흐름 시각화 · 전력 집계 구조도 (건물별 탭) |

---

## 시스템 구성

```
사용자 질문
    ↓
Orchestrator (키워드 룰 → LLM 폴백으로 의도 분류)
    ├── forecast → 예측 Agent     → 답변   ◀ 주력
    ├── report   → 보고서 Agent   → 답변 + PDF
    ├── anomaly  → 이상탐지 Agent → 답변   (보조)
    └── rag      → RAG Agent      → 답변
    ↓
Critic (용어 교정 — 문자열 치환, LLM 미사용)

FastAPI (포트 8000)  ←→  React 대시보드 (Docker: 8080)
PostgreSQL + TimescaleDB + pgvector
```

> **응답 속도**: 의도 분류를 키워드 룰로 처리하고 Critic을 문자열 치환으로 대체해  
> LLM 호출을 질문당 최대 3회 → 1~2회로 감소.

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
docker compose up --build -d
```

| 서비스 | 주소 |
|--------|------|
| 프론트엔드 | http://localhost:8080 |
| 백엔드 API | http://localhost:8000 |
| API 문서 (Swagger) | http://localhost:8000/docs |
| 헬스체크 | http://localhost:8000/health |

> **빌드 시간**: BuildKit 캐시 마운트 + CPU-only torch 적용으로 첫 빌드 약 5~8분,  
> 이후 재빌드(소스 변경만)는 수십 초 내외.

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
| GET | `/anomalies/events` | Regime 이벤트 + 게이트웨이 장애 구간 목록 |
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
| GET | `/report` | 월간 KPI 보고서 조회 + PDF 생성 (cooling_vs_temp 포함) |
| POST | `/report/aggregate` | 월간 보고서 데이터 재집계 |
| GET | `/report/daily` | 일일 보고서 조회 (저장본 없으면 즉시 생성, regenerate로 강제 재생성) |
| POST | `/report/daily/aggregate` | 특정 날짜 일일 보고서 강제 재생성 |
| GET | `/report/daily/list` | 저장된 일일 보고서 목록 |
| GET | `/report/daily/latest-data-date` | 데이터에 존재하는 가장 최근 완전한 날짜 |
| GET | `/report/daily/scheduler` | 자동 생성 스케줄러 상태 (다음 실행·마지막 실행) |
| POST | `/report/daily/scheduler/run` | 스케줄러 작업 즉시 1회 실행 |

> **일일 보고서 자동 생성**: 매일 `DAILY_REPORT_HOUR` 시각(기본 06:00, Europe/Berlin)에  
> 가장 최근 데이터 날짜의 보고서를 자동 생성합니다. `.env`의 `DAILY_REPORT_ENABLED=false`로 끌 수 있습니다.

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
│   ├── Dockerfile                        # CPU-only torch, BuildKit 캐시 마운트
│   ├── requirements.backend.txt
│   ├── scripts/
│   │   └── ingest/sql/
│   │       └── reduced_view.sql          # TimescaleDB 뷰 (ZE/Z 미터 정규화)
│   ├── tests/                            # pytest 테스트
│   │   ├── conftest.py
│   │   ├── test_agents.py
│   │   └── test_api_basic.py
│   └── src/
│       ├── agents/                       # LangGraph 멀티 에이전트
│       │   ├── orchestrator.py           # 의도 분류(키워드 룰+LLM 폴백) + 라우팅
│       │   ├── anomaly_agent.py          # 이상탐지 해석
│       │   ├── forecast_agent.py         # 전력 예측
│       │   ├── reporting_agent.py        # KPI 보고서 + PDF
│       │   ├── rag_agent.py              # 프롬프트 주입형 RAG
│       │   └── state.py                  # 공유 AgentState
│       ├── api/
│       │   ├── main.py                   # FastAPI 진입점 (lifespan에서 스케줄러 기동)
│       │   ├── db.py                     # psycopg2 커넥션 풀
│       │   ├── scheduler.py              # 일일 보고서 자동 생성 (APScheduler)
│       │   └── routers/                  # chat / anomalies / forecast / report
│       ├── data/
│       │   └── loader.py                 # DB 데이터 로더
│       ├── knowledge/                    # 도메인 지식 (온톨로지 대체)
│       │   ├── domain_knowledge.py       # 시스템 프롬프트용 상수
│       │   ├── embedding.py              # 문서 검색용 벡터 임베딩
│       │   └── meter_metadata.json       # 81개 미터 및 설비 그룹 정보
│       └── models/
│           ├── anomaly/
│           │   ├── ensemble.py           # Isolation Forest + Residual 앙상블
│           │   └── residual_model.py     # VMD-LSTM 잔차 기반 이상탐지
│           └── forecasting/
│               ├── prophet_model.py
│               ├── xgboost_model.py
│               ├── lstm_model.py
│               └── vmd_lstm_model.py     # VMD + LSTM + Attention
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf
│   └── src/
│       ├── components/
│       │   ├── DashboardPanel.jsx        # KPI 카드, 에너지 믹스, COP 추이
│       │   ├── ChatPanel.jsx             # 대화형 질의 UI
│       │   ├── AnomalyPanel.jsx          # 이상탐지 결과 + 이벤트 뷰
│       │   ├── ForecastPanel.jsx         # 모델 비교 예측 차트
│       │   ├── ReportPanel.jsx           # 월간 KPI + 냉방-외기온 차트
│       │   ├── DailyReportPanel.jsx      # 일일 KPI + 시간대별 프로파일 + AI 요약
│       │   └── TopologyPanel.jsx         # 에너지 흐름 시각화 + 전력 집계 구조도
│       ├── data/
│       │   └── meterCatalog.js           # 계량기 메타데이터 (Gruner et al. 2025)
│       └── api/
│           └── client.js                 # Axios API 클라이언트
├── .env.example
└── docker-compose.yml
```

---

## 문의

팀원 간 Discord 또는 Notion 프로젝트 채널 참고.
