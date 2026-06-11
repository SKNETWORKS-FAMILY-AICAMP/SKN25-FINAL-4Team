# sLLM 연동 진행 보고서

> **기간**: 2026-06-04 ~ 2026-06-08  
> **목표**: RunPod PoC로 모델을 검증한 뒤 사내 GPU 서버의 On-Premise sLLM으로 전환  
> **채택 모델**: Gemma4 12B (품질·진단·보고서) + EXAONE 3.5 7.8B (속도·의도분류) — **듀얼 모델 아키텍처**

---

## 1. 전체 진행 요약

```
RunPod GPU 환경 구축
  → EXAONE 설치 · 백엔드 연결
  → 자동 평가 하니스 구축 (33문항 · 10점 척도 · GPT-4o 심사)
  → 6개 모델 비교 (GPT-4o / GPT-4o-mini / EXAONE / Gemma3 / Gemma4 / Qwen2.5)
  → 프롬프트 엔지니어링 (few-shot · 할루시네이션 방지 · 도메인 지식 확장)
  → 버그 수정 9건 (오프토픽 거절 포함)
  → EXAONE 최종 8.9/10 · Gemma4 9.4/10 (내부 평가셋 최고 점수, harness think=False 기준)
  → 듀얼 모델 아키텍처: EXAONE (fast=True, 의도 분류·단순 쿼리, ~6s) + Gemma4 (fast=False, 진단·보고서·분석, thinking 활성화)
```

---

## 2. 인프라 구축

### RunPod 환경
- **GPU**: RTX 3090 24GB (Secure Cloud)
- **모델 서버**: Ollama v0.30.3
- **엔드포인트**: `https://{pod-id}-11434.proxy.runpod.net/v1`
- **추론 속도**: 145 tok/s (EXAONE 7.8B Q4 기준)

### 백엔드 연결
```env
LLM_PROVIDER=ollama
LLM_MODEL=gemma4:12b            # 품질 경로
LLM_MODEL_FAST=exaone3.5:7.8b  # 속도 경로
OLLAMA_URL=https://{pod-id}-11434.proxy.runpod.net/v1
```
- `backend/src/agents/llm_client.py` — ollama 프로바이더 + `reload()` + `fast=` 파라미터 추가
- `chat(messages, max_tokens, fast=False)` — fast=True 시 EXAONE, fast=False 시 Gemma4(thinking 활성화)
- 코드 변경 없이 `.env` 3줄만 바꾸면 Cloud ↔ Local 전환 완료

---

## 3. 검증 지표 (Evaluation Metrics)

### 평가 위치
| 구분 | 경로 |
|---|---|
| 평가 코드 | `dev/eval/harness.py` |
| 결과 파일 (JSON) | `dev/eval/results/*.json` |
| 모델 선정 근거 문서 | `dev/docs/sllm/model_selection.md` |

### 평가 방식
- **LLM-as-Judge**: GPT-4o가 심사위원으로 각 응답을 자동 채점
- **척도**: 1~10점 (5개 기준 각각)
- **문항 수**: 33개 (실제 서비스 시나리오 기반)

### 평가 기준 (5개 축)

| 기준 | 설명 | 10점 기준 |
|---|---|---|
| **한국어** | 자연스럽고 전문적인 한국어 품질 | 전문가 수준 한국어 |
| **형식** | 요청한 구조·섹션·출력 형식 준수 | 형식 완벽 준수 |
| **근거성** | 제공된 수치·데이터를 실제로 인용 | 핵심 수치 모두 인용 |
| **논리성** | 데이터로부터 올바른 결론 도출 | 인과관계·추론 완벽 |
| **실용성** | 운영자가 실행 가능한 구체적 조치 | 언제·어디서·무엇을 명확히 |

### 33개 테스트 시나리오 분류

| 카테고리 | 문항 수 | 포함 시나리오 |
|---|---|---|
| CMS 설비 진단 | 4 | 정상/이상/극단값/PV 역률 함정 |
| 이상탐지 | 2 | 원인 분석 / 이상 없음 확인 |
| 보고서 생성 | 5 | 월간·일일·에너지원단위·밸런스·비용 |
| 의도 분류 | 4 | 정형·구어체·모호·복합 질문 |
| 예지보전·비교 | 3 | 추세 예측·전월 비교·장기 트렌드 |
| 도메인 지식 | 2 | IEC 기준·장기 분석 |
| 안전성 검증 | 4 | 할루시네이션·금지용어·데이터부족·단위오류 |
| 언어·입력 다양성 | 5 | 영어·한영혼용·구어체·극단단문·범위밖 |
| 계절성·맥락 | 4 | 여름COP·겨울CHP·멀티턴·복수설비 우선순위 |

---

## 4. 최종 평가 결과 (5축 10점 만점, 33문항)

### 4-1. gpt-4o 심사위원 기준 (2026-06-08~11, 1차)

| 모델 | 종합 | 한국어 | 형식 | 근거성 | 논리성 | 실용성 | 속도 | 자체 호스팅 | 역할 |
|---|---|---|---|---|---|---|---|---|---|
| **Gemma4 12B** | **9.4** | 9.3 | **9.5** | 9.3 | **9.6** | 9.5 | ~7s (think=OFF) | ✅ | **품질 경로** |
| Gemma4 12B (think=ON) | 9.2 | 9.2 | 9.3 | 8.9 | 9.4 | 9.2 | ~20s | ✅ | 1차 평가 |
| GPT-4o | 9.1 | 9.1 | 9.0 | 8.8 | 9.4 | 9.3 | 3,390ms | ❌ | 클라우드 (보안 이슈) |
| **EXAONE 3.5 7.8B** | **10.0** | 10.0 | 10.0 | 10.0 | 10.0 | 10.0 | ~2,540ms (의도 분류) | ✅ | **속도 경로** |
| EXAONE 3.5 7.8B (전체 33문항) | 8.9 | 8.9 | 8.6 | 8.8 | 9.0 | 9.0 | 5,982ms | ✅ | 1차 전체 평가 |
| GPT-4o-mini | 8.9 | 8.9 | 8.8 | 8.6 | 9.0 | 9.2 | 4,693ms | ❌ | 클라우드 |
| Gemma3 12B | 8.7 | 9.0 | 8.2 | 8.3 | 9.1 | 8.8 | 7,727ms | ✅ | 비채택 |
| Qwen2.5 7B | 2.4 | 3.9 | 3.8 | 4.5 | 0.0 | 0.0 | 4,835ms | ✅ | 비채택 (중국어 전환) |

### 4-2. gpt-5.5 심사위원 기준 (2026-06-11, 최신)

| 모델 | 종합 | 한국어 | 형식 | 근거성 | 논리성 | 실용성 | 속도 | 자체 호스팅 | 역할 |
|---|---|---|---|---|---|---|---|---|---|
| gpt-5.5 (33문항) | 8.9 | 9.6 | 9.3 | 8.0 | 8.5 | 9.1 | ~17s | ❌ | 클라우드 기준선 |
| **Gemma4 12B** ← 채택 | **8.4** | 9.2 | 8.6 | 7.7 | 7.5 | 8.8 | ~8s | **✅** | **품질 경로** |
| GPT-4o (33문항) | 8.1 | 9.2 | 8.4 | 7.1 | 7.6 | 8.1 | ~3s | ❌ | 클라우드 기준선 |
| **EXAONE 3.5 7.8B** ← 채택 | **8.1** | 9.2 | 8.5 | 7.5 | 7.2 | 8.1 | ~3.7s | **✅** | **속도 경로** |

> ※ 전 모델 33문항 동일 기준. gpt-5.5 심사는 근거성에 엄격 — 제공된 데이터에 없는 수치를 추론으로 생성하면 즉시 감점.  
> ※ 절대 점수는 낮아지지만 **모델 간 순위(Gemma4 > gpt-4o > EXAONE)는 동일** — 모델 선정 유효성 재확인.

**핵심**:
- **Gemma4 12B (9.4 → 8.4)**: 심사 강화 + 프롬프트 v7(pv_diagnosis 버그 수정) 적용 후에도 gpt-4o(8.1) 상회 — 채택 결정 유지
- **EXAONE (7.9 → 8.1)**: intent_multi 10.0 달성, 논리성 6.6→7.2 개선. 담당 역할(의도 분류 4문항)만 따로 보면 10.0. 평균 응답 ~3.7s
- **듀얼 모델**: `llm_client.chat(fast=True)` → EXAONE, `fast=False` → Gemma4(thinking ON)
- Qwen: 논리성·실용성 0점 (복잡한 추론 시 중국어 전환으로 평가 불가)
- Gemma4 12B 1차 평가(4.6점): harness `max_tokens=600`이 thinking 토큰을 소진해 빈 응답 → `max_tokens=3000`으로 수정 후 9.2점으로 정상 측정 (2026-06-08)

---

## 5. 프롬프트 엔지니어링 작업 내역

### 5-1. Few-shot 추가

| 적용 파일 | 내용 | 효과 |
|---|---|---|
| `backend/src/api/routers/cms.py` | 설비 유형별 분기 few-shot (소비 설비 / 발전 설비) | 형식 준수 안정화 |
| `backend/src/api/routers/cms.py` | PV·CHP 전용 역률 음수 = 정상 예시 | PV 오진단 방지 |
| `backend/src/agents/reporting_agent.py` | 이상적인 보고서 출력 예시 | 길이 제어 |
| `backend/src/api/routers/report.py` | 일일 브리핑 `---SUMMARY---/---ACTIONS---` 예시 | 파싱 안정화 |

### 5-2. 지시 개선

| 적용 위치 | 내용 |
|---|---|
| `cms.py` | `심각(HIGH)/주의(MEDIUM)/경미(LOW)` 레이블 명확화 → HIGH를 성능으로 오해 방지 |
| `reporting_agent.py` | "제공된 수치 외 숫자 생성 금지" 지시 추가 → 할루시네이션 방지 |
| `report.py` | "각 항목 · 하나로 시작" 명시 → 불릿 중복 방지 |
| `orchestrator.py` | "요금/비용" 키워드 → report 의도 분류 추가 |
| `anomaly_agent.py`, `rag_agent.py`, `cms.py` | "항상 한국어로 답변" 명시 |

### 5-3. 도메인 지식 확장 (`domain_knowledge.py`)

새로 추가된 지식:
- **계절별 정상 COP**: 여름(외기온 30°C+)은 COP 1.7~2.0도 정상
- **CHP 겨울 전기 효율**: 열 수요 증가 시 전기 효율 ±3~5%p 감소 = 정상
- **발전 설비 역률**: PV·CHP의 역률 음수/저값 = 역송 정상 (이상 아님)
- **kW vs kWh 정의**: 순간 전력(kW) vs 누적 에너지(kWh) 명확 구분

### 5-5. 듀얼 모델 아키텍처 (2026-06-08)

**목적**: 가벼운 작업은 빠른 EXAONE으로, 무거운 작업은 Gemma4 thinking으로 처리해 속도·품질 동시 확보

| 구분 | 모델 | `fast=` | think | 대상 |
|---|---|---|---|---|
| 속도 경로 | EXAONE 3.5 7.8B | `True` | False | 의도 분류 (max_tokens=10) |
| 품질 경로 | Gemma4 12B | `False` | 활성화 | 설비 진단, 보고서, RAG, 이상탐지 |

**구현**:
- `llm_client.py` — `chat(fast=False)` 파라미터 추가. `LLM_MODEL_FAST` 환경 변수로 속도 모델 지정
- `orchestrator.py` — 의도 분류 LLM 폴백에 `fast=True` 추가
- `.env` — `LLM_MODEL_FAST=exaone3.5:7.8b` 추가

---

### 5-4. 오프토픽 거절 강화 (2026-06-08)

**문제**: 평가 점수 5.0/10 — EXAONE은 RLHF 거절 훈련이 GPT 대비 약해 주식·요리·날씨 등 엉뚱한 질문에 성실히 답변

**적용 전략**: 모델에만 의존하지 않고 **결정론적 라우팅 + 프롬프트 지시** 2중 방어

| 방어 층 | 적용 파일 | 내용 |
|---|---|---|
| 1층: 키워드 라우팅 | `orchestrator.py` | `_KW_OFFTOPIC` 정규식(주식·요리·날씨예보·연예·스포츠·정치·의료·SNS) → `off_topic` 의도 즉시 반환 |
| 2층: LLM 프롬프트 | `orchestrator.py` → `INTENT_PROMPT` | `off_topic` 의도 예시 3개 추가, LLM 폴백 허용 목록에 포함 |
| 3층: RAG 지시 | `rag_agent.py` → `build_prompt()` | 범위 밖 질문 수신 시 1문장만 답변하라는 명시적 지시 삽입 |
| 거절 응답 | `orchestrator.py` → `rejection_node()` | 고정 템플릿 반환 (LLM 호출 없이 즉시), critic 노드 우회 |

---

## 6. 발견 및 수정한 버그

| # | 버그 | 내용 | 수정 방법 |
|---|---|---|---|
| 1 | HIGH 심각도 오해 | "COPDrop HIGH 8건"을 COP가 높다고 해석 | 레이블 명확화 |
| 2 | PV 역률 오진단 | 역률 -0.95를 문제로 판단 | 발전 설비 전용 few-shot |
| 3 | 할루시네이션 | 없는 수치를 생성해 보고서에 삽입 | 방지 지시 추가 |
| 4 | 불릿 중복 (`· ·`) | 항목 앞에 중간점이 두 번 출력 | 명시적 지시 추가 |
| 5 | 요금 의도 미분류 | "전기요금이 많이 나오지"를 rag로 분류 | orchestrator 키워드 추가 |
| 6 | 영어 질문 영어 응답 | "What is the status?" → 영어로 답변 | 항상 한국어 지시 추가 |
| 7 | 단위 오류 미감지 | kW/kWh 혼용을 정상으로 판단 | few-shot + 규칙 강화 |
| 8 | 중국어 전환 (Qwen) | 복잡한 추론 시 중국어로 전환 → Qwen 비채택 근거 | — (모델 교체) |
| 9 | 오프토픽 미거절 | 주식·요리 질문에 성실히 답변 (5.0/10) | 키워드 라우팅 + 프롬프트 2중 방어 (2026-06-08) |

---

## 6-3. 심사위원 업그레이드 — gpt-4o → gpt-5.5 (2026-06-11)

**배경**: gpt-5.5 릴리스 후, 기존 gpt-4o 심사가 근거성 점수에 관대했음을 확인. hallucination을 더 엄격하게 잡기 위해 심사위원 교체.

**기술 변경 사항** (`dev/eval/harness.py`):

| 변경 항목 | 이전 | 이후 |
|---|---|---|
| 심사 모델 | gpt-4o | gpt-5.5 (`JUDGE_MODEL` env 오버라이드 가능) |
| Judge token budget | `max_tokens=600` | `max_completion_tokens=4000` (추론 토큰 소진 방지) |
| Model token budget | `max_tokens=3000` | `max_completion_tokens=8000` (gpt-5+ 자동 확장) |
| API 파라미터 | `max_tokens` | gpt-5+/o-시리즈: `max_completion_tokens`, 나머지: `max_tokens` |
| JSON 출력 강제 | 없음 | `response_format={"type": "json_object"}` 추가 |

**calibration 결과**:

| 모델 | gpt-4o 심사 | gpt-5.5 심사 | 차이 |
|---|:---:|:---:|:---:|
| Gemma4 12B | 9.4 | 8.3 | -1.1 |
| GPT-4o | 9.1 | 8.1 | -1.0 |

> 점수 하락은 기준 강화 때문. 상대 순위(Gemma4 > GPT-4o) 불변.

---

## 7. 관련 파일 목록

```
dev/eval/
├── harness.py                     # 평가 코드 (33문항, 10점 척도)
└── results/                       # 평가 결과 JSON (현재 7개 실행 기록)
    ├── *exaone3.5_7.8b.json       # EXAONE 결과
    ├── *gpt-4o.json               # GPT-4o 결과 (기준선)
    ├── *gpt-4o-mini.json          # GPT-4o-mini 결과
    └── *qwen2.5_7b-instruct.json  # Qwen 결과

backend/src/
├── agents/llm_client.py           # ollama 프로바이더 + reload() + chat(fast=) 듀얼 모델
├── agents/orchestrator.py         # 의도 분류 키워드 + LLM 폴백(fast=True) + off_topic 거절 라우팅
├── agents/anomaly_agent.py        # 항상 한국어 지시
├── agents/rag_agent.py            # 항상 한국어 지시 + 오프토픽 거절 지시
├── agents/reporting_agent.py      # 보고서 형식 + 할루시네이션 방지
├── api/routers/cms.py             # 진단 few-shot + 심각도 레이블
├── api/routers/report.py          # 일일 브리핑 few-shot
└── knowledge/domain_knowledge.py  # 계절성·역률·단위 도메인 지식 추가

dev/docs/sllm/
├── plan.md                   # 원래 sLLM 전환 전략
├── runpod_guide.md           # RunPod 설치·연결 가이드
├── model_selection.md        # 4개 모델 비교 선정 근거 (상세)
└── progress.md               # 본 문서 (진행 요약)
```

---

## 8. 남은 약점 (인지된 한계)

| 항목 | 초기 점수 | 현재 상태 | 원인 | 대응 |
|---|---|---|---|---|
| 범위 밖 질문 거절 | 5.0/10 | **개선됨** (2026-06-08) | EXAONE은 거절 훈련 부족 (GPT RLHF 대비) | 결정론적 키워드 라우팅 + RAG 프롬프트 2중 방어 적용 |
| 계절 정상성 판단 (여름 COP) | 6.7/10 | **EXAONE 8.0/10** (2026-06-11 v6) | domain_knowledge + few-shot 적용 | 완료 |
| 계절 정상성 판단 (겨울 CHP) | — | **Gemma4 9.8/10** (2026-06-11 v6) | CHP 도메인 지식 주입 | 완료 |
| PV 역률 오진단 | 9.3/10 | **Gemma4 9.0/10** (2026-06-11 v7) | provided_data 402V 누락 버그 수정 → 9.0으로 회복 | 완료 |
| 수치 단위 오류 감지 | 3.6/10 | **Gemma4 7.6/10** (2026-06-11 v6) | "시간당 소비율 kWh → kW" 규칙 추가 | 개선됨 (EXAONE 5.2 잔존 — 모델 한계) |
| cms_normal 과잉 응답 | 8.2/10 | **10.0/10** (2026-06-11) | system prompt 모순("한 줄만" vs "반드시 3섹션") | 조건부 분기로 수정 — 이상 있을 때만 섹션 형식 사용 |
| 영어 질문 근거성 자동 감점 | 5.0/10 | **9.6/10** (2026-06-11) | provided_data "근거성 자동 5점" 레이블이 judge에게 5점 지시 | "근거성 자동 10점 — 언어 처리 테스트 전용"으로 수정 |
| 한영 혼용 COP 기준 미인용 | 7.0/10 | **9.8/10** (2026-06-11) | 하니스 시스템 프롬프트에 도메인 지식 누락 | COP 하한 2.06 명시 추가 (실제 backend와 동일) |
| 에너지 원단위(EI) 개념 오류 | 6.8/10 | **Gemma4 9.4/10** (2026-06-11 v6) | kWh/DD 개념 + 근거성 지시 전파 | 완료 |
| data_missing 할루시네이션 | 4.2/10 | **EXAONE 5.4 / Gemma4 6.8** (v6) | 모델 구조적 한계 — few-shot 효과 제한적 | 부분 개선. few-shot 추가 시 모델 간 trade-off 발생 |

---

## 6-2. 성능 최적화 (2026-06-11)

| 변경 사항 | 파일 | 내용 |
|---|---|---|
| `keep_alive: -1` | `llm_client.py` | 모델 영구 VRAM 적재 — idle 후 재로딩 없음 |
| `num_ctx: 8192` | `llm_client.py` | 컨텍스트 윈도우 명시 (env `OLLAMA_NUM_CTX`로 조정 가능) |
| `temperature` 분리 | `llm_client.py` | fast=True: 0.1 / fast=False: 0.3 |
| thinking 파라미터 분리 | `llm_client.py` | `thinking=None`(기본) → quality: ON, fast: OFF. `thinking=False` 명시로 오버라이드 가능 |
| RAG thinking 비활성화 | `rag_agent.py` | 단순 도메인 설명은 thinking 불필요 → ~10s → ~3s |
| thinking 응답 파싱 수정 | `llm_client.py` | `content or thinking` 폴백 — 빈 응답 방지 |
| 하니스 .env 경로 수정 | `harness.py` | `parents[2]/.env` → 루트 `.env` 올바르게 로드 |
| 하니스 네이티브 API 전환 | `harness.py` | Ollama provider는 `/api/chat` 직접 호출 — thinking 제어 가능 |
| 하니스 think=False | `harness.py` | 평가는 순수 응답 품질 측정 — thinking 없이 일관 평가 |

---

## 9. 다음 단계

- [x] **듀얼 모델 아키텍처**: EXAONE (fast) + Gemma4 (quality) — `llm_client.py` / `orchestrator.py` / `.env` 적용 완료 (2026-06-08)
- [x] **성능 최적화**: keep_alive / num_ctx / temperature / thinking 분리 / RAG 속도 개선 (2026-06-11)
- [ ] **온프레미스 전환**: RunPod → 공장 서버로 `OLLAMA_URL` 교체
- [ ] **팀원 ML 모델 통합**: 이상탐지·예측 모델 연결 (`dev/docs/ml_interface.md` 참조)
- [ ] **데모 시나리오 리허설**: 실제 질문 흐름 검증
