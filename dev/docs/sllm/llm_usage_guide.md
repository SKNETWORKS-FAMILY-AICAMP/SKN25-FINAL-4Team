# EMS Agent — sLLM 통합 가이드

> 이 문서는 EMS Agent에서 sLLM(소형 언어 모델)이 어떻게 쓰이는지, 왜 그렇게 설계했는지,
> 어떤 기술로 품질을 높였는지를 처음 보는 사람도 이해할 수 있게 정리한 문서다.

---

## 1. 전체 그림 — sLLM이 하는 일과 하지 않는 일

### 핵심 원칙

> **sLLM은 계산하지 않는다. 설명한다.**

이 시스템에서 sLLM의 역할은 하나다. 백엔드가 DB·ML로 이미 만들어낸 결과물을 **운영자가 이해할 수 있는 한국어**로 변환하는 것.

```
[sLLM이 하지 않는 것]
  - 이상 탐지          → LSTM + IsolationForest + 통계 모델이 담당
  - 전력 예측          → v84 앙상블 (LSTM×6 + CatBoost + LightGBM) 이 담당
  - KPI 계산           → DB 집계 쿼리가 담당
  - 설비 전기 서명 계산 → 전압·전류·역률 실측값 DB 직접 계산

[sLLM이 하는 것]
  - "이상 원인이 뭐야?" → 탐지 결과 + 센서값 받아서 원인·조치 설명
  - "보고서 써줘"       → KPI 수치 받아서 서술 문장 생성
  - "예측 결과 해석해줘"→ ML 수치 받아서 운영 조언
  - "채팅 질문"         → RAG 문서 + 실측값 받아서 답변
```

### 왜 이렇게 나눴나

12B짜리 소형 모델로도 충분한 품질을 내려면 LLM이 "어려운 것"을 안 하면 된다.
LLM에게 "원시 센서 수천 행을 보고 이상을 찾아라"고 시키면 틀린다.
반면 "이 데이터를 보고 설명해라"는 잘 한다.
백엔드가 어려운 계산을 미리 끝내고 LLM에게는 글쓰기만 맡기는 구조다.

---

## 2. 모델 구성 — 무엇을 쓰는가

### RunPod에 올라간 두 모델

```
RunPod GPU (Ollama)
├── gemma4:12b          ← 품질 경로 (LLM_MODEL)
│     Google 개발 / Q4 양자화 약 8GB VRAM
│     thinking 모드: 답하기 전에 내부 추론 과정 수행
│     속도: ~20초 / 품질: 9.2/10
│
└── exaone3.5:7.8b      ← 속도 경로 (LLM_MODEL_FAST)
      LG AI Research 개발 / 한국어 특화
      thinking 꺼짐: 빠른 단답용
      속도: ~6초 / 품질: 8.9/10 (의도분류 100%)
```

### 왜 두 모델인가

의도 분류는 "anomaly", "rag" 같은 단어 하나만 뱉으면 된다.
이걸 위해 20초짜리 Gemma4를 쓰는 건 낭비다.
EXAONE 7.8B는 6초에 이 작업을 100% 정확도로 처리한다.
반면 진단 보고서·복잡한 분석은 Gemma4 thinking 모드로 품질을 확보한다.

### 모델 선정 과정 (요약)

6개 모델을 33문항, 5개 기준으로 평가했다:

| 모델 | 종합 점수 | 속도 | 결정 |
|------|----------|------|------|
| Gemma4 12B | 9.2/10 | ~20s | ✅ 품질 경로 채택 |
| GPT-4o | 9.1/10 | 3.4s | ❌ 클라우드 — 보안상 외부 전송 불가 |
| EXAONE 3.5 7.8B | 8.9/10 | 6.0s | ✅ 속도 경로 채택 |
| Gemma3 12B | 8.7/10 | 7.7s | 비채택 |
| Qwen2.5 7B | 2.4/10 | 4.8s | ❌ 한국어 중 중국어로 전환됨 |

GPT-4o가 가장 빠르지만 회사 설비 데이터를 외부 서버로 보낼 수 없어 제외.
Gemma4와 EXAONE 조합이 보안 + 품질 + 속도 균형이 가장 좋았다.

---

## 3. 코드 구조 — 어디서 어떻게 쓰이나

### 단일 진입점 원칙

모든 LLM 호출은 반드시 한 곳을 거친다:

```
backend/src/agents/llm_client.py  ←  10곳 전부 여기로
```

```python
# llm_client.py 핵심 로직
def chat(messages, max_tokens=1024, fast=False, thinking=None):
    model = LLM_MODEL_FAST if fast else LLM_MODEL
    # fast=True  → EXAONE (의도분류용)
    # fast=False → Gemma4 (분석·보고서용)
```

이 덕분에 `.env`의 모델명 두 줄만 바꾸면 전체 시스템 모델이 교체된다.

### 호출 위치 전체 목록 (10곳)

```
채팅 파이프라인 (agents/)
├── orchestrator.py:157  → 의도 분류 폴백 (EXAONE, max_tokens=10)
├── rag_agent.py:178     → 채팅 답변 (Gemma4, thinking=False)
├── anomaly_agent.py:441 → 이상 원인 분석 (Gemma4, thinking=True)
├── reporting_agent.py:260 → 보고서 서술 (Gemma4, max_tokens=1500)
└── forecast_agent.py:207  → 예측 해석 (Gemma4, max_tokens=1200)

API 직접 호출 (api/routers/)
├── cms.py:386           → 설비 AI 진단 버튼 (Gemma4, max_tokens=600)
├── report.py:209        → 월간 KPI 트렌드 내러티브 (max_tokens=350)
├── report.py:618        → 일일 운영 보고서 요약+조치 (max_tokens=500)
├── report.py:945        → 데이터 품질 요약 (max_tokens=250)
├── report.py:1036       → 에너지 원단위 분석 (max_tokens=250)
└── report.py:1184       → 비용 현황 분석 (max_tokens=300)
```

---

## 4. 요청 흐름 — 순서대로

### 경로 A: 채팅 질문 (가장 일반적인 경로)

```
① 프론트엔드
   사용자가 채팅창에 질문 입력
   → POST /chat/stream

② chat.py — _invoke_graph()
   히스토리 최근 10턴을 lc_messages로 변환
   LangGraph 그래프 실행 시작

③ orchestrator — classify_intent()
   ┌─ 1단계: 키워드 룰 (LLM 없음, 즉시)
   │   "이상탐지" → anomaly
   │   "보고서"   → report
   │   "주식"     → off_topic (즉시 거절, LLM 없음)
   │   매칭 성공 → ④ 바로 이동
   │
   └─ 2단계: 룰 실패 시 EXAONE 호출
       LLM 호출 #1 (fast=True, max_tokens=10)
       "anomaly / rag / report / forecast / cms / off_topic" 반환

④ 의도별 에이전트 분기
   각 에이전트에서 DB/ML 데이터 수집 (LLM 없음)
   → LLM 호출 #2 (Gemma4, 에이전트마다 다른 프롬프트)

⑤ critic_node()
   문자열 치환만 수행 (한전→독일 공공 전력망 등)
   LLM 없음

⑥ 응답 반환
   단어 단위 SSE 스트리밍으로 프론트에 전송
```

**한 번의 채팅에서 LLM 호출 횟수:**
- 키워드 룰 성공 → **1번** (에이전트 1회)
- 키워드 룰 실패 → **2번** (의도분류 + 에이전트)
- CMS 질문 → **0번** (LLM 없이 DB만 조회)
- off_topic → **0번** (즉시 거절 문자열 반환)

---

### 경로 B: UI 버튼·페이지 로드 (채팅 없이 직접 호출)

```
설비 진단 버튼 클릭
   → GET /cms/equipment/{id}/diagnose
   → 전기 서명(전압·전류·역률·COP) 계산
   → Gemma4 호출 #1 (few-shot 2개 포함)

보고서 페이지에서 AI 분석 버튼 클릭 (skip_ai=False 일 때)
   → GET /report, /report/daily 등
   → KPI/일별 데이터 집계
   → Gemma4 호출 #1 (각 엔드포인트별 프롬프트)

※ 기본값은 skip_ai=True
  → 페이지 로드 시 LLM 자동 호출 없음
  → AI 분석 버튼을 눌렀을 때만 실행
```

---

## 5. 성능 향상 기법 — 어떻게 품질을 올렸나

12B짜리 소형 모델로 품질을 높이기 위해 쓴 기법 10가지.

---

### 기법 1: 데이터 선주입 (가장 중요)

LLM에게 질문만 던지는 게 아니라, 백엔드가 먼저 필요한 데이터를 모두 모아서 프롬프트에 담아 보낸다.

```python
# anomaly_agent.py — LLM 호출 전 데이터 준비
anomalies = _fetch_anomalies(...)         # DB에서 이상 결과 조회
anomalies = _enrich_with_sensors(...)    # 이상 시각 센서값 추가
type_counts = _count_anomalies_by_type() # 유형별 건수 집계

# 이 모든 데이터를 프롬프트에 담아 한 번에 전달
prompt = f"이상 결과:\n{anomaly_block}\n센서값:\n{sensor_block}\n..."
answer = llm_chat(prompt)
```

**효과**: LLM이 추론·환각으로 수치를 만들어내지 않고 제공된 데이터만 인용한다.

---

### 기법 2: Few-shot 프롬프팅

이상적인 입출력 예시를 프롬프트에 함께 넣어 출력 형식과 품질을 고정한다.

```
[설비 진단 few-shot 구성]

예시 1 (계절 정상 케이스):
  입력: "여름, COP 1.85, 외기온 35°C, 이상 0건"
  출력: "계절적 효율 저하로 정상. 즉각 조치 불필요."
  → 목적: 여름 COP 저하를 이상으로 오진단하는 것 방지

예시 2 (발전 설비 케이스):
  입력: "태양광, 역률 -0.94"
  출력: "역송 상태로 정상. 역률 관련 조치 불필요."
  → 목적: 발전 설비 음수 역률을 고장으로 오진단하는 것 방지

실제 질문:
  입력: "{실제 설비 데이터}"
  출력: → LLM이 예시 형식을 따라 답변 생성
```

few-shot이 적용된 위치:
- 설비 진단 (cms.py) — 2개
- 이상탐지 분석 (anomaly_agent.py) — 1개
- 일일 운영 보고서 (report.py) — 2개
- 월간 KPI 트렌드 (report.py) — 1개

---

### 기법 3: RAG (Retrieval-Augmented Generation)

RAG Agent가 질문에 답하기 전에 pgvector로 관련 문서를 검색해 컨텍스트에 포함시킨다.

```python
# rag_agent.py
state.doc_context = search_documents(question)   # pgvector top-5 검색
state.meter_facts = lookup_meter_measurements(question)  # 계량기 실측값 DB 조회

# 검색 결과 + 실측값을 프롬프트에 포함
prompt = build_prompt(state)   # 문서 + 실측값 + 질문 조합
answer = llm_chat(prompt, thinking=False)
```

계량기 URN(예: `V.Z84 PF1`)이 질문에 있으면 DB에서 최신값·평균·범위를 직접 조회해 주입한다. LLM이 추측할 필요가 없다.

---

### 기법 4: 도메인 지식 주입

모델 사전학습 데이터에 없는 공장 특화 지식을 `knowledge/domain_knowledge.py`에 정리해두고 모든 프롬프트에 삽입한다.

| 지식 항목 | 내용 | 없으면 발생하는 문제 |
|----------|------|---------------------|
| 계절 COP | 여름 외기온 30°C+ 시 COP 1.7~2.0 정상 | COP 1.85를 이상으로 오진단 |
| CHP 효율 | 겨울 열 수요 증가 시 전기 효율 ±3~5%p 감소 = 정상 | 겨울 효율 저하를 고장으로 오진단 |
| 발전 설비 역률 | PV·CHP 역률 음수/저값 = 역송 정상 | 역률 -0.95를 고장으로 오진단 |
| 전력 단위 | kW(순간 전력) vs kWh(누적 에너지 량) | 단위 혼용 오류 |
| 게이트웨이 장애 구간 | 특정 기간 데이터가 인공 보정값임 | 보정 데이터를 실제 이상으로 오판 |
| Regime 이벤트 | PV 증설·COVID·CHP 변경 등 시설 변화 이력 | 구조적 변화를 이상으로 오해 |

---

### 기법 5: thinking 모드 제어

Gemma4 12B는 답하기 전에 내부 추론 과정(thinking)을 수행한다. 이걸 작업에 따라 켜고 끈다.

```python
# thinking=True: 복잡한 분석 (기본값, 속도 느리지만 품질↑)
answer = llm_chat(prompt, thinking=True)   # 진단·보고서·이상분석

# thinking=False: 단순 설명 (속도↑)
answer = llm_chat(prompt, thinking=False)  # RAG 일반 답변

# fast=True: EXAONE으로 전환 + thinking 자동 OFF
answer = llm_chat(prompt, fast=True)       # 의도 분류 (max_tokens=10)
```

---

### 기법 6: 출력 형식 강제

프롬프트에 정확한 출력 형식을 지정해 파싱 안정성을 높인다.

```python
# report.py — 일일 보고서 형식 강제
"다음 두 섹션을 정확히 아래 형식으로 출력하세요. 다른 텍스트 없이.

---SUMMARY---
(3~4문장)

---ACTIONS---
(체크리스트)"
```

백엔드에서 `---SUMMARY---`와 `---ACTIONS---`로 split해 각각 프론트에 전달한다.
형식 강제가 없으면 LLM이 매번 다른 구조로 답해 파싱이 깨진다.

---

### 기법 7: 환각 방지 지시

LLM이 없는 수치를 만들어내는 것을 명시적으로 금지한다.

```python
# reporting_agent.py
"위 KPI 데이터에 없는 수치를 만들어내지 마세요. 수치가 없으면 '데이터 없음'으로 표기하세요."

# cms.py
"진단에 사용하는 수치는 반드시 제공된 데이터(전기 서명·이상탐지 결과)에서만 인용하세요.
 제공되지 않은 수치를 추론으로 생성하지 마세요."

# anomaly_agent.py
"이상 발생 시각과 센서 수치는 반드시 위 데이터에서 직접 인용하세요."
```

---

### 기법 8: 모델 영구 적재 (keep_alive)

RunPod에 모델을 올리면 기본적으로 일정 시간 후 메모리에서 내린다. 재요청 시 다시 로딩하면 1~2분 지연이 생긴다.

```python
# llm_client.py
payload = {
    "keep_alive": -1,  # 모델을 메모리에서 내리지 않음
    ...
}
```

첫 요청 후에는 로딩 대기 없이 즉시 응답한다.

---

### 기법 9: 진단 결과 캐싱

같은 설비를 같은 날 다시 진단 요청하면 LLM 재호출 없이 캐시된 결과를 반환한다.

```python
# cms.py
cache_key = f"{eq_id}|{window_days}|{anchor.date().isoformat()}"
if cache_key in _diag_cache:
    return _diag_cache[cache_key]   # LLM 호출 없이 바로 반환
```

---

### 기법 10: LLM 실패 시 폴백

LLM 호출이 실패해도 서비스가 중단되지 않도록 폴백이 준비되어 있다.

```python
# cms.py
try:
    diagnosis = llm_chat(...)     # LLM 진단 시도
    llm_used = True
except Exception:
    diagnosis = _fallback_diagnosis(eq, total, by_type)  # 룰 기반 폴백
```

폴백은 이상 건수와 유형을 기반으로 템플릿 진단문을 생성한다.

---

## 6. 각 에이전트별 상세 — 무슨 데이터를 주고, LLM이 무엇을 하나

### 의도 분류 (Orchestrator)

```
입력:  사용자 질문 문자열
처리:  1. 키워드 룰 정규식 매칭 (LLM 없음)
       2. 룰 실패 시 EXAONE 호출 (fast=True, max_tokens=10)
출력:  "anomaly" / "rag" / "report" / "forecast" / "cms" / "off_topic"

사용 모델:  EXAONE 3.5 7.8B
성능 기법:  키워드 룰 우선 (대부분 LLM 안 씀), max_tokens=10으로 최소 비용
실제 의미:  있음 — 다음 에이전트를 결정하는 관문
```

---

### 채팅 답변 (RAG Agent)

```
입력:  질문 + pgvector 검색 문서 top-5 + 계량기 실측값(있는 경우)
처리:  도메인 지식 + 문서 + 실측값 포함 프롬프트 생성 → Gemma4 호출
출력:  자연어 답변

사용 모델:  Gemma4 12B (thinking=False)
성능 기법:  RAG, 계량기 실측값 DB 주입, 도메인 지식 주입
실제 의미:  있음 — RAG 없으면 계량기 실제 값을 알 수 없음
```

---

### 이상 원인 분석 (Anomaly Agent)

```
입력:
  - anomaly_results DB → 탐지된 이상 목록 (타입·심각도·건수)
  - 이상 시각의 센서값 → 계통전력·PV·CHP·COP·기온
  - 게이트웨이 장애 구간 정보
  - 시설 이벤트 이력 (Regime 변화)

처리:  few-shot 1개 + 전체 데이터 → Gemma4 호출 (thinking=True)

출력:
  ### 핵심 요약 (이상 건수·심각도)
  ### 유형별 분석 (원인 추정)
  ### 즉시 조치 목록

사용 모델:  Gemma4 12B (thinking=True)
성능 기법:  ML 선탐지, 센서값 풍부화, 게이트웨이 컨텍스트, few-shot
실제 의미:  있음 — ML이 탐지한 결과를 운영자가 이해할 언어로 변환하는 핵심 역할
```

---

### 설비 AI 진단 (CMS Router)

```
입력:
  - 최근 30일 이상 이력 (유형·심각도·건수)
  - 전기 서명: 전압·전류·역률·COP 실측 평균값

처리:
  - 설비 유형 분기 (발전 설비 vs 소비 설비)
  - PV·CHP면 역률 음수 정상 시스템 프롬프트 추가
  - few-shot 2개 (계절 정상 케이스 + 설비 유형별)
  → Gemma4 호출

출력:
  ### 진단 요약
  ### 추정 원인 (전기 서명 수치 인용)
  ### 권장 조치

사용 모델:  Gemma4 12B (max_tokens=600)
성능 기법:  전기 서명 주입, 설비 유형별 분기, few-shot 2개, 캐싱
실제 의미:  있음 — 설비 카드의 AI 진단 버튼 핵심 기능
```

---

### 보고서 작성 (Reporting Agent)

```
입력:
  - monthly_report 테이블 최근 6개월 KPI
    (소비량·자급률·COP·이상건수·PV·CHP)
  - 없으면 load_range()로 실시간 집계
  - 대화 히스토리 최근 6턴

처리:
  KPI 수치 포함 프롬프트 + 4섹션 형식 지시 + 환각 방지 지시
  → Gemma4 호출 (max_tokens=1500)
  → PDF 자동 생성 (reportlab)

출력:
  ## 1. 핵심 요약
  ## 2. KPI 분석
  ## 3. 이상탐지 현황
  ## 4. 개선 권고사항
  + PDF 파일 저장

사용 모델:  Gemma4 12B (thinking=True)
성능 기법:  KPI 선계산, 섹션 형식 강제, 환각 방지, 히스토리 유지
실제 의미:  있음 — PDF 생성까지 이어지는 핵심 기능
```

---

### 예측 해석 (Forecast Agent)

```
입력:
  - v84 앙상블 예측값 (LSTM×6 median + CatBoost + LightGBM + Ridge + Naive)
  - 결정론적 운영 힌트 (피크 예상 시각, 절감 가능량 등)

처리:  예측 수치 + 힌트 → Gemma4 호출 (max_tokens=1200)

출력:  예측 해석 + 운영 조언 + "[CHART:FORECAST]" 태그

사용 모델:  Gemma4 12B (thinking=True)
성능 기법:  ML 선계산, 운영 힌트 결정론적 계산, 표준 용어 주입
실제 의미:  있음 — ML 수치를 운영자 언어로 변환
```

---

### 보고서 API 직접 호출 (Report Router 5곳)

```
/report           → 월간 KPI 트렌드 3~4문장 (few-shot 1개, max_tokens=350)
/report/daily     → 일일 요약+조치 체크리스트 (few-shot 2개, max_tokens=500)
/report/balance   → 데이터 품질 2~3문장 (max_tokens=250)
/report/energy-intensity → 원단위 효율 추이 2~3문장 (max_tokens=250)
/report/billing   → 비용 현황 + 조치 권고 2~3문장 (max_tokens=300)

공통:
  - skip_ai=True 기본값 → 버튼 클릭 시에만 LLM 호출
  - 수치는 백엔드가 모두 계산 후 주입
  - 짧은 서술 생성이므로 12B로 충분
```

---

## 7. 성능에 기여한 변경 이력

| 날짜 | 변경 내용 | 효과 |
|------|----------|------|
| 2026-06-04 | cms.py 설비 유형별 few-shot 추가 | 발전 설비 역률 오진단 방지 |
| 2026-06-04 | domain_knowledge.py 계절 COP·CHP 효율 추가 | 계절 정상성 오진단 방지 |
| 2026-06-04 | orchestrator.py off_topic 키워드 라우팅 강화 | 무관 질문 즉시 차단 |
| 2026-06-08 | reporting_agent.py "하나의 보고서만" 지시 추가 | 형식 점수 6/10 → 10/10 |
| 2026-06-08 | report.py 일일 브리핑 HIGH 케이스 few-shot | daily_summary 8.2 → 9.2 |
| 2026-06-08 | report.py 월간 트렌드 수치 인용 강제 | 수치 인용률 향상 |
| 2026-06-08 | llm_client.py 듀얼 모델 라우팅 도입 | 속도·품질 동시 확보 |
| 2026-06-08 | cms.py 계절 정상성 few-shot 추가 | 여름 COP 오진단 방지 |
| 2026-06-08 | llm_client.py keep_alive=-1 적용 | 모델 재로딩 대기 제거 |

---

## 8. 인프라 — RunPod 구성

### 왜 RunPod인가

- 회사 설비 데이터를 OpenAI 등 외부 API로 보낼 수 없음 (보안)
- 로컬 CPU로는 12B 모델 추론이 너무 느림 (~수 분)
- RunPod RTX 3090 (24GB VRAM) 에서 두 모델 동시 적재 가능

### 구성 요약

```
RunPod RTX 3090
└── Ollama 서버 (OLLAMA_HOST=0.0.0.0)
    ├── gemma4:12b       (~8GB VRAM)
    └── exaone3.5:7.8b  (~5GB VRAM)
    합계 ~13GB / 24GB VRAM 사용

접속: OLLAMA_URL=https://{pod-id}-11434.proxy.runpod.net/v1
```

### Pod 재생성 시 복구 (2개 명령이면 완료)

```bash
# RunPod 내부에서
ollama pull gemma4:12b
ollama pull exaone3.5:7.8b

# .env에서 URL만 교체
OLLAMA_URL=https://{새pod-id}-11434.proxy.runpod.net/v1

docker compose restart backend
```

바꿀 것: URL 1줄
그대로 유지: 프롬프트, few-shot, 도메인 지식, 평가 결과 전부

---

## 9. 성능 병목 — 어디가 느린가

실제 응답 대기 시간 분포:

```
LSTM 추론 (이상탐지/예측)    0.5~2초   CPU
DB 쿼리                      0.1~0.5초 DB 서버
sLLM 호출 (thinking=False)   5~10초    RunPod GPU
sLLM 호출 (thinking=True)    15~30초   RunPod GPU  ← 병목
```

**체감 응답 대기의 80~90%가 sLLM 대기다.**
CPU 사양을 높여도 체감 속도 개선 효과는 제한적이다.
RunPod GPU 사양과 안정성이 훨씬 중요하다.

---

## 10. 모델 교체 방법 (한 줄)

```env
# .env
LLM_PROVIDER=ollama          # ollama / openai / anthropic / gemini
LLM_MODEL=gemma4:12b         # 품질 경로 모델
LLM_MODEL_FAST=exaone3.5:7.8b  # 속도 경로 모델
OLLAMA_URL=https://...       # Ollama 엔드포인트
```

클라우드로 전환하려면:
```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
OPENAI_API_KEY=sk-...
```
코드 수정 없이 이 두 줄만 바꾸면 전체 10곳 호출이 전환된다.

---

## 관련 문서

- [plan.md](plan.md) — sLLM 전환 전략 및 모델 선정 과정
- [model_selection.md](model_selection.md) — 6개 모델 33문항 평가 상세
- [progress.md](progress.md) — 프롬프트 엔지니어링 변경 이력
- [runpod_guide.md](runpod_guide.md) — RunPod 설치·운영 절차
- [backend_architecture.md](backend_architecture.md) — 레이어별 아키텍처 구조

---

## 관련 코드

| 파일 | 역할 |
|------|------|
| `backend/src/agents/llm_client.py` | 모든 LLM 호출 단일 진입점, 듀얼 모델 라우팅 |
| `backend/src/agents/orchestrator.py` | 의도 분류, LangGraph 그래프 조립 |
| `backend/src/agents/rag_agent.py` | RAG 검색, 계량기 실측값 조회 |
| `backend/src/agents/anomaly_agent.py` | 이상 원인 분석, 센서값 풍부화 |
| `backend/src/agents/reporting_agent.py` | 보고서 서술, PDF 생성 |
| `backend/src/agents/forecast_agent.py` | 예측 수치 해석 |
| `backend/src/agents/cms_agent.py` | 설비 상태 (LLM 없음, DB만) |
| `backend/src/api/routers/cms.py` | 설비 AI 진단 API |
| `backend/src/api/routers/report.py` | 보고서 API (5종) |
| `backend/src/knowledge/domain_knowledge.py` | 공장 특화 도메인 지식 |
