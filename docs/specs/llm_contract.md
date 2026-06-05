# LLM Contract

**갱신일:** 2026-06-02  
**상태:** LLM runtime 기준  
**범위:** 이 문서는 LLM 배치, prompt boundary, retrieval routing, SQL safety, model selection 원칙을 정의한다. Vector DB와 Graphify 운영 기준은 `docs/specs/knowledge_db_contract.md`가 담당한다.

## 1. 원칙

1. LLM은 system-of-record가 아니다. 모든 factual claim은 source, DB, artifact, QA evidence로 추적 가능해야 한다.
2. LLM은 FastAPI normal chat path에서 무거운 workflow를 직접 실행하지 않는다.
3. Text-to-SQL은 read-only query와 SQL preview를 기본값으로 한다.
4. Promotion, DDL, deployment, bulk ETL은 LLM response만으로 실행하지 않는다.
5. Retrieval은 source tier와 document boundary를 따른다.

## 2. LLM placement

| 위치 | 허용 역할 | 금지 역할 |
|---|---|---|
| FastAPI chat | intent routing, short answer, read-only evidence summary | long-running workflow blocking execution |
| Report worker | draft generation, evidence summary | evidence 없는 결과 작성 |
| LangGraph review | QA/report/approval recommendation | direct DB write 또는 promotion |
| Text-to-SQL | read-only SQL draft, result explanation | write/DDL SQL 실행 |
| Knowledge retrieval | source-grounded context assembly | 근거 없는 응답 생성 |

## 3. Prompt boundary

Prompt는 다음 정보를 명확히 분리해야 한다.

```text
user request
retrieved source context
DB/evidence query result
policy constraints
allowed tools/actions
forbidden actions
expected output schema
```

Secrets, connection strings, credential paths, tokens는 prompt나 artifact에 보존하지 않는다.

## 4. Retrieval routing

| 질문 유형 | 우선 retrieval |
|---|---|
| source provenance | `docs/reference/source_inventory.md` |
| measurement/cadence/NULL/state-hold | `docs/specs/measurement_processing_policy.md` |
| data/schema/canonical boundary | `docs/specs/data_platform_contract.md` |
| QA/report/live readiness | `docs/qa/qa_contract.md` |
| ontology class/property | `docs/specs/ontology_schema.md` |
| meter group/redundancy | `docs/specs/meter_metadata.md` |
| Graphify/vector/chunking | `docs/specs/knowledge_db_contract.md` |
| runtime/workflow | `docs/specs/runtime_architecture.md` |

## 5. SQL safety

Text-to-SQL은 다음 guard를 적용한다.

```text
read-only by default
explicit schema allowlist
no INSERT/UPDATE/DELETE/DDL without approval
result row limit
query explanation
source table disclosure
```

Production/canonical query는 read-only evidence 조회만 허용한다. Write query는 approval workflow와 controlled worker를 거친다.

## 6. Model selection

Model selection은 task risk에 따라 결정한다.

| Task | 요구 성격 |
|---|---|
| quick chat/status | low latency, read-only grounding |
| report drafting | long context, evidence synthesis |
| QA review | conservative reasoning, citation discipline |
| SQL generation | schema grounding, deterministic guard |
| approval recommendation | high precision, explicit uncertainty |

## 7. Output contract

LLM output은 다음을 구분한다.

```text
confirmed facts
assumptions
open questions
blocked actions
recommended next step
source/evidence references
```

불확실한 내용은 추정으로 표시하고, 확인 가능한 내용은 도구나 source evidence로 검증한다.

## 8. Verification

- LLM이 제안한 write/DDL/promotion이 자동 실행되지 않는다.
- SQL route가 read-only guard를 통과한다.
- Retrieval source가 질문 유형과 일치한다.
- Report/chat answer가 evidence packet 또는 source document를 참조한다.
