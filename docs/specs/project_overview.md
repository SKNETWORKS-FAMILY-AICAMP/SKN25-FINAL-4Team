# CMS Spec Overview

**갱신일:** 2026-06-15
**상태:** 팀 공유용 spec overview / live stream readiness 반영
**범위:** CMS project architecture, live pipeline branch, final P-Max model-serving boundary, repository map, naming, verification baseline을 요약합니다. 세부 data/runtime/measurement contract는 기존 spec 문서를 기준으로 하며, 이 문서는 중복 정의가 아니라 공유용 navigation layer입니다.

## 1. 목적과 범위

CMS 프로젝트는 건물·설비 계량 시계열을 수집하고, 품질 검증과 보정 경계를 통과한 데이터만 분석·서빙·보고에 사용하는 system입니다. Architecture는 Data plane, Service plane, Workflow plane을 분리한 contract-first 구조를 기준으로 합니다.

Active runtime package는 `src/cms`이며, 문서와 schema namespace는 `CMS` / `cms`를 기준으로 합니다. Live ingestion은 `FastAPI -> Kafka -> PostgreSQL live.measurement_event`를 공통 진입점으로 사용하고, 이후 downstream은 observed canonical/QA lane과 P-Max model-serving lane으로 분리합니다.

2026-06-15 runtime 기준으로 PC1은 `CMS Ingestion API`와 `CMS Backend API`를 분리 실행하고, PC1 Kafka-to-PostgreSQL consumer는 3개 container로 partition별 drain을 수행합니다. PC3는 rebuilt `cms:model-serving` image `sha256:eec4e1804b2c03133776464a77296740338fc4b6903510256ae311a109bb02ab`와 scheduler/worker lane을 사용하며, all worker containers import torch `2.12.0+cpu`; hourly full hybrid model-serving run은 P-Max `clip_zero` policy 기준으로 통과했습니다. 현재 injector는 host Python process를 중지한 상태이며, 다음 적재는 FastAPI/containerized injector 전환 후 이어가는 것을 기준으로 합니다. Kafka backlog는 latest checked total lag `90756`에서 drain 중이며, lag=0 또는 bounded residual은 별도 운영 지표로 추적합니다.

## 2. 핵심 원칙

1. `POST /ingest/measurements`는 payload validation과 Kafka publish만 수행합니다. PostgreSQL direct write, rollup, QA, promotion, model inference는 수행하지 않습니다.
2. Kafka topic `measurement_raw_v1`는 observed raw measurement event stream입니다. Consumer는 idempotent insert로 `live.measurement_event`에 event ledger를 남깁니다.
3. `live.measurement_event` 이후 common trigger는 policy lookup, `live.measurement_1min` upsert, `live.bucket_queue` dirty bucket 등록까지만 수행합니다.
4. Canonical promotion은 approval-gated worker boundary를 통과해야 합니다. `canonical.measurement_*`에는 approved observed fact만 들어갑니다.
5. Peak/P-Max branch의 feature와 forecast는 model-serving output이며 canonical observed fact가 아닙니다.
6. Airflow는 scheduled/model/report orchestration을 담당합니다. FastAPI request path의 blocking inference executor가 아닙니다.
7. Streaming tests는 live measurement ingestion/processing boundary만 검증합니다. P-Max scheduled inference, report, email, production promotion은 별도 승인된 workflow evidence로 검증합니다.
8. Live stream 준비 전 Grafana active dashboard는 AWS에 실제 존재하는 table(`live.measurement_event`, `ops.pipeline_metric`, `qa.meter_tag`, `qa.bad_row`, P-Max mart/ops/qa read table)과 Prometheus exporter metric만 직접 조회합니다. `live.bucket_queue`, `live.measurement_policy`, `qa.live_measurement_issue`, `ops.worker_heartbeat`, `ops.kafka_consumer_lag`, `ops.fastapi_ingest_metric`, `live.promotion_check`는 target/future contract로 남기며 DDL approval 전 active dashboard SQL에서 제외합니다.

## 3. Plane 구조

| Plane | 책임 | 주요 대상 | 금지/주의 |
|---|---|---|---|
| Data plane | source, live/replay input, PostgreSQL processing, QA evidence, canonical/mart output | Kafka, `live`, `canonical`, `qa`, `mart`, `ops` | 승인 없는 canonical write, peak feature의 canonical promotion |
| Service plane | lightweight API, read-only query, status/artifact response, manual job registration | FastAPI, dashboard, Text-to-SQL | bulk ETL, long-running batch, direct model inference execution |
| Workflow plane | scheduled/background execution과 review | Airflow, scheduler, workers, optional LangGraph review | 일반 chat/API request path 대체, approval 없는 side effect |

## 4. Live pipeline branch overview

공통 ingestion 경로는 다음과 같습니다.

```text
sensor / client
-> FastAPI POST /ingest/measurements
-> Kafka measurement_raw_v1
-> kafka_to_postgres_consumer
-> PostgreSQL live.measurement_event
-> common trigger
-> live.measurement_1min + live.bucket_queue
```

`live.bucket_queue` 이후에는 두 개의 downstream branch로 나뉩니다. 단, 2026-06-10 active AWS/Grafana readiness 기준에서는 `live.measurement_event`와 `ops.pipeline_metric` 중심으로 관측하고, `live.measurement_1min`, `live.bucket_queue`, `qa.live_measurement_issue` 등 downstream target table은 migration/DDL approval 이후 active query 대상에 포함합니다.

### 4.1 Branch A: canonical observed / QA / anomaly / promotion lane

Branch A는 observed measurement를 canonical 후보와 QA evidence로 만드는 lane입니다.

```text
live.measurement_1min + live.bucket_queue
-> mean_rollup_worker
-> live.measurement_15min / live.measurement_1h
-> qa_eligibility_worker
-> live.promotion_check + QA evidence packet
-> approval-gated promotion_worker
-> canonical.measurement_1min / canonical.measurement_15min / canonical.measurement_1h
-> anomaly / dashboard / report consumers
```

이 lane의 대표값은 mean observed rollup과 policy 기반 coverage/missing/quality/provenance입니다. `peak_value`, `peak_ts`, model forecast는 canonical promotion 대상이 아닙니다. Anomaly와 dashboard가 clean observed fact를 요구할 경우 `canonical.measurement_*` 또는 approval policy가 명시된 candidate/evidence만 사용합니다.

### 4.2 Branch B: peak_feature / P-Max model-serving lane

Branch B는 P-Max 예측을 위한 feature 및 serving output lane입니다.

```text
live.measurement_1min + live.bucket_queue
-> peak_feature_worker / 1h input materializer
-> mart.peak_feature_15min / mart.anomaly_feature_1h
-> scheduled model-serving workflow
-> P-Max lane: mart.pmax_forecast_15min + ops.pmax_forecast_inference_log + qa.pmax_forecast_evaluation
-> Anomaly lane: mart.anomaly_warning_1h + ops.anomaly_warning_inference_log + qa.anomaly_warning_evaluation
-> qa.model_serving_evidence_packet
-> API/dashboard read-only serving
```

`mart.peak_feature_15min`은 P-Max adapter의 직접 input boundary입니다. `mart.peak_feature_15min`은 legacy/helper projection view/table이며 direct runtime input으로 설명하지 않습니다. `mart.anomaly_feature_1h`은 anomaly v84 adapter의 1h model input boundary입니다. P-Max/anomaly inference 결과는 forecast/warning serving output이며 observed canonical table에 write하지 않습니다.

## 5. Final P-Max model-serving boundary

Final P-Max model release는 `v29`를 기준으로 공유합니다.

| 항목 | 기준 |
|---|---|
| Release | `P-Max v29` |
| Ensemble | per-meter ensemble of `v20`, `v23`, `v25`, `v27` |
| Logical meters | `V.Z81`, `V.Z82`, `H2.Z35x`, `H2.Z36x` |
| Input shape | `96x22` flattened input |
| History requirement | 288 history windows 필요 |
| Forecast horizons | 15 / 30 / 45 / 60 minutes |
| Direct model input boundary | `mart.peak_feature_15min` |
| Optional projection boundary | `mart.peak_feature_15min` |
| Forecast result table | `mart.pmax_forecast_15min` |
| Inference audit log | `ops.pmax_forecast_inference_log` |
| Evaluation table | `qa.pmax_forecast_evaluation` |

Model-serving boundary는 다음과 같습니다.

- Airflow 또는 scheduler가 inference job을 기동하고, job metadata와 lineage를 `ops.pmax_forecast_inference_log`에 남깁니다.
- Inference worker는 `mart.peak_feature_15min`에서 288개 history window를 읽고, `P-Max v29` output을 `mart.pmax_forecast_15min`에 기록합니다.
- Anomaly worker는 `mart.anomaly_feature_1h`에서 343시간 history input을 읽고, `anomaly v84` warning output을 `mart.anomaly_warning_1h`에 기록합니다.
- Evaluation worker는 horizon별 예측/관측 비교와 metric을 `qa.pmax_forecast_evaluation`에 기록합니다.
- FastAPI는 최신 forecast, inference status, evaluation summary를 read-only로 제공합니다. FastAPI request가 직접 model inference, retraining, DB promotion을 실행하지 않습니다.
- `P-Max v29` output은 service forecast입니다. Canonical observed measurement의 대체값이나 QA 없이 promotion 가능한 fact가 아닙니다.

## 6. 기술스택 구조도

팀 공유용 stack architecture SVG는 다음 위치에서 관리합니다.

```text
docs/specs/diagrams/stack_architecture_overview.svg
```

이 구조도는 `docker/`, `src/cms/`, `docs/specs` source evidence를 기준으로 `FastAPI`, `Apache Kafka`, `PostgreSQL`, `Apache Airflow`, `Python consumer/adapter`, `P-Max v29`, `Grafana`, `Prometheus`, `Vector DB / LLM Wiki`, optional `LangGraph`를 icon-based view로 배치합니다. Diagram의 핵심 메시지는 다음과 같습니다.

1. `FastAPI -> Kafka -> PostgreSQL live.measurement_event`는 공통 live ingestion path입니다.
2. `live.measurement_event` 이후 Branch A는 observed canonical / QA / anomaly / promotion lane입니다.
3. Branch B는 peak_feature / P-Max model-serving lane이며, forecast는 `mart/ops/qa`에 남깁니다.
4. Airflow는 model/report workflow를 실행하고, FastAPI request path에서 blocking inference를 실행하지 않습니다.

## 7. Repository map

```text
SKN25-FINAL-4Team/
├── docker/                         # local PostgreSQL/TimescaleDB development stack
├── docs/
│   ├── ontology/                   # RDF/OWL/SHACL ontology artifacts
│   ├── qa/                         # QA, latency, Grafana query contracts
│   ├── reference/                  # source inventory and measurement glossary
│   └── specs/                      # overview, data platform, runtime, measurement, model boundary specs
├── scripts/                        # dry-run, smoke, scratch guard, ontology, contract verification scripts
├── src/cms/                        # CMS Python package
└── tests/                          # unit/integration tests
```

## 8. Naming 및 주요 table

| 대상 | 기준 |
|---|---|
| Project-facing name | `CMS` |
| Python package | `src/cms` |
| PostgreSQL database/user | `cms` |
| Common live ingress ledger | `live.measurement_event` |
| Live candidate tables | `live.measurement_1min`, `live.measurement_15min`, `live.measurement_1h` |
| Canonical observed tables | `canonical.measurement_1min`, `canonical.measurement_15min`, `canonical.measurement_1h` |
| QA/promotion evidence | `live.promotion_check`, `qa.live_measurement_issue` |
| Current active observability | `live.measurement_event`, `ops.pipeline_metric`, `qa.meter_tag`, `qa.bad_row`, `mart.peak_feature_15min`, `mart.pmax_forecast_15min`, `ops.pmax_forecast_inference_log`, `qa.pmax_forecast_evaluation`, `mart.anomaly_warning_1h`, `ops.anomaly_warning_inference_log`, `qa.model_serving_evidence_packet` when the corresponding runtime lane has written evidence |
| Model-serving input/output | `mart.peak_feature_15min`, `mart.anomaly_feature_1h`, `mart.pmax_forecast_15min`, `mart.anomaly_warning_1h`, `ops.*_inference_log`, `qa.model_serving_evidence_packet` |
| Target-only observability before DDL approval | `live.measurement_policy`, `qa.live_measurement_issue`, `ops.worker_heartbeat`, `ops.kafka_consumer_lag`, `ops.fastapi_ingest_metric`, `live.bucket_queue`, `live.promotion_check` |
| Peak/P-Max input | direct runtime input is `mart.peak_feature_15min`; `mart.peak_feature_15min` is legacy/helper projection only |
| P-Max serving output | `mart.pmax_forecast_15min` |
| P-Max ops/QA | `ops.pmax_forecast_inference_log`, `qa.pmax_forecast_evaluation` |
| Anomaly input/output | `mart.anomaly_feature_1h`, `mart.anomaly_warning_1h` |
| Anomaly ops/QA | `ops.anomaly_warning_inference_log`, `qa.anomaly_warning_evaluation` |
| Combined model-serving evidence | `qa.model_serving_evidence_packet` |
| Reference corrected/resampled table | `reference.corrected_resampled_15min`, `reference.corrected_resampled_1h` |
| Missing observation 표현 | `NULL`, `missing observation`, `missing_points` |

## 9. 상세 문서 연결

| 문서 | 역할 |
|---|---|
| `docs/specs/project_overview.md` | 팀 공유용 architecture/spec overview |
| `docs/specs/runtime_architecture.md` | FastAPI, Kafka consumer, worker, Airflow/LangGraph runtime boundary |
| `docs/specs/data_platform_contract.md` | source/Kafka/PostgreSQL schema와 canonical/mart data boundary |
| `docs/specs/measurement_processing_policy.md` | observed measurement 처리, NULL/0, cadence, canonical eligibility 정책 |
| `docs/specs/meter_metadata.md` | meter metadata와 logical meter grouping reference |
| `docs/specs/live_schema_migration_plan.md` | live schema migration draft와 rollback boundary |
| `docs/specs/kafka_ingestion_implementation_plan.md` | Kafka ingestion implementation plan |
| `docs/qa/qa_contract.md` | QA/evidence 기준 |
| `docs/qa/pipeline_latency_test_plan.md` | streaming/live measurement latency test plan |
| `docs/qa/grafana_observability_plan.md` | live stream 전 Grafana operator dashboard readiness plan |
| `docs/qa/grafana_ops_query_contract.md` | active AWS table vs target observability table split과 Grafana query contract |
| `docs/reference/source_inventory.md` | source tier 기준 |
| `docs/reference/measurement_glossary.md` | measurement 용어집 |

## 10. Verification baseline

Markdown overview 변경 자체는 runtime 실행을 요구하지 않습니다. 코드/contract 변경을 동반하는 경우 권장 local check는 다음과 같습니다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with pytest --no-project python -m pytest -q
```

검증 후 생성된 cache는 active tree에 남기지 않습니다.
