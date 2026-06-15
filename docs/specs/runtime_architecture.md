# Runtime Architecture

**갱신일:** 2026-06-15
**상태:** 통합 runtime/workflow 기준
**범위:** 이 문서는 FastAPI service plane, Airflow/scheduler workflow plane, optional LangGraph review workflow, live trigger/worker boundary, canonical branch, peak feature/model-serving branch, application runtime module의 책임 경계를 정의한다.

## 1. 확정 원칙

1. FastAPI `/chat`은 lightweight route다.
2. LangGraph는 synchronous chat path가 아니다.
3. LangGraph는 QA review, report review, replay planning, approval review를 지원하는 optional async workflow다.
4. Bulk ETL, DB write, promotion, deployment, email send는 FastAPI나 LangGraph가 직접 실행하지 않는다.
5. Scheduler/Airflow/report worker가 scheduled report artifacts와 batch/replay job을 소유한다.
6. 모든 side effect는 job registration, approval request, worker execution, audit evidence로 분리한다.

## 2. Plane 구조

| Plane | 포함 | 금지 |
|---|---|---|
| Data plane | source, Kafka streaming buffer, PostgreSQL live/staging/candidate/canonical, QA evidence, mart/ops model-serving artifacts | user chat 직접 promotion |
| Service plane | FastAPI, dashboard, Text-to-SQL, status, artifact download, manual job registration | bulk ETL, long-running batch 직접 실행 |
| Workflow plane | Airflow, scheduler, replay worker, report worker, model job, P-Max adapter/release loader, optional LangGraph review workflow | 일반 chat 응답 path 대체 |

## 3. FastAPI boundary

FastAPI route는 다음 작업만 수행한다.

```text
request validation
lightweight routing
read-only evidence query
job registration
approval request creation
artifact/status response
Kafka publish for POST /ingest/measurements after payload validation
```

`POST /ingest/measurements`는 ingestion gateway route다. 허용 범위는 payload validation, `measurement_raw_v1` envelope 생성, injected Kafka producer publish ack 확인, `202 Accepted` 스타일 응답까지다. 이 route는 PostgreSQL direct write, rollup, QA eligibility, promotion을 수행하지 않는다.

금지 작업은 다음과 같다.

```text
bulk ETL
PostgreSQL direct write from `/ingest/measurements`
rollup / QA / promotion from `/ingest/measurements`
canonical write
production DDL
deployment
email send
long-running report generation
LangGraph blocking execution as normal chat path
```

## 3.1 Current deployed service placement

2026-06-15 기준 운영 배치는 다음과 같다.

| Host | Active service roles | Notes |
|---|---|---|
| PC1 | `cms-ingestion-api`, `cms-backend-api`, `cms-frontend`, `cms-airflow-standalone`, Kafka broker, Kafka exporter, node exporter, 3x `cms-kafka-to-postgres-consumer`, `cms-live-bucket-queue-worker` | Injector host Python process는 중지했으며, 다음 적재는 FastAPI/containerized injector 전환 후 이어간다. |
| PC2 | Kafka broker, Prometheus, Grafana, Kafka exporter, node exporter | Local Kafka/KRaft cluster member and observability host. |
| PC3 | Kafka broker, `cms-canonical-promotion-worker`, `cms-anomaly-feature-worker`, `cms-hybrid-model-serving-scheduler`, exporters | `cms:model-serving` image must include torch; rebuild with `--env-file docker/model_serving.env` because Compose interpolation does not read service `env_file`. |
| AWS | `cms-postgres`, `cms-grafana`, postgres exporter, node exporter | PostgreSQL/Grafana DB plane; Compose files are mirrored as sanitized templates in `docker/compose.aws.db*.yml`. |

Kafka lag is a consumer backlog metric, not API latency. During historical 2023 replay, event timestamps are expected to be 2023; backlog/drain status is evaluated by Kafka offsets and DB read-back.

## 4. Workflow boundary

Workflow plane은 background execution과 review를 담당한다.

```text
ops.api_job / scheduler
-> batch or replay worker
-> QA evidence artifact
-> optional LangGraph review
-> approval recommendation
-> controlled promotion request
```

Model-serving branch도 workflow plane에서 실행한다.

```text
P-Max lane:
mart.peak_feature_15min
-> P-Max feature query / 288-window readiness
-> P-Max v29 adapter / release loader
-> mart.pmax_forecast_15min
   + ops.pmax_forecast_inference_log
   + qa.pmax_forecast_evaluation

Anomaly lane:
live.measurement_1h or approved observed 1h source
-> mart.anomaly_feature_1h
-> anomaly v84 adapter
-> mart.anomaly_warning_1h
   + ops.anomaly_warning_inference_log
   + qa.anomaly_warning_evaluation

Combined evidence:
P-Max packet + anomaly packet + cross-lane consistency
-> qa.model_serving_evidence_packet
```

P-Max/anomaly inference는 streaming smoke/test target이 아니다. Live streaming 검증은 FastAPI -> Kafka -> `live.measurement_event` -> observed bucket/QA/canonical readiness와 model input materialization까지를 우선 대상으로 삼고, model inference는 별도 Airflow model job dry-run 또는 artifact replay로 분리한다.

LangGraph는 recommendation과 review note를 만들 수 있지만, promotion이나 DB write를 직접 수행하지 않는다.

## 5. Live trigger and worker boundary

Live common ingestion path는 다음 순서를 따른다.

```text
sensor / FastAPI ingestion
-> Kafka measurement_raw_v1
-> kafka_to_postgres_consumer
-> live.measurement_event
-> common trigger
-> live.measurement_1min + live.bucket_queue
```

`live.measurement_1min`과 `live.bucket_queue` 이후에는 두 branch로 분리한다.

```text
Branch A: observed / canonical
live.measurement_1min + live.bucket_queue
-> mean_rollup_worker
-> live.measurement_15min / live.measurement_1h
-> QA / anomaly evidence
-> approval + controlled promotion
-> canonical.measurement_1min / canonical.measurement_15min / canonical.measurement_1h

Branch B: model-serving
live.measurement_1min + live.bucket_queue
-> peak_feature_worker / 1h input materializer
-> mart.peak_feature_15min / mart.anomaly_feature_1h
-> Airflow model-serving job
-> P-Max adapter -> mart.pmax_forecast_15min
   + ops.pmax_forecast_inference_log
   + qa.pmax_forecast_evaluation
-> anomaly adapter -> mart.anomaly_warning_1h
   + ops.anomaly_warning_inference_log
   + qa.anomaly_warning_evaluation
-> qa.model_serving_evidence_packet
```

### 5.1 Common trigger responsibility

Trigger는 가볍게 유지한다. 허용 작업은 다음뿐이다.

```text
1. inserted live.measurement_event row 수신
2. live.measurement_policy lookup
3. policy pass 시 live.measurement_1min upsert
4. live.bucket_queue에 job_kind별 dirty bucket 등록
5. policy miss / ambiguous / disabled 등은 qa.live_measurement_issue 기록
```

Trigger 금지 작업은 다음과 같다.

```text
15min mean 계산
1h mean 계산
peak_value / peak_ts 계산
mart feature write
model inference / P-Max adapter 실행
QA eligibility 전체 평가
canonical write
external API call
long transaction 또는 bulk scan
```

Trigger가 생성하는 queue job은 최소 다음 세 종류다.

```text
(job_kind='mean_rollup', resolution='15min')
(job_kind='mean_rollup', resolution='1h')
(job_kind='peak_feature', resolution='15min')
```

### 5.2 Worker responsibility and acceptance criteria

| Worker | 입력 | 출력 | Acceptance criteria |
|---|---|---|---|
| `kafka_to_postgres_consumer` | Kafka `measurement_raw_v1` envelope | `live.measurement_event` | business idempotency key로 idempotent insert. Kafka topic/partition/offset/key와 raw payload hash/source lineage 보존. DB transaction success 이후 offset commit. canonical/mart write 없음. |
| `mean_rollup_worker` | `live.bucket_queue`, `live.measurement_1min`, `live.measurement_policy` | `live.measurement_15min`, `live.measurement_1h` | `job_kind='mean_rollup'`만 처리. mean observed rollup과 coverage/missing/quality/provenance 보존. cumulative/unknown policy는 block 또는 candidate-only. peak value를 live 대표값으로 쓰지 않음. |
| `peak_feature_worker` | `live.bucket_queue`, `live.measurement_1min` | `mart.peak_feature_15min`, optional `mart.peak_feature_15min` | `job_kind='peak_feature'`만 처리. `peak_value`, `peak_ts`, rolling 1h projection을 mart에만 기록. canonical promotion 대상이 아님. P-Max inference를 직접 호출하지 않음. |
| `anomaly_input_materializer` | observed 1h source, policy/QA refs | `mart.anomaly_feature_1h` | anomaly v84용 343시간 1h input boundary를 구성한다. model-specific derived feature와 imputation/provenance는 mart input에만 기록하고 canonical을 수정하지 않음. |
| `qa_eligibility_worker` | live 1min/15min/1h candidates, policy, issues | `live.promotion_check`, QA/anomaly evidence packet | observed source, policy validity, coverage arithmetic, NULL/0 distinction, lineage, blocking issue, anomaly evidence를 평가. pass/warn/block을 재현 가능하게 기록. |
| `promotion_worker` | approved `promotion_run`, eligible `promotion_check` | `canonical.measurement_1min/15min/1h` | explicit approval과 `promotion_id` 없이는 실행하지 않음. canonical에는 approved observed mean rows만 upsert. peak feature는 제외. |
| `airflow_model_job` | model input tables, model/version config, release metadata | model-serving run request/artifact | P-Max/anomaly model-serving 전용 batch/scheduled job. Streaming smoke/test target이 아님. P-Max는 15min 기준 288개 window, anomaly는 1h 기준 343시간 input을 확보할 수 있을 때만 실행. |
| `model_serving_adapter_loader` | P-Max/anomaly inference artifact, release policy | `mart.pmax_forecast_15min`, `mart.anomaly_warning_1h`, ops logs, QA/evidence packets | model output을 release policy에 따라 forecast/warning mart로 적재하고 inference lineage/log/evaluation evidence를 분리 기록. canonical measurement table에 write하지 않음. |
| `report_worker` | QA evidence, promotion_check, job metadata | artifact/status | row counts, block reasons, evidence level, latency summary, artifact ref를 남김. |

### 5.3 Queue and retry rules

- Queue claim은 `(meter_urn, measurement, resolution, bucket_ts, job_kind, policy_version)` 단위로 idempotent해야 한다.
- Worker는 `pending -> running -> done/failed/blocked` 상태 전이를 남긴다.
- Late event가 들어오면 같은 idempotency key의 queue row를 다시 dirty 상태로 만들거나 policy에 맞는 재집계 version을 남긴다.
- Worker failure는 canonical/mart partial write를 성공으로 보고하지 않는다. partial write가 가능하면 retry-safe upsert 또는 transaction boundary가 필요하다.

### 5.4 Model-serving branch rules

- P-Max branch의 direct runtime source는 `mart.peak_feature_15min`이며, `mart.peak_feature_15min`은 optional projection/view다. Kafka event stream이나 PostgreSQL trigger에서 직접 inference를 호출하지 않는다.
- P-Max input 생성 조건은 15min 기준 288개 window 확보 후 96x22 input 구성 가능 여부다. window 부족은 inference skip/block evidence로 남긴다.
- Anomaly branch의 runtime source는 `mart.anomaly_feature_1h`이며, upstream observed 1h source와 model-specific derived/imputation feature를 분리한다.
- Anomaly input 생성 조건은 1h 기준 343시간 history와 63 meter coverage 검증이다. output은 warning/model-serving evidence이며 canonical observed value가 아니다.
- Model-serving loader는 P-Max forecast, anomaly warning, inference log, evaluation, combined evidence를 각각 `mart`, `ops`, `qa`에 기록한다.
- Model inference는 live streaming smoke/test target이 아니며, 별도 Airflow model job dry-run 또는 artifact replay로 검증한다.

## 6. LangGraph review workflow

LangGraph review workflow는 다음 경우에 사용한다.

| 용도 | 입력 | 출력 |
|---|---|---|
| QA review | QA evidence packet | pass/warn/block recommendation |
| Report review | report draft + evidence | review note |
| Replay planning | replay request + source manifest | execution plan |
| Approval review | candidate + promotion_check | approval recommendation |

Human-in-the-loop boundary는 다음과 같다.

```text
LangGraph recommendation
-> human approval
-> controlled worker/role execution
-> audit evidence
```

## 7. Application runtime modules

| Path | 역할 |
|---|---|
| `src/cms/contracts/` | import-safe dataclass, table/route constants |
| `src/cms/data/` | equalization, scratch guard, adapters, live/replay helpers |
| `src/cms/service/` | FastAPI/import-safe service factories and route contracts |
| `src/cms/workflow/` | optional Airflow/LangGraph workflow adapters and guards |
| `scripts/live/` | live/replay CLI dry-run and smoke runners |
| `scripts/migrations/` | offline migration draft generators |
| `scripts/scratch/` | scratch-only integration helpers |
| `scripts/verify/` | repeatable verification gates |

## 8. Job and artifact contract

모든 background job은 최소한 다음 정보를 남긴다.

```text
job_id
run_id
requester
job_type
source_refs
time_window
writes_allowed
target_objects
status
started_at
finished_at
artifact_refs
qa_summary
```

Artifact는 source evidence, row counts, QA status, cleanup command를 포함한다.

## 9. Side-effect safety gates

| Action | 요구 조건 |
|---|---|
| staging/scratch write | isolated target, run_id, cleanup command |
| production/canonical write | explicit approval, promotion id, rollback/reconcile plan |
| DDL/schema change | migration review, approval, backup/recovery plan |
| deployment | deployment approval, health check, rollback plan |
| email/report send | recipient and artifact confirmation |

## 10. Diagram reference

Pipeline diagram은 `docs/specs/diagrams/readme.md`에서 관리한다. 개별 Mermaid source와 render는 서로 다른 pipeline 관점을 표현하므로 유지한다.

## 11. Verification

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with pytest --no-project python -m pytest -q
```

검증 후 생성된 cache는 active tree에 남기지 않는다.
