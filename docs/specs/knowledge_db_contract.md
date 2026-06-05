# Knowledge DB Contract

**갱신일:** 2026-06-02  
**상태:** Vector DB 적재 기준 문서  
**범위:** CMS 기준 문서를 Vector DB에 적재하기 위한 source tier, chunking, metadata, retrieval policy를 정의한다.

## 1. 목적

Knowledge DB는 CMS 기준 문서를 검색 가능한 knowledge source로 제공한다. 저장 대상은 프로젝트 규약과 검증된 source provenance다.

## 2. 적재 원칙

1. Source truth는 Tier 0 원문 source와 Tier 1 CMS 기준 문서다.
2. Vector DB chunk는 원문 path, section heading, content hash를 metadata로 가진다.
3. Secret, credential, local scratch output, generated cache, 비기준 산출물은 적재하지 않는다.
4. Graphify output은 `docs/specs` 관계 탐색 보조 artifact이며 source truth를 대체하지 않는다.
5. Retrieval 결과는 LLM 응답의 근거가 될 수 있지만, DB write나 canonical promotion을 승인하지 않는다.

## 3. 적재 대상 문서

```text
README.md
docs/specs/project_overview.md
docs/specs/data_platform_contract.md
docs/specs/runtime_architecture.md
docs/specs/measurement_processing_policy.md
docs/specs/meter_metadata.md
docs/specs/ontology_schema.md
docs/specs/knowledge_db_contract.md
docs/specs/llm_contract.md
docs/qa/qa_contract.md
docs/reference/source_inventory.md
docs/reference/measurement_glossary.md
```

제외 대상은 다음과 같다.

```text
.env
.venv/
_archive/
notebooks/
outputs/
__pycache__/
.pytest_cache/
raw data files
credentials
```

## 4. Source tier

| Tier | 대상 | 용도 |
|---|---|---|
| Tier 0 | Nature article, Dryad dataset, Honda RI PDF | source provenance와 원문 사실 확인 |
| Tier 1 | active CMS 기준 문서 | retrieval과 project rule grounding |
| Tier 1 보조 | HRI-EU validation code, `meters.yaml`, `issue_template.yml` | 원문 해석 보조 |
| Excluded | benchmark/model experiment, local scratch, generated cache, 비기준 산출물 | Vector DB 적재 제외 |

## 5. Chunking 기준

| 문서 유형 | chunk 단위 | 권장 size | overlap | 기준 |
|---|---|---:|---:|---|
| specs/qa/reference Markdown | heading section | 500-1200 tokens | 80-120 tokens | section title과 source path를 metadata에 포함한다. |
| ontology TTL/SHACL | class/property/individual group | 400-1000 tokens | 50 tokens | Turtle prefix와 entity URI를 보존한다. |
| source inventory | row group 또는 heading section | 400-900 tokens | 80 tokens | source tier와 provenance를 포함한다. |
| diagram `.mmd` | diagram file 단위 | 300-900 tokens | 0 | rendered image는 vector ingest에서 제외한다. |

## 6. Metadata schema

Vector row metadata는 최소 다음 field를 가진다.

```text
doc_id
chunk_id
source_path
section_heading
source_tier
doc_type
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

권장 enum은 다음과 같다.

```text
source_tier = tier0_original | tier1_cms_contract | tier1_supporting | excluded
doc_type = spec | qa | reference | ontology | diagram | source_inventory
cms_layer = source | raw | staging | candidate | canonical | reference | qa | ops | mart | service | workflow | knowledge
pipeline_stage = ingest | process | qa | promote | serve | report | review
```

## 7. Retrieval routing

| 질문 유형 | 우선 문서 |
|---|---|
| source provenance | `docs/reference/source_inventory.md` |
| data layer / DB boundary | `docs/specs/data_platform_contract.md` |
| measurement cadence / NULL / state-hold | `docs/specs/measurement_processing_policy.md` |
| meter group / redundancy | `docs/specs/meter_metadata.md` |
| ontology class/property | `docs/specs/ontology_schema.md` |
| QA / evidence / latency | `docs/qa/qa_contract.md` |
| LLM / prompt / SQL safety | `docs/specs/llm_contract.md` |
| runtime / workflow | `docs/specs/runtime_architecture.md` |
| glossary | `docs/reference/measurement_glossary.md` |

## 8. Graphify 기준

Graphify는 `docs/specs`를 대상으로 contract relationship graph를 생성한다. 목적은 기준 문서 간 관계 탐색이며, source truth는 Markdown 기준 문서와 Tier 0 source다.

권장 scope는 다음과 같다.

```text
docs/specs/*.md
docs/specs/diagrams/*.mmd
```

제외 대상은 다음과 같다.

```text
_archive/
outputs/
notebooks/
raw data
generated cache
```

## 9. 검증 기준

- 적재 대상 문서가 실제로 존재한다.
- Excluded path가 ingest 대상에 포함되지 않는다.
- 모든 chunk는 `source_path`, `section_heading`, `content_sha256`을 가진다.
- Broken link가 없다.
- `CMS` / `cms` naming을 사용한다.
- Non-CMS namespace 표현은 active 기준 문서에 남기지 않는다.
- 비기준 산출물은 Vector DB에 적재하지 않는다.
