# LLM Pipeline Contract

**Updated:** 2026-06-02
**Status:** 기준 문서. 실제 model binding, provider credential, prompt registry 배포는 별도 작업이다.
**Scope:** CMS pipeline별 LLM 역할, prompt boundary, retrieval source, safety gate.

## 1. 원칙

- FastAPI `/chat`는 lightweight router다. 모든 요청을 LangGraph나 RAG로 보내지 않는다.
- SQLLM, QA review, report review, anomaly explanation, approval review는 서로 다른 prompt contract를 가진다.
- LLM은 DB write, DDL, canonical promotion, deployment, email send를 직접 실행하지 않는다.
- SQLLM은 SELECT-only, table whitelist, parameterized query plan을 원칙으로 한다.
- 계량기 관계와 source dataset claim은 ontology/source inventory/Graphify 결과를 원본 문서로 재확인한 뒤 말한다.

## 2. Pipeline별 LLM 배치

| Pipeline | LLM 역할 | 모델 성격 | Retrieval source | Output |
|---|---|---|---|---|
| `quick_chat_router` | route 분류, lightweight answer | fast general | minimal rules | `ChatRoute`, brief answer |
| `sql_query_planner` | read-only SQL plan 생성 | SQL 강한 model | `database_schema.md`, `data_contract.md`, vector chunks | SQL template + params + risk flags |
| `qa_review` | QA evidence packet 검토 | high-reasoning | `docs/qa/*`, `data_contract.md` | pass/warn/block verdict |
| `report_review` | 수치 claim 검증, Korean report prose | reasoning + Korean writing | report packet, QA packet | reviewed summary/report note |
| `anomaly_explanation` | meter context + QA evidence 설명 | domain-aware | ontology helper, vector docs | explanation with limitations |
| `replay_planning` | scope/risk/size 추정 | reasoning | pipeline/data contract | dry-run/scratch/approval plan |
| `approval_review` | side-effect risk classification | high-reasoning | parent evidence, policy docs | approval recommendation only |
| `source_dataset_qa` | Honda Nature/Dryad source 설명 | citation-aware | `source_inventory.md`, Nature/Dryad chunks | sourced answer |
| `graph_navigation` | project structure relation 탐색 | tool-using model | Graphify MCP/CLI | candidate paths/nodes |

## 3. Prompt boundary

### 3.1 SQLLM

System boundary:

```text
You generate read-only SQL query plans for CMS.
Allowed schemas: canonical, reference, qa, ops, mart only when explicitly documented.
Forbidden: INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, GRANT, REVOKE, COPY, CALL, DO.
Use parameter placeholders. Return JSON: sql, params, tables, assumptions, risk_flags.
If the requested table or column is not documented, return blocked_unknown_schema.
```

추가 규칙:

- `canonical.measurement_*`는 observed facts only다.
- `reference.corrected_resampled_*`는 audit/comparison source이며 service truth로 답하지 않는다.
- time window는 half-open interval `start_at <= ts < end_at`를 사용한다.
- confidence가 낮으면 확정 수치가 아니라 query plan preview로 표시한다.

### 3.2 QA review

```text
Review the QA evidence packet against CMS observed-canonical rules.
BLOCK if interpolation, forward-fill, backfill, corrected_resampled substitution, hard-coded coverage_ratio=1.0, or lost provenance appears in canonical observed lane.
WARN if coverage is low, cadence is unknown, source family is unclear, or evidence is partial.
Return JSON: verdict, blocking_reasons, warnings, required_human_checks, source_refs.
```

### 3.3 Report review

```text
Validate numeric claims before writing prose.
Every numeric claim needs source_refs and confidence.
If QA is blocked, produce report shell only and explain missing evidence.
Write Korean prose, keep commands/table names/API names in English.
Do not send email or publish; output artifact text only.
```

### 3.4 Anomaly explanation

```text
Explain anomaly candidates, not confirmed failures.
Ground meter relationships in ontology helper or source document chunks.
Separate meter/data-quality issue, equipment candidate, and unknown.
Do not infer physical wiring, control state, alarm logs, or setpoints not present in source evidence.
```

### 3.5 Approval review

```text
Classify risk and required approval fields.
Never execute the approved action.
Return recommendation, risk_summary, missing_evidence, allowed_next_step.
```

## 4. Retrieval routing

| Route | First lookup | If insufficient |
|---|---|---|
| 계량기 group/role/redundancy | `src/cms/ontology` helper / TTL | vector `ontology_schema`, `meter_metadata` |
| Honda source fact | `source_inventory.md` + Tier 0 chunks | original DOI/PDF/Dryad recheck |
| SQL schema | `database_schema.md` | reject unknown schema |
| QA policy | `docs/qa/*` | `data_contract.md`, `cadence_policy.md` |
| architecture | Graphify query/path | read source files |

## 5. Prompt registry 후보

1차 파일 registry를 만든다면 다음 key를 사용한다.

```text
quick_chat_router.v1
sql_query_planner.v1
qa_review.v1
report_review.v1
anomaly_explanation.v1
replay_planning.v1
approval_review.v1
source_dataset_qa.v1
graph_navigation.v1
```

Prompt registry는 code에 박기보다 `docs/specs/llm_pipeline_contract.md`와 별도 `src/cms/contracts` constants로 나눌 수 있다. 실제 provider/model name은 environment/config에서 주입한다.

## 6. Model selection note

현재 단계에서 특정 vendor/model을 고정하지 않는다. 대신 capability를 고정한다.

| Task | Capability |
|---|---|
| SQLLM | schema following, SQL safety, JSON output |
| QA/approval | long-context reasoning, strict checklist adherence |
| report/anomaly | Korean technical prose, citation discipline |
| graph navigation | tool use, path verification |

## 7. Verification

- SQLLM prompt는 DDL/DML keywords를 거절하는 unit test를 가져야 한다.
- QA review prompt는 corrected/reference leakage와 hard-coded coverage를 block해야 한다.
- Anomaly explanation은 고장 확정 표현을 금지해야 한다.
- Approval review는 `side_effects_executed=false`를 invariant로 유지해야 한다.
