# CMS Source Inventory

**갱신일:** 2026-06-02  
**상태:** Vector DB source tier 기준 문서  
**범위:** CMS 기준 문서와 Vector DB에 반영할 source tier, 포함/제외 기준, chunk 후보를 정의한다.

## 1. 기준 원칙

- Active 문서 표기와 runtime namespace는 `CMS` / `cms`를 사용한다.
- 원문 논문과 Dryad dataset은 Tier 0 source다.
- Benchmark, model experiment, 발표용 중간 산출물은 source truth가 아니다.
- Vector DB는 project 규약 문서를 검색하기 위한 지식 저장소이며, raw data lake나 experiment log 저장소가 아니다.
- Graphify는 `docs/specs` 중심의 contract graph 생성 보조 도구이며, source truth는 Markdown 기준 문서와 원문 source다.

## 2. Tier 0 source

| Source | Provenance | CMS에서의 용도 |
|---|---|---|
| Nature Scientific Data article | DOI `10.1038/s41597-025-05186-3` | dataset scope, facility, meter inventory, measurement dictionary, issue semantics의 최상위 원문 |
| Honda RI PDF | `https://www.honda-ri.de/pubs/pdf/6282.pdf` | Nature article 원문 text 확인용 |
| Dryad dataset | DOI `10.5061/dryad.73n5tb363` | archive manifest, data file layout, license, compressed source provenance |

Nature 원문 제목은 **A Real-World Energy Management Dataset from a Smart Company Building for Optimization and Machine Learning**이다. 대상은 Honda R&D Europe Offenbach facility의 2018-2023 실측 데이터이며, offices, workshops, server rooms, vehicle emissions lab, PV system, CHP, heating/ventilation/air conditioning, central cooling 설비를 포함한다.

## 3. Tier 1 CMS 기준 문서

| Source | 역할 |
|---|---|
| `readme.md` | 저장소 목적, 실행 기준, 주요 문서 index |
| `docs/specs/project_overview.md` | CMS architecture 개요 |
| `docs/specs/data_platform_contract.md` | source, raw, staging, candidate, canonical, reference, mart boundary |
| `docs/specs/runtime_architecture.md` | Data/Service/Workflow plane과 side-effect boundary |
| `docs/specs/measurement_processing_policy.md` | cadence, expected/coverage, NULL/state-hold, measurement 처리 정책 |
| `docs/specs/meter_metadata.md` | 81 meter classification, role, redundancy, source metadata |
| `docs/specs/ontology_schema.md` | ontology class/property와 역량질문 |
| `docs/specs/knowledge_db_contract.md` | Vector DB, chunking, Graphify 기준 |
| `docs/specs/llm_contract.md` | LLM 역할, prompt boundary, retrieval routing, SQL safety |
| `docs/qa/qa_contract.md` | QA, evidence level, report/chat route, live/replay latency 기준 |
| `docs/reference/measurement_glossary.md` | 전기/열/기상 measurement 용어집 |
| `docs/ontology/cms.ttl`, `docs/ontology/cms_shapes.ttl` | CMS namespace RDF/SHACL artifact |

## 4. Tier 1 보조 source

HRI-EU `MonitoringDatasetAnalysis` repository는 원문 dataset validation code와 issue/meter metadata 보조 source다. 권장 활용 대상은 다음이다.

| 파일 | 용도 |
|---|---|
| `readme.md` | validation, reduced dataset, downsampling code provenance |
| `meters.yaml` | meter category grouping 보조 확인 |
| `issue_template.yml` | issue/event metadata schema 보조 확인 |
| `src/ReadFiles.py` | data file naming/loading convention reference |

이 repository는 Nature article과 Dryad dataset보다 우선하지 않는다.

## 5. 원문에서 CMS에 반영할 facts

### Meter / hardware / category

- Tables 2-3은 meter URN, hardware type, description의 primary source다.
- `(E)` 표기는 같은 line을 측정하는 distinct URN 2개가 설치됨을 뜻한다.
- `ZE` meters는 2023년 독일 calibration legislation 대응 설치 맥락이다.
- hardware model vocabulary 후보는 `ABB-B24`, `Janitza UMG 96 RM-E`, `Janitza UMG 96 PA MID+`, `Socomec I35/DIRIS I35`, `SensorStar 2C`, `SensorStar 2/2U`, `Lufft WS501-UMB`이다.

### Measurement dictionary

- Thermal: `P`, `W`, `Tvl`, `Trl`, `Tdiff`, `qv`, `V`.
- Electricity: `f`, `I1/I2/I3`, `U1/U2/U3`, `P1/P2/P3`, `W1/W2/W3`, `PF1/PF2/PF3`, `P`, `Q`, `PF`, `Win`, `Wout`, `WQin`, `WQout`, `W`, `WQ`.
- Weather: `AH`, `Dc`, `Dp`, `H`, `Igc`, `Igm`, `Pa`, `ρ`, `Sc`, `Ta`, `Ua`.

### Data records and cadence

- Full data file pattern: `URN_MEASUREMENT_raw.csv.gz`, `URN_MEASUREMENT_harmonized.csv.gz`, `URN_MEASUREMENT_corrected.csv.gz`, `URN_MEASUREMENT_corrected_resampled_{1min|15min|1h}.csv.gz`.
- Full data file columns: `datetime_utc`, `URN.measurement`.
- Janitza, Socomec, weather station은 change-of-value recording 성격이 있다.
- ABB-B24, SensorStar 계열은 periodic 1min sample resolution 성격이 있다.
- Tixi gateway는 timestamp jitter/drift evidence가 있어 cadence policy와 timestamp QA에 provenance로 연결해야 한다.

### Issue / QA semantics

- Manual issues: main cooling meter failure, powered-off design studio meters, wrong configuration, implausible PV readings, wrong conversion factor, interference near CHP wire 등.
- Automatic issues: zero measurement, leap/lasting leap, missing interval.
- Change-of-value collection 때문에 missing interval detection은 meter-specific maximum expected time threshold가 필요하다.
- Corrected/resampled products는 CMS에서 `reference.corrected_resampled_*`로만 취급하고 canonical observed truth로 승격하지 않는다.

## 6. Vector DB 제외 기준

| 제외 대상 | 이유 |
|---|---|
| benchmark/model experiment 결과 | CMS source truth가 아니라 실험 산출물이다. |
| 발표용 중간 산출물 | 규약 문서가 아니며 source provenance가 불안정하다. |
| 비기준 운영 산출물 | 프로젝트 규약이 아니다. |
| local scratch output | 재현 가능한 기준 문서가 아니다. |
| secret 또는 credential 관련 파일 | Vector DB 적재 금지 대상이다. |

## 7. Vector DB chunk 후보

| chunk_id | Source tier | 내용 |
|---|---|---|
| `paper.abstract_dataset_scope` | Tier 0 | title, DOI, facility, period, raw/processed/issues |
| `paper.facility_assets` | Tier 0 | PV, CHP, HVAC, central cooling, building function |
| `paper.data_acquisition` | Tier 0 | gateways, protocol, InfluxDB, change-of-value vs periodic sampling |
| `paper.tables_2_3_meter_inventory` | Tier 0 | meter URN, hardware, description, `(E)`, `ZE` notes |
| `paper.tables_4_6_measurement_dictionary` | Tier 0 | thermal/electric/weather measurement code dictionary |
| `paper.data_records_file_layout` | Tier 0 | raw/harmonized/corrected/corrected_resampled file naming |
| `paper.issue_detection` | Tier 0 | manual/automatic issues, missing/leap/zero semantics |
| `dryad.dataset_manifest` | Tier 0 | DOI, license, file list, checksum |
| `github.issue_template_and_meters_yaml` | Tier 1 보조 | issue schema와 meter grouping 보조 |
| `cms.data_platform_contract` | Tier 1 | source/candidate/QA/canonical/reference boundary |
| `cms.measurement_processing_policy` | Tier 1 | cadence, NULL/state-hold, coverage, canonical eligibility |
| `cms.meter_metadata` | Tier 1 | 81 meter, role, equipment group, redundancy |
| `cms.qa_contract` | Tier 1 | QA evidence, leakage guard, latency metric |

## 8. 검증 기준

- Tier 0 source는 DOI 또는 URL provenance를 가진다.
- Tier 1 source는 active repository path를 가진다.
- Excluded source는 Vector DB ingest 대상에서 빠진다.
- Chunk metadata에는 `source_tier`, `source_path`, `doc_type`, `cms_layer`, `content_sha256`이 포함된다.
