# EMS AI 에이전트 — 설비 상태감시·예지보전 AI 코파일럿

SK Networks AI Family 25기 4팀 파이널 프로젝트.

> **포지셔닝** — 기존 FEMS/모니터링 시스템이 "데이터를 보여주는" 도구라면,
> 이 시스템은 **"결정을 도와주는"** AI 코파일럿이다.
> **이상탐지·설비 상태감시·고장 진단·예지보전·정비**를 하나의 LangGraph Agent로 묶고,
> **센서 증설 없이 기존 전기·에너지 계측(전압·전류·역률·전력)** 만으로 설비를 진단한다.

**핵심 차별점** — 경쟁사(진동/다센서 기반 예지보전)는 물리 센서 증설이 전제다.
우리는 이미 있는 전기·성능 데이터에 **LLM 진단 코파일럿**을 얹어, *왜 이상한지·무엇을 할지·언제 고장날지* 를 설명한다.

- **데이터**: Honda R&D Europe GmbH (독일 오펜바흐) — 81개 계량기(전압/전류/역률/전력 등), 2018~2024년
- **GitHub**: https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN25-FINAL-4Team

---

## 주요 기능

### 핵심 — CMS(설비 상태감시)
| 기능 | 설명 |
|------|------|
| <img src=".github/assets/readme/factory.svg" width="16" alt="" /> **설비 상태 감시** | 설비별 헬스 스코어(노출시간 정규화) + 상태등급 · 현재 전력/COP 지표 · 최근 이상 요약 |
| <img src=".github/assets/readme/stethoscope.svg" width="16" alt="" /> **AI 고장 원인 진단** | LLM이 이상 이력 + **전기 시그니처(3상 불평형·역률·주파수)** + 도메인 지식으로 원인·근거·조치 생성 |
| <img src=".github/assets/readme/telescope.svg" width="16" alt="" /> **예지보전** | COP·이상 발생률 **추세 외삽**으로 "현 추세면 N개월 후 기준치 도달" 위험 예측 |
| <img src=".github/assets/readme/wrench.svg" width="16" alt="" /> **정비 작업지시** | 진단 → 작업지시 생성 → 진행/완료 칸반 → 조치 결과 기록 (이력 루프) |
| <img src=".github/assets/readme/triangle-alert.svg" width="16" alt="" /> **이상탐지** | 2경로 — ① LSTM 잔차 비율 기반(주력, ratio=\|실제−예측\|/threshold, ≥2.0=HIGH) ② 통계·IF·LSTM-AE 3단 투표(폴백) |

### AI 코파일럿
| 기능 | 설명 |
|------|------|
| <img src=".github/assets/readme/message-square.svg" width="16" alt="" /> **대화형 + 에이전트** | 자연어 질의(LangGraph) + **실제 행동**(작업지시 생성·시뮬 제어 실행) · 현재 보던 설비 **컨텍스트 자동 인지** |
| <img src=".github/assets/readme/brain.svg" width="16" alt="" /> **적응형 학습** | 권고의 과거 성공/실패(outcome)로 우선순위 재랭킹 + 신뢰도 표시 |

### 운영 보조 (에너지)
| 기능 | 설명 |
|------|------|
| <img src=".github/assets/readme/chart-no-axes-combined.svg" width="16" alt="" /> **수요 예측** | v84 앙상블 (LSTM×6 median + CatBoost + LightGBM + Ridge + Naive, 계량기별 개인화 45개, shrunk bias correction) |
| <img src=".github/assets/readme/sliders-horizontal.svg" width="16" alt="" /> **제어 및 최적화** | 피크 시프트·야간부하·효율 권고 (승인/거부 + 적응형 학습) |
| <img src=".github/assets/readme/wallet-cards.svg" width="16" alt="" /> **목표 요금 관리** | 월말 요금 추정 · 피크 위험 모니터링 |
| <img src=".github/assets/readme/file-text.svg" width="16" alt="" /> **보고서** | 월간 KPI(YoY·MoM) + 일일 운영 브리핑(PDF/DOCX/HWPX) |
| <img src=".github/assets/readme/network.svg" width="16" alt="" /> **계량기 토폴로지** | 미터 에너지 흐름·집계 구조 시각화 |

---

## 시스템 구성

```
사용자 질문
    ↓
Orchestrator (키워드 룰 → LLM 폴백으로 의도 분류, 정확도 92%)
    ├── cms      → CMS Agent      → 설비 상태/진단/예지보전/작업지시 (+ 행동 실행)  ◀ 주력
    ├── anomaly  → 이상탐지 Agent → 답변
    ├── report   → 보고서 Agent   → 답변 + PDF
    ├── forecast → 예측 Agent     → v84 앙상블 추론 (계량기별)
    └── rag      → RAG Agent      → pgvector 검색 + 답변
    ↓
Critic (용어 교정 — 문자열 치환)

FastAPI (8000)  ←→  React 대시보드 (Docker: 8080)
PostgreSQL(TimescaleDB) + pgvector  ←  reference.corrected_resampled_1h
시뮬레이터: 가상 시계가 과거 데이터를 "실시간"으로 재생 → 워커가 이상 자동 탐지·알림
```

---

## 사전 요구사항

- Docker 23.0 이상 + Docker Compose v2 (권장)
- 또는 Python 3.11 이상 + Node.js 18 이상 (로컬 직접 실행 시)
- PostgreSQL 접속 정보 (팀 서버: 13.209.98.228 / cms DB)
- Ollama 엔드포인트 (RunPod 또는 로컬), 또는 OpenAI / Anthropic / Gemini API 키

---

## 설치 및 실행

### 1. 레포 클론

```bash
git clone https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN25-FINAL-4Team.git
cd SKN25-FINAL-4Team
```

### 2. 환경변수 설정

```bash
cp .env.example .env
```

`.env`에 DB 접속 정보와 LLM 설정을 입력합니다:

```env
DB_HOST=13.209.98.228
DB_PORT=5432
DB_USER=cms
DB_PASSWORD=YOUR_DB_PASSWORD
DB_NAME=cms
DATABASE_URL=postgresql://cms:YOUR_DB_PASSWORD%40@13.209.98.228:5432/cms

# LLM 프로바이더 (ollama | openai | anthropic | gemini)
LLM_PROVIDER=ollama
LLM_MODEL=gemma4:12b

# Ollama 엔드포인트 (RunPod 원격 또는 로컬)
OLLAMA_URL=https://<runpod-id>.proxy.runpod.net/v1
# OLLAMA_URL=http://localhost:11434/v1
```

> **주의**: DB 비밀번호에 `@` 등 특수문자가 포함된 경우 `DATABASE_URL`에서 `%40`으로 인코딩하세요.

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

---

## 실행 방법 B — 로컬 직접 실행 (개발용)

### 백엔드
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd src && uvicorn api.main:app --reload --port 8000
```

### 프론트엔드
```bash
cd frontend
npm install
npm run dev    # http://localhost:5173
```

### 테스트
프로젝트 루트에서 실행합니다.

```bash
python -m pytest tests
```

---

## ML 아티팩트 (사전 학습 모델)

예측·이상탐지 모델 artifacts는 Hugging Face Hub([mintmarket/ems-agent-artifacts](https://huggingface.co/datasets/mintmarket/ems-agent-artifacts))에 보관됩니다.

**Docker Compose 실행 시 자동 다운로드**됩니다 — 별도 작업 불필요.
컨테이너 시작 시 artifacts가 없으면 자동으로 받고, 이미 있으면 스킵합니다.

> 로컬 직접 실행 시에는 수동 다운로드 필요:
> ```bash
> .venv/bin/python scripts/download_artifacts.py
> ```

---

## ML 학습 파이프라인 (v84 앙상블)

예측 모델은 별도 Python 환경에서 학습합니다. 학습 후 생성된 artifacts를 백엔드가 로드합니다.

### 환경 세팅 (최초 1회)

```bash
cd backend
python3.12 -m venv .venv-train
.venv-train/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv-train/bin/pip install numpy pandas scikit-learn joblib catboost lightgbm \
    sqlalchemy psycopg2-binary python-dotenv matplotlib
```

> Apple Silicon(M1/M2/M3)은 CPU 전용으로 학습합니다. CatBoost/LightGBM의 MPS OpenMP 충돌 문제로 GPU 가속을 사용하지 않습니다.

### 학습 실행

```bash
# 1시간 예측 (45개 계량기 전체, 병렬 4워커)
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    .venv-train/bin/python -m ml.pipeline.train --horizon 1 --workers 4

# 3시간 예측
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    .venv-train/bin/python -m ml.pipeline.train --horizon 3 --workers 4

# LSTM 재학습 없이 Pass 2(앙상블) 단계만 재시작 (크래시 후 재개)
.venv-train/bin/python -m ml.pipeline.train --horizon 1 --workers 4 --skip-pass1

# 특정 계량기만
.venv-train/bin/python -m ml.pipeline.train --horizon 1 --meters H2.Z66
```

> **`--workers N`** — 병렬 학습 워커 수. OMP 스레드 제한 환경변수를 같이 설정해야 CatBoost/LightGBM segfault를 방지할 수 있습니다 (train.py 내부에서도 자동 설정).
>
> **`--skip-pass1`** — LSTM `.pt` 파일이 이미 있으면 Pass 1(LSTM 학습, ~3시간)을 건너뛰고 Pass 2(CatBoost/LightGBM/Ridge 학습)부터 재시작합니다.

학습 완료 후 `backend/ml/pipeline/artifacts/{1h|3h}/{meter_urn}/` 에 모델 파일이 생성됩니다.
백엔드 `GET /forecast/predict/v84-ensemble?meter_urn=H2.Z66&horizon=1` 로 추론 확인.

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
> 데모 초기화: <img src=".github/assets/readme/trash-2.svg" width="15" alt="" /> 대화 내역 **전체 삭제** / 제어 **이력 초기화** 버튼.

---

## API 엔드포인트 (요약)

### CMS (설비)
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/cms/equipment` | 설비별 헬스 스코어·상태·현재 지표 |
| GET | `/cms/equipment/{id}/diagnose` | LLM 고장 원인 진단 (전기 시그니처 포함) |
| GET | `/cms/predictive` | 추세 기반 예지보전 위험 예측 |
| POST/GET | `/cms/work-orders` | 정비 작업지시 생성/목록 |
| POST | `/cms/work-orders/{id}/status` | 작업지시 상태 전환 |

### 채팅 / 제어 / 시뮬레이터
| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/chat`, `/chat/stream` | 자연어 질의(컨텍스트·행동 지원), SSE 스트리밍 |
| GET/DELETE | `/chat/sessions` | 대화 세션 목록 / 전체 삭제 |
| GET/DELETE | `/control/recommendations` | 운영 권고 조회 / 이력 초기화 |
| POST | `/control/recommendations/{id}/approve\|reject` | 권고 승인/거부 |
| POST | `/simulator/{start\|pause\|reset\|seek\|speed}` | 가상 시계 제어 |
| GET | `/notifications/stream` | 실시간 알림 SSE |

### 시스템 설정 / 사용자 관리
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET/POST | `/settings` | 설정 조회·변경 (LLM·스케줄·알림 임계값) |
| POST | `/settings/test-llm` | LLM 연결 테스트 |
| GET/POST | `/users` | 사용자 목록 / 추가 |
| PATCH/DELETE | `/users/{id}` | 사용자 수정 / 삭제 |

### 이상탐지 / 예측 / 보고서
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/anomalies`, `/anomalies/summary` | 결과 목록(유형 필터)·요약 |
| POST/GET | `/anomalies/run`, `/anomalies/run/status/{job}` | 탐지 실행/상태 |
| GET | `/forecast/models` | 등록 모델 목록 + artifacts 유무 |
| POST | `/forecast/train/v84-ensemble` | 학습 트리거 (`?horizon=1&meters=H2.Z66`) |
| GET | `/forecast/train/status` | 학습 진행 상태 |
| GET | `/forecast/predict/v84-ensemble` | 추론 (`?meter_urn=H2.Z66&horizon=1`) |
| GET | `/report`, `/report/daily`, `/report/billing` | 월간/일일/요금 보고서 |

> **이상탐지 2경로**
> - **주력**: LSTM 잔차 비율 기반. `ratio = |실제−예측| / threshold`. ratio ≥ 2.0=HIGH, ≥ 1.5=MEDIUM, ≥ 1.0=LOW. (IsolationForest 미사용)
> - **폴백** (artifacts 없을 때): 통계(Z·IQR·STL) + IsolationForest + LSTM-AE 3단 투표. 3표=HIGH, 2표=MEDIUM, 1표=LOW.
> - 유형 분류(COPDrop·CHPOutage·NightConsumption·PVNightNonZero·PowerSpike) → `anomaly_results` 저장 → CMS 전체 소비.

---

## 프로젝트 구조

```text
SKN25-FINAL-4Team/
├── backend/                         # FastAPI 및 ML 코드
│   ├── src/
│   │   ├── agents/                  # LangGraph 에이전트
│   │   ├── api/                     # 앱 진입점, 서비스, API 라우터
│   │   ├── data/                    # 데이터 접근
│   │   ├── knowledge/               # RAG 지식 및 임베딩
│   │   └── models/anomaly/          # 이상 탐지 모델
│   ├── ml/pipeline/                 # 예측 학습·추론 파이프라인
│   ├── docs/kb/                     # RAG 지식베이스 문서
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                        # React (Vite) 대시보드
│   └── src/
│       ├── api/                     # 백엔드 API 클라이언트
│       ├── components/
│       │   ├── common/              # 공용 UI 컴포넌트
│       │   └── panels/              # 화면 단위 패널
│       ├── data/                    # 정적 카탈로그
│       ├── App.jsx
│       └── theme.js
├── tests/                           # 백엔드 단위·API 테스트
├── dev/                             # 분석 자료, 평가, 개발 스크립트
│   ├── data/
│   ├── docs/
│   ├── eval/
│   └── scripts/
├── scripts/
│   ├── download_artifacts.py        # HF Hub에서 ML artifacts 다운로드
│   └── upload_artifacts.py          # HF Hub에 ML artifacts 업로드 (관리자용)
├── .env.example
└── docker-compose.yml
```

> `dev/data`, `dev/docs`, `dev/eval`과 생성된 모델 artifacts는 Git에서 제외됩니다.
> artifacts는 Hugging Face Hub(`mintmarket/ems-agent-artifacts`)에 보관 — `scripts/download_artifacts.py`로 받습니다.

---

## 기술 스택

- **백엔드**: FastAPI · LangGraph · psycopg2 · APScheduler
- **DB**: PostgreSQL + TimescaleDB + pgvector (`reference.corrected_resampled_1h`, WeatherStation)
- **프론트**: React (Vite) · Recharts · lucide-react · react-markdown
- **sLLM**: Ollama (`gemma4:12b`) — RunPod GPU 서버 또는 로컬. OpenAI / Anthropic / Gemini로 `.env` 1줄 전환 가능
- **의도 분류**: 키워드 룰 기반 우선 분류 + LLM 폴백 (골든셋 100문항 기준 **92% 정확도**)
- **ML 예측**: v84 앙상블 — LSTM×6 버전 median + CatBoost + LightGBM + Ridge + Seasonal Naive, 잔차 타겟(P(t)−P(t−1)), 45개 계량기 개인화, shrunk bias correction
- **ML 이상탐지**: LSTM 잔차 비율 기반(주력, ratio ≥ 2.0=HIGH) / 통계+IF+LSTM-AE 3단 투표(폴백)

---

## 문의

팀원 간 Discord 또는 Notion 프로젝트 채널 참고.
