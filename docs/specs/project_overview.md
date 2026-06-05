# CMS 프로젝트 개요

**갱신일:** 2026-06-02  
**상태:** Vector DB 적재 기준 개요  
**범위:** CMS project architecture, repository map, naming, verification baseline을 정의한다.

## 1. 범위

CMS 프로젝트는 건물·설비 계량 시계열을 수집하고, 품질 검증과 보정 경계를 통과한 데이터만 분석·서빙·보고에 사용하는 system이다. Architecture는 Data plane, Service plane, Workflow plane을 분리한 contract-first 구조를 기준으로 한다.

Active runtime package는 `src/cms`이며, 문서와 schema namespace는 `CMS` / `cms`를 기준으로 한다.

## 2. Architecture

Data plane은 source archive와 live/replay input을 수집한 뒤 staging 또는 raw buffer에 보존하고, processor와 QA gate를 통해 candidate output과 QA evidence를 만든다. Candidate는 canonical이 아니며, dashboard나 model dry-run에서 serving preview로만 사용할 수 있다. Canonical layer로의 승격은 `ops.promotion_request`, QA evidence, approval, controlled promotion role을 통과한 뒤에만 허용한다.

Service plane은 FastAPI를 중심으로 빠른 상태 조회, read-only query, manual job registration, report artifact download, lightweight chat interface를 제공한다. LangGraph는 synchronous chat path에 배치하지 않는다.

Workflow plane은 Airflow, scheduler, background worker가 소유한다. 정기 report, replay/backfill planning, QA evidence packet, approval review, incident review, model inference dry-run 검증은 workflow plane에서 실행하며, LangGraph는 이 plane의 optional async review layer로 사용한다.

## 3. Repository map

```text
SKN25-FINAL-4Team/
├── docker/                         # local PostgreSQL/TimescaleDB development stack
├── docs/
│   ├── ontology/                   # RDF/OWL/SHACL ontology artifacts
│   ├── qa/                         # 통합 QA contract
│   ├── reference/                  # source inventory and measurement glossary
│   └── specs/                      # overview, data platform, runtime, measurement, knowledge, LLM, ontology, metadata specs
├── scripts/                        # dry-run, smoke, scratch guard, ontology, contract verification scripts
├── src/cms/                        # CMS Python package
└── tests/                          # unit/integration tests
```

## 4. Naming 및 용어

| 대상 | 기준 |
|---|---|
| Project-facing name | `CMS` |
| Python package | `src/cms` |
| PostgreSQL database/user | `cms` |
| Canonical table | `canonical.measurement_1min`, `canonical.measurement_15min`, `canonical.measurement_1h` |
| Reference corrected/resampled table | `reference.corrected_resampled_15min`, `reference.corrected_resampled_1h` |
| Missing observation 표현 | `NULL`, `missing observation`, `missing_points` |

## 5. Vector DB 적재 대상 문서

| 문서 | 역할 |
|---|---|
| `docs/specs/data_platform_contract.md` | data layer와 DB boundary |
| `docs/specs/runtime_architecture.md` | service/workflow boundary |
| `docs/specs/measurement_processing_policy.md` | measurement 처리 정책 |
| `docs/specs/meter_metadata.md` | meter metadata |
| `docs/specs/ontology_schema.md` | ontology schema와 역량질문 |
| `docs/specs/knowledge_db_contract.md` | Vector DB ingest 기준 |
| `docs/specs/llm_contract.md` | LLM/retrieval/SQL safety 기준 |
| `docs/qa/qa_contract.md` | QA/evidence 기준 |
| `docs/reference/source_inventory.md` | source tier 기준 |
| `docs/reference/measurement_glossary.md` | measurement 용어집 |

## 6. 검증 baseline

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with pytest --no-project python -m pytest -q
```

검증 후 생성된 cache는 active tree에 남기지 않는다.
