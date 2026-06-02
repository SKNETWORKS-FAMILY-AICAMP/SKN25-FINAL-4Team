# Knowledge DB Contract

**Updated:** 2026-06-02
**Status:** 기준 문서. 실제 AWS DDL, vector row 적재, Hermes MCP config 변경은 별도 승인/작업으로 수행한다.
**Scope:** CMS 문서 기반 vector DB 기준, AWS PostgreSQL `pgvector` 준비, Graphify 기반 project graph와 MCP hook 운영 기준.

## 1. 목적

CMS agent/RAG/SQLLM/anomaly explanation이 같은 기준 문서를 사용하도록 source tier, chunking, metadata, retrieval policy를 고정한다. Graph DB server는 별도로 구축하지 않고, project graph는 Graphify artifact와 MCP hook 후보로 운영한다.

## 2. Source tier

| Tier | Source | Vector 대상 | Graphify 대상 | 비고 |
|---|---|---|---|---|
| 0 | Nature DOI `10.1038/s41597-025-05186-3`, Honda RI PDF, Dryad DOI `10.5061/dryad.73n5tb363` | 예 | 요약/참조만 | 원문 데이터 source |
| 1 | `README.md`, `docs/specs/*.md`, `docs/qa/*.md`, `docs/reference/source_inventory.md`, `docs/reference/domain_concepts.md` | 예 | 예 | active CMS 기준 |
| 1 | `docs/ontology/ems.ttl`, `ems_shapes.ttl`, `competency_questions.md` | 예 | 예 | namespace는 legacy `ems`, meaning은 CMS metadata |
| 1 | `src/cms/**`, `scripts/ontology/**`, `scripts/verify/**`, `tests/**` | 선택 | 예 | code graph/navigation |
| 2 | HRI-EU `MonitoringDatasetAnalysis` selected files | 예 | 별도 후보 | meter grouping, issue template 보조 |
| 2 | `_archive`의 원문 Table 2/3 일부 mapping | 제한 | archive graph 별도 | active claim 전 원문 재확인 |
| 제외 | benchmark/model reports, notebooks, generated run folders, caches, `.env`, secrets | 아니오 | 아니오 | stale/민감/실험 결과 오염 방지 |

## 3. Vector DB 대상 문서

1차 vector DB는 문서 기준만 확정한다. 실제 row 적재는 별도 ingestion script에서 수행한다.

필수 ingest 후보:

```text
README.md
docs/specs/project_overview.md
docs/specs/data_contract.md
docs/specs/database_schema.md
docs/specs/pipeline_skeleton.md
docs/specs/application_skeleton.md
docs/specs/mongo_live_replay_contract.md
docs/specs/feature_spec.md
docs/specs/ontology_schema.md
docs/specs/meter_metadata.md
docs/specs/meter_measurement_cadence_policy.md
docs/specs/knowledge_db_contract.md
docs/specs/llm_pipeline_contract.md
docs/qa/anomaly_service_data_qa_contract.md
docs/qa/live_stream_qa_latency_matrix.md
docs/qa/qa_report_chat_policy.md
docs/reference/source_inventory.md
docs/reference/domain_concepts.md
docs/ontology/competency_questions.md
```

제외:

```text
.env
.venv/
_archive/ 전체 dump
reports/generated run folders
notebooks/
outputs/
__pycache__/
.pytest_cache/
raw data files
```

## 4. Chunking 기준

| 문서 유형 | chunk 단위 | 권장 size | overlap | notes |
|---|---|---:|---:|---|
| specs/qa/reference Markdown | heading section | 500-1200 tokens | 80-120 tokens | section title과 source path를 metadata에 포함 |
| ontology TTL/SHACL | class/property/individual group | 400-1000 tokens | 50 tokens | Turtle prefix와 entity URI 보존 |
| source inventory | row group 또는 heading section | 400-900 tokens | 80 tokens | source tier와 provenance 필수 |
| code docs/docstring | file-level summary + function group | 300-800 tokens | 50 tokens | code 자체보다 contract 의미 중심 |
| diagram `.mmd` | diagram file 단위 | 300-900 tokens | 0 | rendered image는 vector ingest 제외 |

## 5. Metadata schema

Vector row metadata는 최소 다음 필드를 가진다.

```text
doc_id
chunk_id
source_path
source_tier
doc_type
active_scope
legacy_scope
cms_layer
pipeline_stage
ontology_entities
data_layer
measurement_family
meter_urns
provenance_url
updated_at
content_sha256
embedding_model
embedding_dim
language
```

권장 enum:

```text
source_tier = tier0_original | tier1_active | tier2_reference | excluded
doc_type = spec | qa | reference | ontology | code_contract | source_inventory
active_scope = cms_active | source_dataset | legacy_reference | excluded
cms_layer = archive | staging | reference | canonical | qa | ops | mart | service | workflow | knowledge
```

## 6. AWS PostgreSQL pgvector 준비

실제 AWS DDL은 production/canonical write와 별개지만 DB side effect이므로 실행 전에 접속 대상, 계정 권한, rollback/teardown 기준을 확인한다. 기준 DDL은 다음이다.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS vector.document_chunk (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_tier TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    active_scope TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding_model TEXT,
    embedding_dim INTEGER,
    embedding vector(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_document_chunk_metadata
    ON vector.document_chunk USING gin (metadata);

-- embedding_dim/model이 확정된 뒤 생성한다.
-- CREATE INDEX IF NOT EXISTS idx_document_chunk_embedding_hnsw
--     ON vector.document_chunk USING hnsw (embedding vector_cosine_ops);
```

Embedding dimension은 모델 결정 후 고정한다. `intfloat/multilingual-e5-large`면 1024, compact Korean model이면 768 계열일 수 있다. dimension mismatch를 피하기 위해 table 생성 전 embedding model을 확정하거나, model별 table/view 분리를 사용한다.

## 7. Graphify 운영 기준

Graph DB server는 구축하지 않는다. Graphify artifact를 project navigation graph로 유지한다.

권장 생성 방식:

```text
active-tree mirror -> graphify update --no-cluster -> graphify-out/graph.json, graph_tree.html
```

제외:

```text
.git, .venv, .env, _archive, graphify-out, __pycache__, .pytest_cache, generated reports
```

Graphify output은 candidate knowledge다. 중요한 claim은 `docs/specs`, `docs/qa`, `docs/reference`, `docs/ontology` 원본으로 재확인한다.

## 8. Graphify MCP hook 기준

현재 설치된 `graphify` CLI는 `query`, `path`, `explain`, `update`, `tree`를 제공한다. 별도 MCP command가 노출되어 있지 않으면 다음 중 하나로 진행한다.

1. Graphify package의 MCP extra 또는 upstream MCP entrypoint가 확인되면 Hermes native MCP에 등록한다.
2. MCP entrypoint가 없으면 작은 stdio MCP wrapper를 별도 구현해 `graphify query/path/explain`을 tool로 노출한다.
3. MCP가 준비되기 전에는 Hermes built-in terminal/file tool로 `graphify query --graph graphify-out/graph.json`를 호출한다.

Hermes native MCP 설정 예시는 entrypoint가 확인된 뒤 다음 형태를 따른다.

```yaml
mcp_servers:
  graphify_cms:
    command: "/home/viowlet/.local/bin/graphify"
    args: ["mcp", "--graph", "/home/viowlet/Projects/SKN25-FINAL-4Team/graphify-out/graph.json"]
    timeout: 120
    connect_timeout: 60
```

위 `mcp` subcommand는 현재 CLI help에서 확인되지 않았다. 따라서 실제 config 적용 전 반드시 다음을 검증한다.

```bash
graphify --help | grep -i mcp
hermes mcp add graphify_cms --command ...
hermes mcp test graphify_cms
hermes gateway restart
```

## 9. Retrieval policy

| Query type | Primary retrieval | Secondary retrieval | 금지 |
|---|---|---|---|
| SQLLM schema 질문 | `database_schema.md`, `data_contract.md` | vector chunks | DDL/DML 생성 |
| 계량기 관계 | ontology helper / TTL | vector chunk | 관계 추정 |
| anomaly explanation | QA packet + ontology context | vector docs | 고장 확정 표현 |
| source dataset 질문 | source inventory + Nature/Dryad provenance | vector chunks | benchmark 결과로 원문 대체 |
| architecture 질문 | Graphify query/path | source file verification | generated graph 단독 확정 |

## 10. 검증

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py

test -f graphify-out/graph.json
graphify query "CMS data boundary" --graph graphify-out/graph.json
```
