# EMS Agent — 기술 스택 전체 정리

> SK Networks AI Family 25기 4팀 파이널 프로젝트 기술 스택 문서.
> 각 기술이 **무엇인지**, **이 프로젝트에서 어떻게 쓰였는지**를 중심으로 정리.
> 최종 업데이트: 2026-06-12

---

## 목차

1. [전체 구조 한눈에 보기](#1-전체-구조-한눈에-보기)
2. [백엔드 — FastAPI + LangGraph](#2-백엔드--fastapi--langgraph)
3. [AI 에이전트 — LangGraph 멀티에이전트](#3-ai-에이전트--langgraph-멀티에이전트)
4. [sLLM — EXAONE + Gemma4 듀얼 모델](#4-sllm--exaone--gemma4-듀얼-모델)
5. [RAG — pgvector + sentence-transformers](#5-rag--pgvector--sentence-transformers)
6. [ML 예측 모델 — v84 앙상블](#6-ml-예측-모델--v84-앙상블)
7. [ML 이상탐지 모델](#7-ml-이상탐지-모델)
8. [데이터베이스 — PostgreSQL + TimescaleDB](#8-데이터베이스--postgresql--timescaledb)
9. [프론트엔드 — React + Vite](#9-프론트엔드--react--vite)
10. [인프라 — Docker + RunPod + HF Hub](#10-인프라--docker--runpod--hf-hub)
11. [개발 도구](#11-개발-도구)
12. [버전 요약표](#12-버전-요약표)

---

## 1. 전체 구조 한눈에 보기

```
┌──────────────────────────────────────────────────────────────────┐
│  사용자 브라우저                                                    │
│  React (Vite) — 12개 패널 대시보드 + 로그인 화면 (세션 인증)       │
└────────────────────────┬─────────────────────────────────────────┘
                         │ HTTP / SSE (Server-Sent Events)
┌────────────────────────▼─────────────────────────────────────────┐
│  FastAPI (Python 3.12)  — 11개 라우터                             │
│  ├── LangGraph 멀티에이전트 (6개 노드 + Critic)                    │
│  │   ├── Orchestrator → classify → route                         │
│  │   │   ├── cms / anomaly / report / forecast / rag / off_topic │
│  │   └── LLM 호출 → Ollama (RunPod RTX 3090)                     │
│  │        ├── EXAONE 3.5:7.8B  ← fast 모드 (의도 분류)           │
│  │        └── Gemma4:12B       ← quality 모드 (진단·보고서)        │
│  ├── ML 예측 (v84 앙상블 — LSTM×6 median + CatBoost + LightGBM + Ridge) │
│  ├── ML 이상탐지 (LSTM 잔차 주력 / 통계·IF·LSTM-AE 폴백)          │
│  ├── APScheduler (월 1회 자동 재학습)                              │
│  ├── 시뮬레이터 워커 (가상 시계 + 자동 이상탐지·SSE 알림)          │
│  ├── RAG (pgvector + sentence-transformers)                       │
│  └── 인증 (세션 토큰 기반 로그인, role: admin/operator/viewer)     │
└────────────────────────┬─────────────────────────────────────────┘
                         │ psycopg2 / SQLAlchemy
┌────────────────────────▼─────────────────────────────────────────┐
│  PostgreSQL + TimescaleDB + pgvector (AWS EC2 13.209.98.228)      │
│  Honda R&D Europe — 81개 계량기, 2018~2024년, ~285만 행           │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. 백엔드 — FastAPI + LangGraph

### FastAPI

| 항목 | 내용 |
|------|------|
| **무엇** | Python 비동기 웹 프레임워크. Django보다 빠르고, Flask보다 기능이 많다. |
| **특징** | 타입 힌트 기반 자동 검증, Swagger UI 자동 생성, async/await 지원 |
| **사용 버전** | `>=0.110.0` |

**이 프로젝트에서의 역할:**
- 전체 REST API 서버 (`localhost:8000`)
- SSE(Server-Sent Events)로 채팅 스트리밍 실시간 전송 (`/chat/stream`)
- 실시간 능동형 알림 SSE (`/notifications/stream`) — 시뮬레이터가 이상 감지 시 자동 발송
- 11개 라우터로 기능 분리

```
backend/src/api/routers/
├── chat.py          # 채팅 + SSE 스트리밍 + 세션 관리 (DB 저장)
├── cms.py           # 설비 진단 + 작업지시
├── anomalies.py     # 이상탐지 실행/조회
├── report.py        # 월간/일일 보고서
├── forecast.py      # 예측 API
├── control.py       # 피크 시프트 권고 + 적응형 학습
├── simulator.py     # 가상 시계 + 배경 워커 (이상탐지 자동 실행)
├── notifications.py # SSE 능동형 알림 브로드캐스트
├── settings.py      # LLM 설정
├── users.py         # 사용자 관리
└── auth.py          # 세션 토큰 기반 로그인/로그아웃
```

---

### 인증 (auth.py)

| 항목 | 내용 |
|------|------|
| **방식** | 세션 토큰 기반 (in-memory store, 재시작 시 초기화) |
| **엔드포인트** | `POST /auth/login`, `GET /auth/me`, `POST /auth/logout` |
| **역할** | `admin` · `operator` · `viewer` 3단계 권한 |

현재 프로토타입 구현 — 운영 전환 시 JWT + 영속 세션 + 전 엔드포인트 인가 확장 필요.

---

### 실시간 알림 (notifications.py)

| 항목 | 내용 |
|------|------|
| **방식** | SSE (`/notifications/stream`) — asyncio.Queue 브로드캐스트 |
| **트리거** | 시뮬레이터 워커가 이상 감지 시 자동 발송, `/notifications/demo`로 수동 발송 가능 |
| **연결** | 프론트엔드 알림창에서 EventSource 구독 → "AI 분석" 클릭 시 채팅 자동 질의 |

---

### Uvicorn

| 항목 | 내용 |
|------|------|
| **무엇** | ASGI(비동기 서버 게이트웨이) 서버. FastAPI 앱을 실제로 실행하는 웹 서버. |
| **사용 버전** | `>=0.23.0` |

Docker 컨테이너에서 `uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 1`로 실행.

---

### APScheduler

| 항목 | 내용 |
|------|------|
| **무엇** | Python 내장 스케줄러. Airflow처럼 별도 서버 없이 FastAPI 안에서 크론잡 실행. |
| **사용 버전** | `>=3.10.0` |

**이 프로젝트에서의 역할:**
- 매월 1일 자정 v84 앙상블 자동 재학습 (`ml.pipeline.train --horizon 1` → `--horizon 3` 순차 실행)
- 재학습 로그: `backend/src/logs/auto_train_YYYYMMDD_HHMMSS.log`
- 설정 패널에서 스케줄 ON/OFF, 상태 조회 가능 (`/report/daily/scheduler`)

---

### psycopg2 + SQLAlchemy

| 항목 | 내용 |
|------|------|
| **psycopg2** | PostgreSQL 전용 Python 드라이버. 가장 빠른 DB 연결. |
| **SQLAlchemy** | Python ORM. SQL을 Python 코드로 쓸 수 있게 해줌. v2부터 async 지원. |

**이 프로젝트에서의 역할:**
- `psycopg2`: 직접 SQL 쿼리 (시계열 데이터 조회 — 성능 중요 구간)
- `SQLAlchemy`: ML 학습 파이프라인의 데이터 로드 (`build_engine`, `fetch_meter_window`)
- 커넥션 풀 (minconn=3) + 시작 시 워밍업 (첫 요청 500 오류 방지)

---

## 3. AI 에이전트 — LangGraph 멀티에이전트

### LangGraph

| 항목 | 내용 |
|------|------|
| **무엇** | LLM 기반 멀티에이전트 워크플로우를 상태 기계(State Machine)로 구성하는 프레임워크. LangChain 팀 제작. |
| **사용 버전** | `>=0.1.0` |
| **핵심 개념** | `StateGraph`: 노드(에이전트)와 엣지(라우팅)를 정의. 상태(`AgentState`)가 노드 사이를 흘러다님. |

**이 프로젝트에서의 구조:**

```
사용자 질문
    │
    ▼
[classify 노드]  ← 키워드 룰 우선, 모호하면 LLM(EXAONE fast) 폴백
    │
    ├─ anomaly  → [anomaly_agent]   → DB에서 이상탐지 결과 조회 + LLM 분석
    ├─ report   → [reporting_agent] → KPI 집계 + LLM 보고서 생성
    ├─ forecast → [forecast_agent]  → v84 앙상블 추론 + LLM 요약
    ├─ cms      → [cms_agent]       → 설비 진단 + LLM 원인 분석 + 작업지시 행동 수행
    ├─ rag      → [rag_agent]       → pgvector 검색 + LLM 답변
    └─ off_topic → [rejection_node] → LLM 호출 없이 즉시 거절 (템플릿 응답)
    │
    ▼
[critic 노드]  ← 에너지 도메인 용어 교정 (LLM 없음, 문자열 치환 8개 패턴)
               한전→독일 공공 전력망, 수전량→계통 인입 전력량, ㎾h→kWh 등
    │
    ▼
최종 답변
```

**의도 분류 상세:**
- **1차 방어**: 27개+ 정규식 키워드 룰 4종 (anomaly/report/forecast/cms) + 오프토픽 패턴 1종 (LLM 호출 없음, <1ms)
- **특수 처리**: 계량기 URN 패턴(`V.Z84` 등) → 무조건 rag / 미래 시제 표현 → 무조건 forecast / 설비명+이상건수 조합 → 무조건 cms
- **2차 방어**: LLM 폴백 — EXAONE fast 모드, `max_tokens=10`, ~6s
- **오프토픽 차단**: 주식·요리·날씨·정치·의료·SNS·DB 조작 명령 등 → 즉시 거절

**Critic 노드 (v8 개선):**
이전 버전에서는 LLM을 호출해 품질 검토. 현재는 **LLM 호출 없이** 8개 문자열 치환 패턴만 적용.
응답속도 개선 (LLM 호출 제거) + 한국 에너지 용어 → Honda 독일 도메인 용어로 교정.

---

### LangChain Core

| 항목 | 내용 |
|------|------|
| **무엇** | LangGraph가 의존하는 기반 라이브러리. 메시지 포맷, 프롬프트 템플릿 등 공통 추상화. |
| **사용 버전** | `>=0.0.400` |

LangGraph 내부에서 사용. 직접 노출되는 API는 아님.

---

## 4. sLLM — EXAONE + Gemma4 듀얼 모델

### 듀얼 모델 아키텍처

| 구분 | 모델 | 역할 | 응답속도 |
|------|------|------|----------|
| **fast 모드** | EXAONE 3.5:7.8B (LG AI Research) | 의도 분류 (`max_tokens=10`, `thinking=False`) | ~6초 |
| **quality 모드** | Gemma4:12B (Google DeepMind) | 진단·보고서·RAG·이상탐지 분석 (`thinking=True`) | ~20초 |
| **quality (빠른)** | Gemma4:12B | RAG 단순 설명용 (`thinking=False` 명시) | ~3초 |

**모델 선정 근거 (33문항, GPT-5.5-as-Judge, 5축 10점 척도 · 2026-06-11 최종):**

| 모델 | 종합 | 한국어 | 형식 | 근거성 | 논리성 | 실용성 | 속도 | 자체 호스팅 |
|------|------|--------|------|--------|--------|--------|------|-------------|
| GPT-5.5 (참고) | 8.9 | 9.6 | 9.3 | 8.0 | 8.5 | 9.1 | ~17s | ❌ 클라우드 |
| **Gemma4 12B** ← 채택 | **8.6** | 9.4 | 9.1 | 8.1 | 8.0 | 8.6 | ~8s | ✅ |
| GPT-4o | 8.1 | 9.2 | 8.4 | 7.1 | 7.6 | 8.1 | ~3s | ❌ 클라우드 |
| **EXAONE 3.5 7.8B** ← 채택 | **8.1** | 9.1 | 8.6 | 7.6 | 7.0 | 8.1 | ~3.7s | ✅ |
| Gemma3 12B | 8.7 | 9.0 | 8.2 | 8.3 | 9.1 | 8.8 | 7.7s | ✅ (비채택) |
| Qwen2.5 7B | 2.4 | 3.9 | 3.8 | 4.5 | — | — | 4.8s | ✅ (탈락) |

> **EXAONE 10.0은 의도 분류 역할(4문항) 전용 성능** — 전체 33문항 종합은 8.1.
> Qwen2.5 탈락 이유: 복잡한 수치 추론 시 중국어로 전환.
> GPT-4o 제외 이유: 회사 설비 데이터를 외부 서버로 전송할 수 없음 (보안).
> 심사위원: 2026-06-11부로 GPT-4o → GPT-5.5 교체 (근거성 기준 강화). 상대 순위는 불변.

---

### Ollama

| 항목 | 내용 |
|------|------|
| **무엇** | 로컬에서 오픈소스 LLM을 실행하는 서버. OpenAI API와 호환되는 엔드포인트 제공. |
| **용도** | EXAONE, Gemma4 모델을 GPU 서버에서 서빙 |
| **엔드포인트** | `https://{runpod-id}.proxy.runpod.net/v1` (OpenAI 호환) |
| **버전** | v0.30.3+ |

**llm_client.py 구조:**
```python
def chat(messages, max_tokens=1024, fast=False, thinking=None) -> str:
    model = LLM_MODEL_FAST if fast else LLM_MODEL
    # fast=True   → EXAONE 3.5:7.8B, think=False, temperature=0.1
    # fast=False  → Gemma4:12B, think=True (기본), temperature=0.3
    # thinking=False 명시 → Gemma4 think OFF (~3s, 단순 설명용)
```

**성능 최적화:**
- `keep_alive=-1`: 모델 영구 메모리 적재 (재요청 시 로딩 대기 없음)
- `OLLAMA_NUM_CTX` 환경변수로 컨텍스트 길이 조절 (기본 8192)
- Ollama 전용: OpenAI SDK 대신 httpx로 `/api/chat` 직접 호출 (thinking 파라미터 지원)

`.env` 3줄만 바꾸면 Cloud API ↔ 로컬 전환 가능:
```env
LLM_PROVIDER=ollama          # openai | anthropic | gemini | ollama
LLM_MODEL=gemma4:12b
LLM_MODEL_FAST=exaone3.5:7.8b
```

---

### OpenAI / Anthropic / Gemini SDK

| 항목 | 내용 |
|------|------|
| **openai** `>=1.0.0` | GPT-4o/5.5 등 OpenAI 모델. 개발/테스트 시 기준선 |
| **anthropic** `>=0.102.0` | Claude 시리즈. 옵션 프로바이더 |
| **Gemini** | OpenAI 호환 엔드포인트 사용 (별도 SDK 불필요) |

모두 `llm_client.py`의 단일 진입점에서 추상화. 프로바이더 전환 시 코드 수정 없음.

---

### 프롬프트 엔지니어링 (v8)

파인튜닝 없이 프롬프트만으로 도메인 적응. v8에서 전면 재설계.

| 위치 | 기법 | 효과 |
|------|------|------|
| `cms.py` | Few-shot 3개 (소비설비/발전설비/계절 정상성) | PV 역률 오진단 방지, HIGH 레이블 오해 방지 |
| `reporting_agent.py` | "하나의 보고서만" + 할루시네이션 방지 지시 | 형식 점수 6/10 → 10/10 |
| `report.py` | `---SUMMARY---`/`---ACTIONS---` few-shot | 일일 브리핑 점수 8.2 → 9.2 |
| `domain_knowledge.py` | 계절별 COP, CHP 효율, 역률 정상 범위 주입 | 계절 이상 오진단 방지 |
| `orchestrator.py` | off_topic 2중 방어 (키워드 룰 + LLM 지시) | 거절 정확도 5.0 → 9.0+ |

---

## 5. RAG — pgvector + sentence-transformers

### pgvector

| 항목 | 내용 |
|------|------|
| **무엇** | PostgreSQL 확장 플러그인. DB 안에서 직접 벡터 유사도 검색 가능. |
| **특징** | 별도 Chroma/Pinecone 서버 없이 기존 PostgreSQL에 벡터 인덱스 추가 |
| **검색 방식** | 코사인 유사도 (`<=>` 연산자), Top-K=5, 임계값 0.7 |

```sql
-- 실제 사용 쿼리 형태
SELECT content FROM knowledge_docs
ORDER BY embedding <=> $1
LIMIT 5;
```

---

### sentence-transformers

| 항목 | 내용 |
|------|------|
| **무엇** | 텍스트를 고정 크기 벡터(임베딩)로 변환하는 모델 라이브러리. |
| **사용 버전** | `>=2.3.0` |
| **특징** | 로컬 실행 — 임베딩은 클라우드 API 없이 처리 (데이터 보안) |

**이 프로젝트에서의 역할:**
- `backend/src/knowledge/embedding.py`: 도메인 지식 문서를 벡터로 변환해 pgvector에 저장
- 사용자 질문도 동일 모델로 임베딩 → pgvector에서 유사 문서 검색

**지식 베이스 (backend/docs/kb/):**
- `01_facility_overview.md` — 설비 개요
- `02_anomaly_types_and_actions.md` — 이상 유형·조치
- `03_kpi_formulas_and_units.md` — KPI 공식·단위
- `04_energy_optimization_strategy.md` — 에너지 최적화 전략
- `05_meter_catalog_and_interpretation.md` — 계량기 카탈로그

---

## 6. ML 예측 모델 — v84 앙상블

### 아키텍처 개요

```
입력: 최근 250시간 전력 데이터 (1시간 간격)
    │
    ▼
[전처리] 잔차 타겟 (P(t) − P(t−1)) + 시간/계절 피처
    │
    ├── LSTM ×6개 (v63·v67·v71 세 버전 × 다양한 은닉층·드롭아웃 조합)
    ├── CatBoost
    ├── LightGBM ×2  (1h 전용 / 3h 전용)
    ├── Ridge Regression
    └── Seasonal Naive (계절 기준선)
    │
    ▼
[앙상블] LSTM ×6 중앙값(median) → 검증 기반 시간대별 shrunk bias correction × gain 1.30
    │
    ▼
출력: 1시간 후 / 3시간 후 전력 예측 (kW)
```

**45개 계량기 개인화** — 계량기마다 별도 모델 학습 및 아티팩트 저장.

**P-Max 피크 예측 앙상블** — 별도 모델 세트 (LightGBM×2 + XGBoost + CatBoost, `import_pmax_v29_60min`).

---

### PyTorch

| 항목 | 내용 |
|------|------|
| **무엇** | Meta가 만든 딥러닝 프레임워크. LSTM 모델 구현에 사용. |
| **사용 버전** | `>=2.0.0` (CPU-only 설치 — Docker 환경 GPU 없음) |

**이 프로젝트에서의 역할:**
- `RecurrentPredictor`: LSTM 기반 시계열 예측 모델 클래스
- 6가지 변형(은닉층 크기, 드롭아웃, 레이어 수)으로 앙상블 다양성 확보
- 학습된 가중치 `.pt` 파일로 저장 (HF Hub에 보관)

---

### CatBoost

| 항목 | 내용 |
|------|------|
| **무엇** | Yandex가 만든 그래디언트 부스팅 라이브러리. 범주형 피처 처리에 강점. |
| **사용 버전** | `>=1.2.0` |

LSTM이 잡지 못하는 비선형 패턴 포착. 학습 파일: `catboost.cbm` (HF Hub에 보관).

---

### LightGBM

| 항목 | 내용 |
|------|------|
| **무엇** | Microsoft가 만든 그래디언트 부스팅. CatBoost보다 빠른 학습 속도. |
| **사용 버전** | `>=4.0.0` |

2개 모델 (`lightgbm_t_plus_1.txt`, `lightgbm_t_plus_3.txt`)로 1h/3h 각각 학습.

---

### XGBoost

| 항목 | 내용 |
|------|------|
| **무엇** | 그래디언트 부스팅의 원조. 안정적이고 검증된 성능. |
| **사용 버전** | `>=1.7.0` |

P-Max 피크 예측 앙상블(LightGBM×2 + XGBoost + CatBoost)에서 사용.

---

### scikit-learn

| 항목 | 내용 |
|------|------|
| **무엇** | Python 머신러닝의 표준 라이브러리. 전처리, 평가 지표, 간단한 모델 제공. |
| **사용 버전** | `>=1.3.0` |

**이 프로젝트에서의 역할:**
- `MinMaxScaler`, `StandardScaler`: 전력 데이터 정규화
- `Ridge Regression`: 앙상블 기준선 모델
- `IsolationForest`: 이상탐지 폴백 경로
- 스케일러 저장: `input_scaler.joblib`, `target_scaler.joblib`

---

### joblib

| 항목 | 내용 |
|------|------|
| **무엇** | Python 객체 직렬화 라이브러리. scikit-learn 모델 저장에 표준적으로 사용. |
| **사용 버전** | `>=1.3.0` |

스케일러, Ridge 모델, P-Max 앙상블 모델 저장 형식. HF Hub에 LFS로 관리.

---

### statsmodels

| 항목 | 내용 |
|------|------|
| **무엇** | 통계 모델링 라이브러리. ARIMA, STL 분해, 회귀 분석 등. |
| **사용 버전** | `>=0.14.0` |

이상탐지 폴백 경로에서 STL 분해 기반 잔차 이상 감지에 사용.

---

### vmdpy

| 항목 | 내용 |
|------|------|
| **무엇** | VMD(Variational Mode Decomposition) — 시계열 신호 분해 알고리즘. |
| **사용 버전** | `>=0.1` |

전력 시계열의 트렌드/주기 성분 분리에 사용.

---

### pandas / numpy

| 항목 | 내용 |
|------|------|
| **pandas** `>=2.0.0` | 시계열 데이터 처리의 핵심. DB 쿼리 결과 → DataFrame 변환, 리샘플링, 결측 처리. |
| **numpy** `>=2.0.0` | 수치 연산 기반. 배열 슬라이싱, 이상 점수 계산 등. |

---

## 7. ML 이상탐지 모델

### 2경로 이상탐지 구조

```
[주력 경로] v84 LSTM 잔차 비율 기반  (artifacts 있을 때)
    LSTM 예측값과 실젯값의 잔차를 계량기별 임계값(threshold)으로 나눈 비율:
    ratio = |actual - predicted| / threshold
    → ratio ≥ 2.0 = HIGH  |  ≥ 1.5 = MEDIUM  |  ≥ 1.0 = LOW
    ※ IsolationForest는 이 경로에서 미사용 (if_flag 고정 0)

[폴백 경로] 3단 투표  (v84 artifacts 없을 때)
    통계 기반 (Z-score, IQR, STL 잔차)
    IsolationForest
    LSTM-AE (오토인코더 재구성 오차)
    → 3표 = HIGH  |  2표 = MEDIUM  |  1표 = LOW
```

### 이상 유형 분류

| 유형 | 설명 |
|------|------|
| `CHPOutage` | CHP 발전 중단 감지 |
| `PowerSpike` | 전력 급증 |
| `COPDrop` | 냉난방 성능 계수 급락 |
| `NightConsumption` | 야간 비정상 소비 |
| `PVNightNonZero` | PV 야간 비영 발전 (센서 이상) |

### LSTM-AE (오토인코더)

정상 데이터로만 학습 → 이상 데이터가 들어오면 재구성 오차가 커짐. 오차 > 임계값 = 이상.

### 시뮬레이터 연동

가상 시계가 과거 데이터를 "실시간"으로 재생하면, 배경 워커(`simulator.py`)가 자동으로 이상탐지를 실행하고 `/notifications/stream` SSE로 결과를 프론트엔드에 전송한다.

---

## 8. 데이터베이스 — PostgreSQL + TimescaleDB

### PostgreSQL

| 항목 | 내용 |
|------|------|
| **무엇** | 오픈소스 관계형 DB. 가장 기능이 풍부하고 확장성이 뛰어남. |
| **서버** | AWS EC2 (`13.209.98.228:5432`) |
| **DB명** | `cms` |

---

### TimescaleDB

| 항목 | 내용 |
|------|------|
| **무엇** | PostgreSQL 확장. 시계열 데이터 특화 — 자동 파티셔닝, 압축, 시계열 함수. |
| **용도** | 15분 간격 전력 계측 데이터 (~285만 행) 고속 조회 |

주요 테이블:
```sql
reference.corrected_resampled_1h    -- 계량기별 시간 단위 집계 (주 조회 테이블)
reference.corrected_resampled_15min -- 15분 단위
ems.*                               -- 원본 계측 데이터 (71개 계량기, 6년치)
anomaly_results                     -- 이상탐지 결과 저장
cms.work_orders                     -- 정비 작업지시
cms.control_recommendations         -- 운영 권고 이력
monthly_report                      -- AI 생성 월간 보고서 캐시
daily_report                        -- AI 생성 일일 브리핑 캐시
chat_sessions                       -- 채팅 세션 메타데이터
chat_messages                       -- 채팅 메시지 이력
users                               -- 사용자 계정 (역할 포함)
```

**데이터 특이사항:**
- 게이트웨이 장애 4개 구간 마스킹 처리 (2020-02~03, 2020-08~09, 2021-11~12, 2022-05~07)
- `V.Z81` 의 `W_in`은 오버플로우(-55억 kWh) — 사용 금지
- CHP 계량기: `H1.ZE20` (2023~) / `H1.Z20` (~2022) coalesce 처리

---

### pgvector

PostgreSQL 내에서 임베딩 벡터 저장 및 코사인 유사도 검색. RAG 섹션 참조.

---

## 9. 프론트엔드 — React + Vite

### React 19

| 항목 | 내용 |
|------|------|
| **무엇** | Facebook이 만든 UI 컴포넌트 라이브러리. 현재 가장 널리 쓰이는 프론트엔드 프레임워크. |
| **버전** | `^19.2.5` |

**12개 패널 구조 + 인증 화면:**

| 패널/화면 | 역할 |
|----------|------|
| LoginScreen | 세션 토큰 로그인 (역할별 접근 제어) |
| DashboardPanel | KPI 요약 + 실시간 시뮬레이터 시계 |
| EquipmentPanel | 설비별 헬스스코어 + AI 진단 + 예지보전 |
| MaintenancePanel | 작업지시 칸반 (진행/완료 이력) |
| AnomalyPanel + AnomalyChartPanel | 이상탐지 이력 + 차트 시각화 |
| ChatWorkspacePanel (+ ChatHistoryPanel) | AI 대화 + 세션 목록/히스토리 관리 |
| ForecastPanel | 수요 예측 시각화 (1h/3h) |
| ControlPanel | 운영 권고 + 적응형 학습 (승인/거부 이력) |
| BillingPanel | 목표 요금 관리 + 피크 위험 모니터링 |
| ReportPanel (+ DailyReportPanel) | 월간 KPI + 일일 브리핑 (PDF/DOCX/HWPX 다운로드) |
| TopologyPanel | 계량기 에너지 흐름 토폴로지 |
| SettingsPanel | LLM 설정 (프로바이더/모델/API키) + 스케줄러 |
| UsersPanel | 사용자 관리 |

**테마 시스템 (theme.js):**
- 라이트/다크 모드 전환 (`data-theme` CSS 변수)
- 틸(teal) 브랜드 컬러 기반 디자인 토큰
- `localStorage`에 선호 테마 저장
- 알림에서 "AI 분석" 클릭 → 채팅 자동 질의 (`chatSeed` 상태 전달)
- 설비 패널 → 이상 내역 드릴다운 (`anomalyFilter` 상태 전달)

---

### Vite 8

| 항목 | 내용 |
|------|------|
| **무엇** | 차세대 프론트엔드 빌드 도구. Create React App보다 10배 이상 빠른 HMR(핫 리로드). |
| **버전** | `^8.0.10` |

개발 서버: `npm run dev` → `localhost:5173`
프로덕션 빌드: `npm run build` → Docker Nginx로 서빙

---

### Recharts 3

| 항목 | 내용 |
|------|------|
| **무엇** | React용 차트 라이브러리. D3.js 기반, 선언적 API. |
| **버전** | `^3.8.1` |

**이 프로젝트에서의 역할:**
- `LineChart`: 전력 트렌드, 예측 결과
- `BarChart`: 월간 KPI 비교
- `ComposedChart`: 보고서 패널 — Grid/PV/CHP 스택 바 + COP 라인 오버레이
- `AreaChart`: 이상탐지 시계열

---

### lucide-react

| 항목 | 내용 |
|------|------|
| **무엇** | 픽셀 퍼펙트 SVG 아이콘 라이브러리. React 컴포넌트로 제공. |
| **버전** | `^1.17.0` |

사이드바 메뉴 아이콘, 버튼 아이콘 전체에 사용 (`Factory`, `Wrench`, `AlertTriangle` 등).

---

### axios

| 항목 | 내용 |
|------|------|
| **무엇** | HTTP 클라이언트. `fetch` API보다 인터셉터, 에러 처리가 편리. |
| **버전** | `^1.16.0` |

`frontend/src/api/client.js`: 모든 백엔드 API 호출 집중 관리. `Authorization: Bearer` 헤더 자동 주입.

---

### react-markdown + remark-math + rehype-katex

| 항목 | 내용 |
|------|------|
| **react-markdown** `^10.1.0` | Markdown 텍스트를 React 컴포넌트로 렌더링 |
| **remark-math** `^6.0.0` | Markdown 수식 구문 파싱 플러그인 |
| **rehype-katex** `^7.0.1` | HTML 수식 렌더링 (KaTeX 기반) |
| **katex** `^0.16.45` | 수식($E=mc^2$) 렌더링 엔진 |

AI 진단 결과, 보고서 텍스트를 Markdown+수식으로 받아 렌더링.

---

## 10. 인프라 — Docker + RunPod + HF Hub

### Docker + Docker Compose

| 항목 | 내용 |
|------|------|
| **무엇** | 컨테이너 기반 앱 패키징·실행 도구. "내 컴퓨터에서는 됐는데" 문제 해결. |
| **버전** | Docker 23.0+ / Compose v2 |

**서비스 구성:**
```yaml
services:
  backend:   FastAPI (port 8000) + ML 모델 볼륨 마운트
  frontend:  React 빌드 → Nginx (port 8080)
```

**자동 artifacts 다운로드:**
컨테이너 시작 시 `ensure_artifacts.py` 실행 → HF Hub에서 없는 모델만 자동 다운로드.

---

### RunPod

| 항목 | 내용 |
|------|------|
| **무엇** | GPU 클라우드 서비스. 시간당 과금으로 RTX 3090 등 GPU 대여. |
| **사용 GPU** | RTX 3090 24GB (Secure Cloud) |
| **용도** | Ollama 서버 실행 — Gemma4:12B + EXAONE 3.5:7.8B 동시 서빙 |
| **추론 속도** | 145 tok/s (EXAONE 7.8B Q4 기준) |

```
RunPod Pod 내:
  ollama serve (OLLAMA_HOST=0.0.0.0)
  └── gemma4:12b      (~8GB VRAM)
  └── exaone3.5:7.8b  (~5GB VRAM)
  RTX 3090 24GB → 두 모델 동시 적재 (keep_alive=-1)
  
외부 접근: https://{pod-id}-11434.proxy.runpod.net/v1
```

---

### Hugging Face Hub

| 항목 | 내용 |
|------|------|
| **무엇** | ML 모델·데이터셋 공유 플랫폼. Git 기반 버전 관리. |
| **저장소** | `mintmarket/ems-agent-artifacts` (public dataset) |
| **보관 내용** | v84 앙상블 artifacts 45개 계량기 × 1h/3h (~589MB) + P-Max 모델 (~114MB) |

```bash
# 다운로드 (팀원, 토큰 불필요)
.venv/bin/python scripts/download_artifacts.py

# Docker 실행 시 자동 다운로드 (ensure_artifacts.py)
docker compose up --build -d
```

---

### reportlab + python-docx

| 항목 | 내용 |
|------|------|
| **reportlab** `>=4.0.0` | Python PDF 생성 라이브러리 |
| **python-docx** `>=1.1.0` | Python Word 문서(.docx) 생성 |

보고서 패널의 PDF / DOCX / HWPX 다운로드 기능.

---

### MLflow (skinny)

| 항목 | 내용 |
|------|------|
| **무엇** | ML 실험 추적 플랫폼. 모델 파라미터, 지표, 아티팩트 버전 관리. |
| **버전** | `mlflow-skinny>=3.12.0` (경량 버전 — UI 없이 추적만) |

v84 앙상블 학습 실험 지표 기록용.

---

## 11. 개발 도구

### pytest

| 항목 | 내용 |
|------|------|
| **무엇** | Python 테스트 프레임워크. |
| **버전** | `>=8.0.0` + `pytest-asyncio` + `pytest-cov` |

`tests/` 폴더에 백엔드 API 테스트.

---

### httpx

| 항목 | 내용 |
|------|------|
| **무엇** | 비동기 HTTP 클라이언트. FastAPI 테스트, Ollama 직접 API 호출에 사용. |
| **버전** | `>=0.27.0` |

`llm_client.py`에서 Ollama 네이티브 `/api/chat` 엔드포인트 직접 호출 (thinking 파라미터 지원을 위해 OpenAI SDK 우회).

---

### Git LFS

| 항목 | 내용 |
|------|------|
| **무엇** | 대용량 파일을 Git에서 포인터로 관리하는 확장. GitHub 100MB 제한 우회. |
| **사용** | `.gitattributes`에 `*.joblib filter=lfs` 설정 |

P-Max `.joblib` 모델 파일 16개 관리. 나머지 `.pt`, `.cbm`은 HF Hub로 이관.

---

### Jinja2

| 항목 | 내용 |
|------|------|
| **무엇** | Python 템플릿 엔진. 변수 치환, 조건문, 반복문을 HTML/텍스트에 삽입. |
| **버전** | `>=3.1.2` |

보고서 HTML 템플릿 렌더링에 사용.

---

## 12. 버전 요약표

### 백엔드 Python 패키지

| 패키지 | 버전 | 역할 |
|--------|------|------|
| fastapi | >=0.110.0 | REST API 서버 |
| uvicorn | >=0.23.0 | ASGI 웹 서버 |
| langgraph | >=0.1.0 | 멀티에이전트 워크플로우 |
| langchain-core | >=0.0.400 | LangGraph 의존 기반 라이브러리 |
| openai | >=1.0.0 | GPT / Ollama 호환 LLM 클라이언트 |
| anthropic | >=0.102.0 | Claude LLM 클라이언트 |
| torch | >=2.0.0 | LSTM 예측 모델 |
| catboost | >=1.2.0 | 그래디언트 부스팅 예측 |
| lightgbm | >=4.0.0 | 그래디언트 부스팅 예측 |
| xgboost | >=1.7.0 | 그래디언트 부스팅 (P-Max) |
| scikit-learn | >=1.3.0 | 전처리 + IsolationForest |
| statsmodels | >=0.14.0 | STL 분해 이상탐지 |
| pandas | >=2.0.0 | 데이터 처리 |
| numpy | >=2.0.0 | 수치 연산 |
| matplotlib | >=3.7.0 | ML 파이프라인 시각화 |
| psycopg2-binary | >=2.9.10 | PostgreSQL 드라이버 |
| sqlalchemy | >=2.0.0 | ORM + 쿼리 빌더 |
| sentence-transformers | >=2.3.0 | 텍스트 임베딩 (RAG) |
| huggingface_hub | >=1.0.0 | HF Hub artifacts 다운로드 |
| apscheduler | >=3.10.0 | 크론 스케줄러 |
| reportlab | >=4.0.0 | PDF 생성 |
| python-docx | >=1.1.0 | Word 문서 생성 |
| mlflow-skinny | >=3.12.0 | 실험 추적 |
| httpx | >=0.27.0 | 비동기 HTTP 클라이언트 (Ollama 직접 호출) |
| jinja2 | >=3.1.2 | 템플릿 엔진 |
| pytz | >=2024.1 | 시간대 처리 |
| sentencepiece | >=0.1.99 | 토큰화 (sentence-transformers 의존) |
| vmdpy | >=0.1 | 시계열 신호 분해 |

### 프론트엔드

| 패키지 | 버전 | 역할 |
|--------|------|------|
| react | ^19.2.5 | UI 프레임워크 |
| vite | ^8.0.10 | 빌드 도구 |
| recharts | ^3.8.1 | 차트 라이브러리 |
| axios | ^1.16.0 | HTTP 클라이언트 (Bearer 토큰 자동 주입) |
| lucide-react | ^1.17.0 | 아이콘 |
| react-markdown | ^10.1.0 | Markdown 렌더링 |
| remark-math | ^6.0.0 | 수식 파싱 |
| rehype-katex | ^7.0.1 | 수식 렌더링 |
| katex | ^0.16.45 | 수식 렌더링 엔진 |

### 인프라 / 모델

| 항목 | 버전/사양 | 역할 |
|------|-----------|------|
| Docker | 23.0+ | 컨테이너 |
| Docker Compose | v2 | 멀티 컨테이너 오케스트레이션 |
| PostgreSQL | 15+ (TimescaleDB) | 시계열 DB |
| pgvector | 최신 | 벡터 검색 |
| Ollama | v0.30.3+ | LLM 로컬 서빙 (keep_alive=-1) |
| Gemma4:12B | Google DeepMind | quality 모드 LLM (thinking 지원) |
| EXAONE 3.5:7.8B | LG AI Research | fast 모드 LLM |
| RunPod | RTX 3090 24GB | GPU 서버 |
| Hugging Face Hub | - | 모델 아티팩트 저장소 |
