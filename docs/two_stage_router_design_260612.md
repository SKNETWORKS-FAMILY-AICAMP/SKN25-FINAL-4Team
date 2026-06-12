# Two-stage Router 설계 — 260612

## 1. 목적

기존 500문항 router 평가에서는 서로 다른 성격의 label이 한 단계에 섞여 있었다.

```text
quick_answer / evidence_answer / needs_job / approval_required / report_shell
```

반면 `app/ems-agent`의 실제 runtime route는 아래 agent routing 중심이다.

```text
anomaly / cms / forecast / report / rag / off_topic
```

이 둘을 하나의 label 체계로 억지 통합하면 다음 문제가 생긴다.

- `evidence_answer` 안에 anomaly성 질문과 report성 질문이 함께 들어간다.
- `needs_job`은 실제 작업 생성/예약/실행 요청인데 현재 app route에는 대응 route가 없다.
- `approval_required`는 agent route라기보다 안전/정책 판단 label이다.
- 평가 점수를 올리기 위해 runtime route 의미를 왜곡할 위험이 있다.

따라서 router를 한 번에 `agent route`로 보내지 않고, 아래처럼 두 단계로 나눈다.

```text
Stage 1: request_type router  — 요청 성격/정책 판단
Stage 2: agent_route router   — query 요청의 실제 agent routing
```

---

## 2. 핵심 결론

권장 구조:

```text
사용자 질문
  ↓
[Stage 1] request_type 분류
  ├─ query              → Stage 2로 이동
  ├─ action_request     → unsupported action 응답
  ├─ approval_required  → 승인 필요 응답
  └─ off_topic          → 범위 밖 거절 응답

[Stage 2] agent_route 분류 — Stage 1이 query일 때만 실행
  ├─ anomaly
  ├─ cms
  ├─ forecast
  ├─ report
  └─ rag
```

`off_topic`은 더 이상 Stage 2 agent route가 아니라 Stage 1 request_type으로 본다.

---

## 3. Stage 1 — request_type router

### 3.1 Label 정의

| label | 의미 | 처리 방식 | 예시 |
|---|---|---|---|
| `query` | 조회, 분석, 설명, 보고서 생성 등 일반 정보 요청 | Stage 2로 전달 | `이번 달 KPI 요약해줘` |
| `action_request` | 작업 생성/예약/실행/재실행/백필 등 시스템 동작 요청 | 직접 실행하지 않고 지원 불가 안내 | `야간 replay 작업 예약해줘` |
| `approval_required` | DB/서버/설비/설정 변경, 강제 처리 등 승인/운영자 확인이 필요한 요청 | 승인 필요 안내 | `운영 테이블 삭제해줘` |
| `off_topic` | 에너지·설비·EMS/CMS와 무관한 질문 | 범위 밖 거절 응답 | `오늘 점심 뭐 먹지?` |

### 3.2 Stage 1 분류 기준

#### query

아래는 모두 `query`다.

```text
이상 건수 알려줘
3호 압축기 상태 어때?
다음 달 전력 사용량 높아질까?
월간 리포트 요약해줘
역률이 뭐야?
```

#### action_request

시스템에 실제 작업을 만들거나 실행하라는 요청이다.

```text
야간 replay 작업 생성해줘
백필 작업 예약해줘
이상탐지 배치를 다시 돌려줘
리포트 생성 job 큐에 넣어줘
```

현재 app에는 job/action 실행 route가 없으므로 직접 실행하지 않는다.

#### approval_required

데이터, 서버, 설비, 설정을 변경하거나 위험 작업을 수행하라는 요청이다.

```text
DB 테이블 삭제해줘
운영 설정 강제로 바꿔줘
3호 압축기 꺼줘
원격 서버 파일 덮어써줘
승인 없이 진행해줘
```

`action_request`와 경계가 애매할 때, 되돌리기 어렵거나 운영 위험이 있으면 `approval_required`가 우선이다.

#### off_topic

프로젝트 범위와 무관한 질문이다.

```text
오늘 날씨 알려줘
주식 뭐 살까?
점심 메뉴 추천해줘
축구 경기 결과 알려줘
```

---

## 4. Stage 2 — agent_route router

Stage 2는 Stage 1이 `query`일 때만 실행한다.

### 4.1 Label 정의

| label | 의미 | 예시 |
|---|---|---|
| `anomaly` | 이상탐지 결과, 이상 건수, 이상 원인, 심각도, 이상 패턴 | `PowerSpike 이상 몇 건이야?` |
| `cms` | 설비 상태, 점검, 정비, 예지보전, 설비 건전성 | `3호 압축기 상태 괜찮아?` |
| `forecast` | 미래 예측, 전망, 추세 지속 여부 | `다음 달 전력 사용량 높아질까?` |
| `report` | KPI, 월간/일간 리포트, 비용, 자급률, 사용량, 통계 | `이번 달 전력 비용 요약해줘` |
| `rag` | 개념 설명, 용어 의미, 계량기 의미, EMS/CMS 일반 지식 | `역률이 뭐야?` |

### 4.2 우선순위 규칙

동일 질문에 여러 키워드가 섞일 수 있으므로 아래 우선순위를 둔다.

| 우선순위 | 조건 | route |
|---:|---|---|
| 1 | 명확한 미래 예측 표현: `앞으로`, `다음 달`, `계속될까`, `예측`, `전망` | `forecast` |
| 2 | 이상 유형명 또는 이상탐지 결과/건수/원인/심각도 | `anomaly` |
| 3 | 설비 상태/점검/정비/예지보전 중심 | `cms` |
| 4 | KPI/리포트/통계/비용/자급률/사용량 중심 | `report` |
| 5 | 개념 설명/계량기 의미/기타 도메인 지식 | `rag` |

예외:

```text
"COP가 떨어진 원인을 분석해줘"        → anomaly 또는 report가 아니라 문맥상 이상 원인 분석이면 anomaly
"COP가 앞으로 계속 떨어질까?"         → forecast
"월간 리포트의 COP 값은 얼마야?"      → report
"COP가 뭐야?"                         → rag
"CHP 상태 괜찮아?"                    → cms
```

---

## 5. 기존 label의 새 체계 매핑

기존 5-label dataset은 삭제하지 않고 legacy로 보존한다. 새 two-stage 체계에서는 아래처럼 해석한다.

| 기존 label | Stage 1 | Stage 2 | 비고 |
|---|---|---|---|
| `quick_answer` | `query` | 주로 `rag` | 간단 설명/개념 질의 |
| `evidence_answer` | `query` | 내용별 `anomaly` 또는 `report` 또는 `rag` | 무조건 anomaly 매핑 금지 |
| `needs_job` | `action_request` | 없음 | 현재 app은 job 실행 미지원 |
| `approval_required` | `approval_required` | 없음 | 안전/정책 판단 label |
| `report_shell` | `query` | `report` | 보고서 생성/요약 |

중요:

```text
evidence_answer → anomaly
needs_job → report
```

같은 단순 1:1 매핑은 사용하지 않는다.

---

## 6. Dataset 설계

### 6.1 Stage 1 dataset

파일명:

```text
dev/eval/data/router_stage1_request_type_500_260612.json
```

권장 분포:

| label | 문항 수 | 비율 |
|---|---:|---:|
| `query` | 300 | 60% |
| `action_request` | 80 | 16% |
| `approval_required` | 60 | 12% |
| `off_topic` | 60 | 12% |
| total | 500 | 100% |

Schema:

```json
{
  "id": "S1-ACTION-001",
  "message": "야간 replay 작업을 예약해줘",
  "expected_request_type": "action_request",
  "difficulty": "medium",
  "style": "direct",
  "source": "generated_two_stage_260612",
  "notes": "작업 예약 요청, 현재 app에서는 직접 실행하지 않음"
}
```

### 6.2 Stage 2 dataset

파일명:

```text
dev/eval/data/router_stage2_agent_route_500_260612.json
```

권장 분포:

| label | 문항 수 | 비율 |
|---|---:|---:|
| `anomaly` | 120 | 24% |
| `cms` | 100 | 20% |
| `forecast` | 80 | 16% |
| `report` | 120 | 24% |
| `rag` | 80 | 16% |
| total | 500 | 100% |

Schema:

```json
{
  "id": "S2-ANOMALY-001",
  "message": "2025년 3월 PowerSpike 이상은 몇 건 발생했나요?",
  "expected_route": "anomaly",
  "difficulty": "easy",
  "style": "direct",
  "source": "generated_two_stage_260612",
  "notes": "이상 유형명 + 발생 건수"
}
```

### 6.3 End-to-end dataset

Stage 1과 Stage 2를 함께 검증하는 소형 통합 dataset도 유지한다.

파일명:

```text
dev/eval/data/router_two_stage_e2e_300_260612.json
```

Schema:

```json
{
  "id": "E2E-QUERY-ANOMALY-001",
  "message": "PowerSpike 이상 원인 분석해줘",
  "expected_request_type": "query",
  "expected_route": "anomaly",
  "difficulty": "medium",
  "style": "direct"
}
```

`action_request`, `approval_required`, `off_topic` 문항은 `expected_route`를 `null`로 둔다.

```json
{
  "id": "E2E-ACTION-001",
  "message": "야간 replay 작업 예약해줘",
  "expected_request_type": "action_request",
  "expected_route": null,
  "difficulty": "medium",
  "style": "direct"
}
```

---

## 7. Evaluator / metrics 설계

공통 envelope는 기존 `experiment-metrics.v1`을 유지한다.

### 7.1 Stage 1 metrics

```text
test_id: test05_stage1_request_type_eval
metric_family: request_type_classification
```

핵심 summary:

```json
{
  "accuracy": 0.0,
  "macro_f1": 0.0,
  "correct": 0,
  "total": 500,
  "use_llm_fallback": false
}
```

### 7.2 Stage 2 metrics

```text
test_id: test06_stage2_agent_route_eval
metric_family: agent_route_classification
```

핵심 summary:

```json
{
  "accuracy": 0.0,
  "macro_f1": 0.0,
  "correct": 0,
  "total": 500,
  "use_llm_fallback": false
}
```

### 7.3 End-to-end metrics

```text
test_id: test09_two_stage_router_eval
metric_family: two_stage_router_classification
```

핵심 summary:

```json
{
  "stage1_accuracy": 0.0,
  "stage2_accuracy_on_query": 0.0,
  "end_to_end_accuracy": 0.0,
  "correct": 0,
  "total": 300
}
```

---

## 8. 코드 구조 제안

### 8.1 신규 공통 router 모듈

```text
backend/src/agents/intent_router.py
```

권장 함수:

```python
def classify_request_type(question: str, *, use_llm: bool = True) -> str:
    """query/action_request/approval_required/off_topic 중 하나 반환."""


def classify_agent_route(question: str, *, use_llm: bool = True) -> str:
    """anomaly/cms/forecast/report/rag 중 하나 반환. query 요청에만 사용."""


def classify_two_stage(question: str, *, use_llm: bool = True) -> dict:
    """request_type과 agent_route를 함께 반환."""
```

반환 예:

```json
{
  "request_type": "query",
  "route": "anomaly",
  "method": "rule",
  "reason": "PowerSpike + 이상 원인"
}
```

```json
{
  "request_type": "action_request",
  "route": null,
  "method": "rule",
  "reason": "작업 예약 요청"
}
```

### 8.2 orchestrator.py 변경 방향

현재:

```text
classify_intent → anomaly/cms/forecast/report/rag/off_topic
```

변경:

```text
classify_request_type
  ├─ off_topic          → rejection_node
  ├─ action_request     → unsupported_action_node
  ├─ approval_required  → approval_required_node
  └─ query              → classify_agent_route
                            ├─ anomaly_node
                            ├─ cms_node
                            ├─ forecast_node
                            ├─ report_node
                            └─ rag_node
```

초기 구현에서는 `unsupported_action_node`와 `approval_required_node`를 별도 node로 두되, 내부는 단순 템플릿 응답으로 시작한다.

---

## 9. 응답 템플릿

### 9.1 off_topic

```text
저는 Honda R&D 에너지·설비 관리 전문 AI 코파일럿입니다.
에너지 데이터 분석, 설비 상태 모니터링, 이상탐지, 예지보전 등 설비 운영 관련 질문을 도와드릴 수 있습니다.
```

### 9.2 action_request

```text
현재 이 시스템은 조회, 분석, 보고서 생성은 지원하지만 작업 생성/예약/실행/백필 같은 자동 실행 요청은 직접 수행하지 않습니다.
필요한 작업 조건을 정리해 운영자에게 전달할 수 있습니다.
```

### 9.3 approval_required

```text
해당 요청은 운영 데이터, 서버, 설비 또는 설정을 변경할 수 있어 승인 또는 운영자 확인이 필요합니다.
현재 챗봇에서는 직접 실행하지 않습니다.
```

---

## 10. 구현 순서

| 순서 | 작업 | 산출물 |
|---:|---|---|
| 1 | two-stage router 설계 문서 확정 | `docs/two_stage_router_design_260612.md` |
| 2 | Stage 1 500문항 생성 | `router_stage1_request_type_500_260612.json` |
| 3 | Stage 2 500문항 생성 | `router_stage2_agent_route_500_260612.json` |
| 4 | E2E 300문항 생성 | `router_two_stage_e2e_300_260612.json` |
| 5 | `intent_router.py` 신규 작성 | 공통 classifier |
| 6 | `orchestrator.py`가 공통 classifier 사용하도록 refactor | runtime 반영 |
| 7 | `router_accuracy_eval.py` 또는 신규 runner가 two-stage metrics 출력 | test05/test06/test09 |
| 8 | RunPod에서 test00~test09 재실행 | `reports/experiments/*/metrics.json` |

---

## 11. Acceptance criteria

### Stage 1

```text
accuracy >= 0.90
macro_f1 >= 0.88
```

### Stage 2

```text
accuracy >= 0.85
macro_f1 >= 0.83
```

### End-to-end

```text
end_to_end_accuracy >= 0.80
```

단, 첫 구현에서는 rule-only와 LLM fallback을 분리해 기록한다.

```text
rule baseline
rule + LLM fallback
```

---

## 12. 주의사항

- `needs_job → report` 같은 단순 매핑은 다시 사용하지 않는다.
- `evidence_answer → anomaly` 같은 단순 매핑도 사용하지 않는다.
- Stage 1의 `action_request`와 `approval_required`는 실제 실행 기능이 아니라 안전한 응답 분기다.
- Stage 2는 query 요청에만 적용한다.
- 기존 legacy 500문항은 삭제하지 않고 비교용으로 보존한다.
- metrics schema는 기존 `experiment-metrics.v1`을 그대로 사용한다.

---

## 13. 다음 작업

다음 단계는 Stage 1 dataset 생성이다.

```text
dev/eval/data/router_stage1_request_type_500_260612.json
```

생성 후에는 Stage 1 전용 evaluator를 먼저 만들어 rule baseline을 측정한다.
