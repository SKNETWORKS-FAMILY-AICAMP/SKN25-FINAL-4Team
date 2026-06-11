# LLM 백엔드 아키텍처 — 성능 구조 가이드

> **현재 단계**: RunPod 전용 GPU에서 온프레미스 배포 구성을 사전 검증하는 PoC다.
>
> 프롬프트와 라우팅은 백엔드에 유지되며, 최종적으로 동일한 Ollama 구성을 사내 GPU 서버로 이전한다.

---

## 전체 구조 — 3개 레이어

```
사용자 질문
    │
    ▼
┌─────────────────────────────────────────────┐
│  레이어 1 — 모델 제어                        │
│  agents/llm_client.py                       │
│  어떤 모델을, 어떻게 부를지                  │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│  레이어 2 — 프롬프트 엔지니어링              │
│  agents/ + api/routers/                     │
│  무엇을 어떻게 물어볼지 (few-shot 포함)      │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│  레이어 3 — 도메인 지식 주입                 │
│  knowledge/domain_knowledge.py              │
│  모델이 모르는 공장 특화 지식               │
└─────────────────────────────────────────────┘
    │
    ▼
RunPod (Ollama)
  gemma4:12b       ← 품질 경로
  exaone3.5:7.8b   ← 속도 경로
```

---

## 레이어 1 — 모델 제어

### `backend/src/agents/llm_client.py`

모든 LLM 호출이 반드시 이 파일의 `chat()` 함수를 거친다. 사용처 7곳(의도분류/진단/보고서/이상탐지/RAG/예측/일일요약)이 전부 이 단일 진입점을 공유한다.

**듀얼 모델 라우팅**:

```python
def chat(messages, max_tokens=1024, fast=False):
    model = LLM_MODEL_FAST if fast else LLM_MODEL
    # fast=True  → EXAONE 3.5 7.8B (think=false, ~6s)
    # fast=False → Gemma4 12B (thinking 활성화, ~20s)
```

| `fast=` | 모델 | thinking | 대상 작업 |
|---------|------|----------|-----------|
| `True`  | EXAONE 3.5 7.8B | 꺼짐 | 의도 분류 (max_tokens=10) |
| `False` | Gemma4 12B | 켜짐 | 진단·보고서·RAG·이상탐지 |

**환경 변수로 모델 교체**:

```env
LLM_MODEL=gemma4:12b            # 품질 경로
LLM_MODEL_FAST=exaone3.5:7.8b  # 속도 경로
OLLAMA_URL=https://{pod-id}-11434.proxy.runpod.net/v1
```

이 3줄만 바꾸면 모델 교체 완료. 코드 수정 불필요.

---

## 레이어 2 — 프롬프트 엔지니어링

### 의도 분류 — `agents/orchestrator.py`

사용자 질문을 6개 의도(`anomaly / report / rag / forecast / cms / off_topic`)로 분류해 적절한 에이전트로 라우팅.

- **1차 방어**: `_KW_OFFTOPIC` 정규식 — 주식·요리·날씨·연예·스포츠·의료 등 즉시 `off_topic` 반환 (LLM 호출 없음)
- **2차 처리**: LLM 폴백 → `fast=True`로 EXAONE에게 의도 분류 요청 (단 10토큰)
- **거절 응답**: `rejection_node()` — 고정 템플릿 즉시 반환, critic 우회

```python
# orchestrator.py — LLM 폴백 의도 분류
raw = llm_chat(
    [{"role": "user", "content": INTENT_PROMPT.format(question=question)}],
    max_tokens=10,
    fast=True,   # EXAONE으로 빠르게
)
```

---

### 설비 진단 — `api/routers/cms.py`

진단 품질을 결정하는 **few-shot 3개**가 삽입된다.

| few-shot | 내용 | 방지하는 문제 |
|----------|------|---------------|
| `fs_seasonal` | 외기온 35°C 여름, COP 1.85 → 계절 정상 판정 | 계절 효율 저하를 이상으로 오진단 |
| `fs_user` (소비 설비) | 압축기 전압 저하·전류 과부하 → HIGH 이상 진단 | HIGH를 "성능이 높다"로 오해 |
| `fs_user2` (발전 설비) | PV 역률 -0.95 → 역송 정상 판정 | 음수 역률을 고장으로 오진단 |

프롬프트 순서: `[system, fs_seasonal_user, fs_seasonal_assistant, fs_user, fs_assistant, 실제질문]`

---

### 보고서 생성 — `agents/reporting_agent.py`

경영진 보고서 품질을 올리는 핵심 지시 2개:

```python
# 1. 없는 수치 생성 금지
"**중요: 위 KPI 데이터에 없는 수치를 만들어내지 마세요.**"

# 2. 단일 출력 강제 (대안 버전 금지)
"**하나의 완성된 보고서만 작성하세요. 대안, 다른 버전, 예시 비교를 절대 제시하지 마세요.**"
```

→ 적용 전후: 형식 점수 6/10 → 10/10

---

### 일일 브리핑 / 월간 트렌드 — `api/routers/report.py`

**일일 브리핑** (`_generate_daily_summary`): HIGH 이상 발생 케이스 few-shot 포함. `---SUMMARY---` / `---ACTIONS---` 형식을 강제해 프론트엔드 파싱 안정화.

**월간 트렌드** (`_generate_trend_narrative`): "반드시 제공된 수치(소비량·자급률·COP·이상탐지 건수)를 모두 인용하세요." 지시 + few-shot으로 수치 인용률 강제.

---

### RAG / 일반 질의 — `agents/rag_agent.py`

```python
# 항상 한국어 응답
"무조건 한국어로 답변하세요."

# 범위 밖 질문 거절
"에너지·설비 범위를 벗어난 질문이면 한 문장으로만 답하고 더 이상 설명하지 마세요."
```

---

### 이상탐지 분석 — `agents/anomaly_agent.py`

```python
# 항상 한국어 응답
"항상 한국어로 답변하세요."
```

이상 심각도(HIGH/MEDIUM/LOW) 레이블이 anomaly 모델에서 생성되어 LLM에 전달. LLM은 원인 분석·조치 권고 담당.

---

## 레이어 3 — 도메인 지식

### `backend/src/knowledge/domain_knowledge.py`

모델 사전학습 데이터에 없는 **공장 특화 지식**. 모든 프롬프트에 삽입되어 오진단을 방지한다.

| 지식 | 내용 | 없으면 발생하는 오류 |
|------|------|---------------------|
| 계절별 정상 COP | 여름(외기온 30°C+) COP 1.7~2.0 정상 | 여름 COP 1.85를 이상으로 오진단 |
| CHP 겨울 효율 | 열 수요 증가 시 전기 효율 ±3~5%p 감소 = 정상 | 겨울 CHP 효율 저하를 이상으로 오진단 |
| 발전 설비 역률 | PV·CHP 역률 음수/저값 = 역송 정상 (이상 아님) | 역률 -0.95를 고장으로 오진단 |
| 전력 단위 정의 | kW(순간 전력) vs kWh(누적 에너지) 구분 | 단위 혼용 오류 미감지 |

---

## RunPod 재생성 후 복구 절차

**모델 가중치만 런팟에 있고, 성능 코드는 백엔드에 있으므로** Pod를 새로 만들어도 두 명령이면 원상복구된다.

```bash
# RunPod Pod 내부에서
curl -fsSL https://ollama.com/install.sh | sh
OLLAMA_HOST=0.0.0.0 ollama serve &

ollama pull gemma4:12b       # ~8GB, 수분 소요
ollama pull exaone3.5:7.8b   # ~5GB, 수분 소요
```

```env
# .env — 새 Pod ID로 URL만 교체
OLLAMA_URL=https://{새pod-id}-11434.proxy.runpod.net/v1
```

```bash
docker compose restart backend
```

**바꿀 것**: URL 1줄  
**그대로 유지되는 것**: 모든 프롬프트, few-shot, 도메인 지식, 듀얼 모델 라우팅, 평가 결과

---

## 성능에 기여한 변경 이력

| 날짜 | 파일 | 변경 | 점수 변화 |
|------|------|------|-----------|
| 2026-06-04 | `cms.py` | 설비 유형별 few-shot, HIGH 레이블 명확화 | 형식 안정화 |
| 2026-06-04 | `domain_knowledge.py` | 계절 COP, CHP 효율, 역률 지식 추가 | 계절 정상성 6.7→부분개선 |
| 2026-06-04 | `orchestrator.py` | off_topic 키워드 라우팅 + 거절 노드 | 오프토픽 5.0→개선 |
| 2026-06-08 | `reporting_agent.py` | "하나의 보고서만" 지시 추가 | 형식 6/10→10/10 |
| 2026-06-08 | `report.py` | 일일 브리핑 HIGH 케이스 few-shot | daily_summary 8.2→9.2 |
| 2026-06-08 | `report.py` | 월간 트렌드 수치 인용 강제 + few-shot | trend 점수 개선 |
| 2026-06-08 | `cms.py` | 계절 정상성 few-shot 추가 | seasonal_normal 8.6→8.8 |
| 2026-06-08 | `llm_client.py` + `orchestrator.py` | 듀얼 모델 라우팅 (EXAONE fast / Gemma4 quality) | 속도·품질 동시 확보 |

---

## 관련 문서

- [plan.md](plan.md) — sLLM 전환 전략 및 아키텍처 결정
- [model_selection.md](model_selection.md) — 6개 모델 비교 및 Gemma4 선정 근거
- [progress.md](progress.md) — 전체 진행 요약 및 프롬프트 엔지니어링 내역
- [runpod_guide.md](runpod_guide.md) — RunPod 설치·운영 가이드
