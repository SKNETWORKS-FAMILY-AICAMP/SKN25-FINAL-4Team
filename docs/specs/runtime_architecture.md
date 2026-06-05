# Runtime Architecture

**갱신일:** 2026-06-04  
**상태:** 통합 runtime/workflow 기준  
**범위:** 이 문서는 FastAPI service plane, Airflow/scheduler workflow plane, optional LangGraph review workflow, live trigger/worker boundary, application skeleton의 책임 경계를 정의한다.

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
| Data plane | source, Kafka streaming buffer, PostgreSQL live/staging/candidate/canonical, QA evidence | user chat 직접 promotion |
| Service plane | FastAPI, dashboard, Text-to-SQL, status, artifact download, manual job registration | bulk ETL, long-running batch 직접 실행 |
| Workflow plane | Airflow, scheduler, replay worker, report worker, optional LangGraph review workflow | 일반 chat 응답 path 대체 |

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

LangGraph는 recommendation과 review note를 만들 수 있지만, promotion이나 DB write를 직접 수행하지 않는다.

## 5. Live trigger and worker boundary

Live path는 다음 순서를 따른다.

```text
sensor / FastAPI ingestion
-> Kafka measurement_raw_v1
-> kafka_to_postgres_consumer
-> live.measurement_event
-> common trigger
-> live.measurement_1min + live.bucket_queue
-> workers
-> QA eligibility
-> controlled promotion
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
| `peak_feature_worker` | `live.bucket_queue`, `live.measurement_1min` | `mart.peak_feature_15min`, `mart.peak_input_15min` | `job_kind='peak_feature'`만 처리. `peak_value`, `peak_ts`, rolling 1h features를 mart에만 기록. canonical promotion 대상이 아님. |
| `qa_eligibility_worker` | live 1min/15min/1h candidates, policy, issues | `live.promotion_check`, QA evidence packet | observed source, policy validity, coverage arithmetic, NULL/0 distinction, lineage, blocking issue를 평가. pass/warn/block을 재현 가능하게 기록. |
| `promotion_worker` | approved `promotion_run`, eligible `promotion_check` | `canonical.measurement_1min/15min/1h` | explicit approval과 `promotion_id` 없이는 실행하지 않음. canonical에는 approved observed mean rows만 upsert. peak feature는 제외. |
| `report_worker` | QA evidence, promotion_check, job metadata | artifact/status | row counts, block reasons, evidence level, latency summary, artifact ref를 남김. |

### 5.3 Queue and retry rules

- Queue claim은 `(meter_urn, measurement, resolution, bucket_ts, job_kind, policy_version)` 단위로 idempotent해야 한다.
- Worker는 `pending -> running -> done/failed/blocked` 상태 전이를 남긴다.
- Late event가 들어오면 같은 idempotency key의 queue row를 다시 dirty 상태로 만들거나 policy에 맞는 재집계 version을 남긴다.
- Worker failure는 canonical/mart partial write를 성공으로 보고하지 않는다. partial write가 가능하면 retry-safe upsert 또는 transaction boundary가 필요하다.

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

## 7. Application skeleton

| Path | 역할 |
|---|---|
| `src/cms/contracts/` | import-safe dataclass, table/route constants |
| `src/cms/data/` | equalization, scratch guard, adapters, live/replay helpers |
| `src/cms/service/` | FastAPI/import-safe service skeleton |
| `src/cms/workflow/` | optional Airflow/LangGraph workflow skeletons |
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

Pipeline diagram은 `docs/specs/diagrams/README.md`에서 관리한다. 개별 Mermaid source와 render는 서로 다른 pipeline 관점을 표현하므로 유지한다.

## 11. Verification

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with pytest --no-project python -m pytest -q
```

검증 후 생성된 cache는 active tree에 남기지 않는다.
