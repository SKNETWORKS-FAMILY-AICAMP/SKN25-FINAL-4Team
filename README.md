# TTF-FMS — 설비 상태감시·예지보전 AI 코파일럿

SK Networks AI Family 25기 4팀 파이널 프로젝트.

> **포지셔닝** — 기존 FEMS/모니터링 시스템이 "데이터를 보여주는" 도구라면,
> TTF-FMS(Facility Management System)는 **"결정을 도와주는"** AI 코파일럿이다.
> **이상탐지·설비 상태감시·고장 진단·예지보전·정비**를 하나의 LangGraph Agent로 묶고,
> **센서 증설 없이 기존 전기·에너지 계측(전압·전류·역률·전력)** 만으로 설비를 진단한다.

**핵심 차별점** — 경쟁사(진동/다센서 기반 예지보전)는 물리 센서 증설이 전제다.
우리는 이미 있는 전기·성능 데이터에 **LLM 진단 코파일럿**을 얹어, *왜 이상한지·무엇을 할지·언제 고장날지* 를 설명한다.

- **데이터**: Honda R&D Europe GmbH (독일 오펜바흐) — 다수 계량기(전압/전류/역률/전력 등), 2017~2024년
- **GitHub**: https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN25-FINAL-4Team (브랜치 `app/ems-agent`)

---

## 주요 기능

### 핵심 — CMS(설비 상태감시)
| 기능 | 설명 |
|------|------|
| 🏭 **설비 상태 감시** | 설비별 헬스 스코어(노출시간 정규화) + 상태등급 · 현재 전력/COP 지표 · 최근 이상 요약 |
| 🩺 **AI 고장 원인 진단** | LLM이 이상 이력 + **전기 시그니처(3상 불평형·역률·주파수)** + 도메인 지식으로 원인·근거·조치 생성 |
| 🔮 **예지보전** | COP·이상 발생률 **추세 외삽**으로 "현 추세면 N개월 후 기준치 도달" 위험 예측 (추세 기반·참고용) |
| 🔧 **정비 작업지시** | 진단 → 작업지시 생성 → 진행/완료 칸반 → 조치 결과 기록 (이력 루프) |
| 🚨 **이상탐지** | 2경로 — ① VMD-LSTM 예측 잔차 + Isolation Forest(주력) ② 통계·IF·LSTM-AE 3단 투표(폴백) · HIGH/MEDIUM/LOW |

### AI 코파일럿
| 기능 | 설명 |
|------|------|
| 💬 **대화형 + 에이전트** | 자연어 질의(LangGraph) + **실제 행동**(작업지시 생성·시뮬 제어 실행) · 현재 보던 설비 **컨텍스트 자동 인지** · 알림 → 자동 분석 |
| 🧠 **적응형 학습** | 권고의 과거 성공/실패(outcome)로 우선순위 재랭킹 + 신뢰도 표시 |

### 운영 보조 (에너지)
| 기능 | 설명 |
|------|------|
| 📈 **수요 예측** | VMD-LSTM(주력 사전학습) + XGBoost(폴백/백테스트) · 모델 비교 |
| ⚡ **제어 및 최적화** | 피크 시프트·야간부하·효율 권고 (승인/거부 + 적응형 학습) |
| 💰 **목표 요금 관리** | 월말 요금 추정 · 피크 위험 모니터링 |
| 📄 **보고서** | 월간 KPI(YoY·MoM, 냉방-외기온 상관) + 일일 운영 브리핑(PDF/DOCX/HWPX) |
| 🔌 **계량기 토폴로지** | 미터 에너지 흐름·집계 구조 시각화 |

---

## 시스템 구성

```
사용자 질문
    ↓
Orchestrator (키워드 룰 → LLM 폴백으로 의도 분류)
    ├── cms      → CMS Agent      → 설비 상태/진단/예지보전/작업지시 (+ 행동 실행)  ◀ 주력
    ├── anomaly  → 이상탐지 Agent → 답변
    ├── report   → 보고서 Agent   → 답변 + PDF
    ├── forecast → 예측 Agent     → 답변 (보조)
    └── rag      → RAG Agent      → pgvector 검색 + 답변
    ↓
Critic (용어 교정 — 문자열 치환)

FastAPI (8000)  ←→  React 대시보드 (Docker: 8080, 라이트 엔터프라이즈 UI)
PostgreSQL + TimescaleDB + pgvector
시뮬레이터: 가상 시계가 과거 데이터를 "실시간"으로 재생 → 워커가 이상 자동 탐지·알림
```

> **안정화 설계** — 모든 동기 라우터를 `def`로 두어 FastAPI 스레드풀에서 병렬 처리(이벤트 루프 블록 방지),
> DB 커넥션 풀(2~25)+반환 시 rollback, DDL 1회 실행(락 데드락 방지). 의도 분류는 키워드 룰 우선으로 LLM 호출 절감.

> **팀 분담** — ML 모델(VMD-LSTM 예측, 잔차+IF 이상탐지)은 팀원 담당. 인터페이스 계약은 [docs/ML_INTERFACE.md](docs/ML_INTERFACE.md) 참고.

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

`.env`에 DB 접속 정보와 LLM API 키를 입력합니다:

```env
DB_HOST=YOUR_DB_HOST
DB_PORT=5432
DB_USER=YOUR_DB_USER
DB_PASSWORD=YOUR_DB_PASSWORD
DB_NAME=YOUR_DB_NAME
DATABASE_URL=postgresql://YOUR_DB_USER:YOUR_DB_PASSWORD@YOUR_DB_HOST:5432/YOUR_DB_NAME

# LLM 프로바이더 (openai | anthropic | gemini)
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
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

> 첫 빌드 약 5~8분(CPU-only torch + BuildKit 캐시), 이후 재빌드는 수십 초.

---

## 실행 방법 B — 로컬 직접 실행 (개발용)

### 백엔드
```bash
cd backend
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.backend.txt
cd src && uvicorn api.main:app --reload --port 8000
```

### 프론트엔드
```bash
cd frontend
npm install
npm run dev                      # http://localhost:5173
```

---

## 데모 시나리오 (라이브)

```
1. 시뮬레이터 시계를 2023-02-08 로 시크 → 6× 속도 → 시작
   (또는 챗봇에 "시뮬레이터 2023-02-08로 가서 시작해줘")
2. ~15초 내 HIGH 이상 토스트 발생 (CHP 정지·COP 급락 다발 구간)
3. 토스트 "AI 분석" → 챗봇이 해당 이상을 자동 분석
4. 설비 상태 감시 → 카드 클릭 → AI 진단(전기 시그니처 근거) → 작업지시 생성
5. 정비 작업지시 탭에서 진행/완료, 제어 탭에서 적응형 학습 확인
```
> 데모 초기화: 대화 내역 🗑전체삭제 / 제어 🗑이력초기화 버튼.

---

## API 엔드포인트 (요약)

### CMS (설비)
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/cms/equipment` | 설비별 헬스 스코어·상태·현재 지표 |
| GET | `/cms/equipment/{id}/diagnose` | LLM 고장 원인 진단 (전기 시그니처 포함) |
| GET | `/cms/predictive` | 추세 기반 예지보전 위험 예측 |
| POST/GET | `/cms/work-orders` | 정비 작업지시 생성/목록 |
| POST | `/cms/work-orders/{id}/status` | 작업지시 상태 전환(open→in_progress→done) |

### 채팅 / 제어 / 시뮬레이터
| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/chat`, `/chat/stream` | 자연어 질의(컨텍스트·행동 지원), SSE 스트리밍 |
| GET/DELETE | `/chat/sessions` | 대화 세션 목록 / 전체 삭제 |
| GET/DELETE | `/control/recommendations` | 운영 권고 조회 / 이력 초기화 |
| POST | `/control/recommendations/{id}/approve\|reject` | 권고 승인/거부 |
| GET | `/control/learning-stats` | 적응형 학습 통계 |
| POST | `/simulator/{start\|pause\|reset\|seek\|speed}` | 가상 시계 제어 |
| GET | `/notifications/stream` | 실시간 알림 SSE |

### 이상탐지 / 예측 / 보고서
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/anomalies`, `/anomalies/summary`, `/anomalies/events` | 결과 목록(유형 필터)·요약·이벤트 |
| POST/GET | `/anomalies/run`, `/anomalies/run/status/{job}` | 백그라운드 탐지 실행/상태 |
| GET | `/forecast/compare`, `/forecast/predict/{model}`, `/forecast/backtest` | 예측 비교/단일/백테스트 |
| GET | `/report`, `/report/daily`, `/report/billing` | 월간/일일/요금 보고서 |

> **이상탐지 2경로** — `/anomalies/run` 실행 시 모델 파일 유무로 자동 분기:
> - **주력**: VMD-LSTM 예측 **잔차(\|실제−예측\|)** 임계 초과 + IsolationForest 합산. 둘 다=HIGH, 잔차 1.5배 초과=MEDIUM, 한쪽=LOW.
> - **폴백**: 통계(Z·IQR·STL) + IsolationForest + LSTM-AE **3단 투표**. 2표↑=HIGH, 1표=MEDIUM.
> - 공통: 유형(COPDrop·CHPOutage·NightConsumption·PVNightNonZero·PowerSpike) 분류 후 `anomaly_results` 저장.
> - **CMS 연동**: 모든 CMS 기능(헬스·진단·예지보전·작업지시)이 `anomaly_results`를 소비 → 이상탐지 모델 교체 시 CMS 전체가 자동 개선.

---

## 프로젝트 구조

```
SKN25-FINAL-4Team/
├── docs/
│   └── ML_INTERFACE.md                 # 팀 ML 모델 인터페이스 계약
├── backend/src/
│   ├── agents/                         # LangGraph 멀티 에이전트
│   │   ├── orchestrator.py             # 의도 분류(cms/anomaly/report/forecast/rag) + 라우팅
│   │   ├── cms_agent.py                # 설비 상태/진단/예지보전/작업지시 + 행동 실행
│   │   ├── anomaly_agent.py · forecast_agent.py · reporting_agent.py · rag_agent.py
│   │   └── state.py                    # 공유 AgentState (context 포함)
│   ├── api/
│   │   ├── main.py · db.py(풀+rollback) · scheduler.py · report_export.py
│   │   └── routers/                    # cms · control · simulator · notifications
│   │   │                               #  + chat · anomalies · forecast · report
│   ├── data/loader.py                  # ems 스키마 로더 (전기 계측 포함)
│   ├── knowledge/                      # domain_knowledge · embedding(pgvector) · meter_metadata
│   └── models/
│       ├── anomaly/   residual_model(주력) · ensemble/statistical/isolation/lstm_ae(폴백)
│       └── forecasting/ vmd_lstm_model(주력) · xgboost_model · (prophet/lstm)
├── frontend/src/
│   ├── theme.js · EquipIcon.jsx        # 라이트 엔터프라이즈 디자인 토큰 + 설비 아이콘
│   ├── App.jsx                         # 셸(사이드바·탑바, lucide 아이콘)
│   └── components/
│       ├── DashboardPanel · EquipmentPanel · MaintenancePanel   # CMS 핵심
│       ├── AnomalyPanel · ControlPanel · ForecastPanel · BillingPanel · ReportPanel
│       ├── ChatPanel · ChatWorkspacePanel(AI 대화) · SimulatorClock
│       └── TopologyPanel · SettingsPanel · UsersPanel
├── .env.example
└── docker-compose.yml
```

---

## 기술 스택

- **백엔드**: FastAPI · LangGraph · psycopg2 · APScheduler
- **DB**: PostgreSQL + TimescaleDB + pgvector
- **프론트**: React (Vite) · Recharts · lucide-react · react-markdown
- **LLM**: OpenAI / Anthropic / Gemini (env 전환) — 최종 단계에 Ollama(sLLM) 전환 예정(데이터 보안)
- **ML(팀)**: VMD-LSTM(예측) · 잔차+IsolationForest(이상탐지)

---

## 문의

팀원 간 Discord 또는 Notion 프로젝트 채널 참고.
