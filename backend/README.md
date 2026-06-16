# Backend — FastAPI + LangGraph AI 에이전트

EMS Agent 백엔드. FastAPI REST API + LangGraph 멀티 에이전트 + ML 예측·이상탐지 파이프라인.

---

## 로컬 실행

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 프로젝트 루트의 .env가 자동으로 로드됨
cd src && uvicorn api.main:app --reload --port 8000
```

Swagger UI: <http://localhost:8000/docs>

---

## 환경변수 (루트 `.env`)

| 변수 | 설명 | 예시 |
|---|---|---|
| `LLM_PROVIDER` | `ollama` \| `openai` \| `anthropic` \| `gemini` | `ollama` |
| `LLM_MODEL` | 품질 경로 모델 (Gemma4) | `gemma4:12b` |
| `LLM_MODEL_FAST` | 속도 경로 모델 (EXAONE) | `exaone3.5:7.8b` |
| `OLLAMA_URL` | Ollama 엔드포인트 | `https://<pod>.proxy.runpod.net/v1` |
| `DATABASE_URL` | PostgreSQL 연결 문자열 | `postgresql://cms:pw@host:5432/cms` |

---

## 소스 구조

```
src/
├── api/
│   ├── main.py               # FastAPI 앱, 라이프사이클, CORS
│   └── routers/
│       ├── chat.py           # POST /chat, /chat/stream (SSE)
│       ├── cms.py            # /cms/equipment, /diagnose, /predictive, /work-orders
│       ├── anomalies.py      # /anomalies, /anomalies/run
│       ├── forecast.py       # /forecast/predict/v84-ensemble, /train
│       ├── report.py         # /report, /report/daily, /report/billing
│       ├── control.py        # /control/recommendations
│       ├── simulator.py      # /simulator/{start|pause|reset|seek|speed}
│       ├── notifications.py  # /notifications/stream (SSE)
│       └── settings.py       # /settings, /settings/test-llm
├── agents/
│   ├── orchestrator.py       # 의도 분류 (키워드 룰 → EXAONE 폴백, 92% 정확도)
│   ├── llm_client.py         # Ollama/OpenAI 통합 클라이언트, fast= 파라미터
│   ├── cms_agent.py          # 설비 진단·예지보전·작업지시 에이전트
│   ├── anomaly_agent.py      # 이상 원인 분석 에이전트
│   ├── reporting_agent.py    # 보고서 생성 에이전트
│   ├── rag_agent.py          # pgvector RAG 에이전트
│   ├── forecast_agent.py     # 수요 예측 에이전트
│   └── state.py              # LangGraph 공유 상태 스키마
├── data/
│   └── loader.py             # TimescaleDB 쿼리 (12컬럼 노출)
├── knowledge/
│   ├── domain_knowledge.py   # 계절 COP·역률·kW/kWh 도메인 지식
│   ├── embedding.py          # pgvector 임베딩 생성
│   └── meter_metadata.json   # 계량기 메타데이터
└── models/anomaly/           # LSTM 잔차 비율 이상탐지 모델
```

---

## 에이전트 흐름

```
POST /chat
  → orchestrator.py  (의도 분류: cms | anomaly | report | forecast | rag | off_topic)
      ├── cms      → cms_agent.py      (LLM 진단 + 작업지시 실행)
      ├── anomaly  → anomaly_agent.py  (이상 원인 분석)
      ├── report   → reporting_agent.py
      ├── forecast → forecast_agent.py
      └── rag      → rag_agent.py      (pgvector 검색 + 답변)
  → Critic (용어 교정, 문자열 치환)
```

**LLM 듀얼 아키텍처**:
- `llm_client.chat(fast=True)` → EXAONE 3.5 7.8B (의도 분류, ~2s)
- `llm_client.chat(fast=False)` → Gemma4 12B + thinking (진단·보고서·분석, ~8s)

---

## ML 파이프라인

### 이상탐지 (2경로)

| 경로 | 방식 | 조건 |
|---|---|---|
| 주력 | LSTM 잔차 비율: `ratio = \|실제−예측\| / threshold` ≥ 2.0=HIGH | artifacts 있을 때 |
| 폴백 | 통계(Z·IQR·STL) + IsolationForest + LSTM-AE 3단 투표 | artifacts 없을 때 |

유형: `COPDrop` · `CHPOutage` · `NightConsumption` · `PVNightNonZero` · `PowerSpike`

### 수요 예측 (v84 앙상블)

LSTM×6 버전 median + CatBoost + LightGBM + Ridge + Seasonal Naive.
잔차 타겟 `P(t) − P(t−1)`, 45개 계량기 개인화, shrunk bias correction.

```bash
# 학습 (별도 venv 필요 — torch + catboost + lightgbm)
cd backend
python3.12 -m venv .venv-train
.venv-train/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv-train/bin/pip install numpy pandas scikit-learn joblib catboost lightgbm \
    sqlalchemy psycopg2-binary python-dotenv

OMP_NUM_THREADS=1 .venv-train/bin/python -m ml.pipeline.train --horizon 1 --workers 4
```

artifacts는 Hugging Face Hub(`mintmarket/ems-agent-artifacts`)에 보관.
Docker Compose 실행 시 자동 다운로드, 로컬 직접 실행 시 `scripts/download_artifacts.py`.

---

## 테스트

```bash
# 프로젝트 루트에서
python -m pytest tests/
```

---

## sLLM 평가 결과 (v8, gpt-5.5 심사 기준)

| 모델 | 역할 | 종합 | 근거성 |
|---|---|---|---|
| Gemma4 12B | 품질 경로 (진단·보고서) | **8.6/10** | 8.1 |
| EXAONE 3.5 7.8B | 속도 경로 (의도 분류) | 10.0 (담당 4문항) | — |

현재 라우터 v2 sensitivity 평가는 `dev/eval/router_two_stage_metrics_260615.py`와
`dev/eval/data/router_two_stage_eval_300_v2_260615.json` 기준으로 실행합니다.
모델별 실행 결과는 `reports/experiments/router_two_stage_classification/`에 생성됩니다.
