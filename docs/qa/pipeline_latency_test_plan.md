# Pipeline Latency and Scratch DB Test Plan

**갱신일:** 2026-06-15
**상태:** 실행 전 계획
**범위:** live pipeline의 scratch DB integration, branch별 correctness, stage별 latency 측정 기준을 정의한다. 이 문서는 DB write 실행 승인이 아니다.

## 1. 목적

나중에 수행할 주요 테스트는 pipeline별 latency와 correctness를 모두 측정하는 것이다. 목표는 단순 insert/read smoke가 아니라 실제 stage contract가 이어지는지 확인하는 데 있다.

기준 flow는 다음과 같다.

```text
source event
-> FastAPI POST /ingest/measurements
-> Kafka measurement_raw_v1
-> kafka_to_postgres_consumer
-> live.measurement_event
-> common trigger + live.measurement_policy
-> live.measurement_1min + live.bucket_queue
-> mean_rollup_worker -> live.measurement_15min / live.measurement_1h
-> peak_feature_worker -> mart.peak_feature_15min
-> optional helper projection -> mart.peak_input_15min
-> qa_eligibility_worker -> live.promotion_check
-> approval-gated promotion readiness
```

## 2. Evidence ladder

| Level | 의미 | 허용 동작 | 완료 기준 |
|---|---|---|---|
| `local_dry_run` | 파일 또는 in-memory 처리 | DB write 없음 | input/output count, policy label, latency marker |
| `mocked_adapter` | repository/adapter mock | DB client 없음 | target name validation, write payload shape |
| `scratch_db_integration` | isolated PostgreSQL scratch objects and Kafka envelope fixtures or approved local broker | 승인된 scratch write만 | PostgreSQL read-back, row count, offset/DLQ decision, latency |
| `aws_scratch_or_staging` | AWS isolated scratch/staging | 승인된 AWS scratch write만 | object names, counts, latency, cleanup plan |
| `production_promotion` | canonical controlled promotion | 별도 approval 필요 | approval_id, promotion_id, rollback/reconcile evidence |

현재 다음 단계는 `local_dry_run`과 `mocked_adapter`를 강화한 뒤, Viowlet 승인 후 `scratch_db_integration`으로 이동한다.

## 3. Scratch object naming

Scratch run id는 production처럼 보이면 안 된다.

허용 예시:

```text
scratch_YYYYMMDDTHHMMSSZ
latency_YYYYMMDDTHHMMSSZ
```

금지:

```text
live
live_*
prod
prod_*
production
production_*
canonical
canonical_*
```

Kafka Phase 1 local/import-safe test는 실제 broker 없이 `KafkaEnvelope` fixture를 우선 사용한다.

```text
topic = measurement_raw_v1
dlq_topic = measurement_dead_letter_v1
consumer_group = postgres-live-ingest
```

PostgreSQL scratch schema:

```text
scratch_<run_id>
```

PostgreSQL scratch tables:

```text
scratch_<run_id>.measurement_event
scratch_<run_id>.measurement_1min
scratch_<run_id>.bucket_queue
scratch_<run_id>.measurement_15min
scratch_<run_id>.measurement_1h
scratch_<run_id>.peak_feature_15min
scratch_<run_id>.peak_input_15min
scratch_<run_id>.promotion_check
scratch_<run_id>.latency_event
```

Scratch test는 `live.measurement_event`, `live.bucket_queue`, `canonical.measurement_*`, production `mart`에 직접 쓰지 않는다. 실제 table contract는 scratch schema에 복제하거나 temporary object로 검증한다.

## 4. Required latency metrics

모든 latency는 stage별 marker와 end-to-end marker를 함께 남긴다.

| Metric | 시작 | 종료 |
|---|---|---|
| `source_to_fastapi_sec` | source event 생성/수신 | FastAPI ingestion request 수신 |
| `fastapi_to_kafka_sec` | FastAPI validation 완료 | Kafka produce ack 확인 |
| `kafka_to_event_sec` | Kafka envelope consume | PostgreSQL scratch measurement_event write 확인 |
| `event_to_1min_sec` | measurement_event write | 1min bucket write 확인 |
| `event_to_queue_sec` | measurement_event write | bucket_queue rows 확인 |
| `one_min_to_15min_sec` | queue claim 또는 1min ready | 15min mean rollup write 확인 |
| `one_min_to_1h_sec` | queue claim 또는 1min ready | 1h mean rollup write 확인 |
| `one_min_to_peak_feature_sec` | queue claim 또는 1min ready | peak_feature_15min write 확인 |
| `peak_feature_to_optional_projection_sec` | peak_feature_15min ready | peak_input_15min write 확인 |
| `qa_eligibility_sec` | candidate row ready | promotion_check write 확인 |
| `promotion_ready_sec` | promotion_check pass | approval-required promotion-ready marker |
| `end_to_end_sec` | source event 생성/수신 | promotion-ready marker 또는 QA block evidence |

집계는 최소 다음을 포함한다.

```text
count
min
mean
p50
p95
max
failed_count
blocked_count
retry_count
```

## 5. Correctness checks by branch

### 5.1 Raw / event branch

- FastAPI accepted count equals expected valid source event count.
- Kafka raw envelope count equals accepted FastAPI publish count.
- PostgreSQL `measurement_event` count equals accepted raw event count.
- event idempotency key prevents duplicates.
- raw payload hash/source ref exists.
- no canonical write occurs.

### 5.2 1min branch

- `event_ts` floors to the expected 1min `bucket_ts`.
- `0` remains observed value.
- missing observation remains `NULL` with `observed_points = 0`.
- policy miss/ambiguous rows produce issue records and no queue jobs.

### 5.3 Mean rollup branch

- `mean_rollup / 15min` writes only mean observed rollup.
- `mean_rollup / 1h` writes only mean observed rollup.
- `expected_points`, `observed_points`, `gap_points`, `coverage_ratio` follow native cadence policy.
- `live.measurement_15min/1h.value` is not peak/max.

### 5.4 Peak branch

- `peak_feature / 15min` writes only peak feature branch.
- `peak_value`, `peak_ts`, `max`, `min`, `mean`, `std` are mart feature values.
- rolling 1h peak fields are in `peak_input_15min`.
- peak feature rows are never canonical candidates.

### 5.5 QA / eligibility branch

- blocking issue rows block promotion readiness.
- coverage below threshold blocks or warns according to policy.
- lineage missing blocks.
- cumulative/unknown/circular/COV-without-evidence are blocked.
- corrected/reference leakage is blocked.
- `QA-PEAK-001` blocks peak-to-canonical leakage.

### 5.6 Promotion readiness branch

- approval_id is required.
- promotion_id is required.
- scratch test does not write `canonical.measurement_*`.
- production promotion is a separate approval step.

## 6. Test shape

Minimum scratch DB test shape:

```text
1. Create run_id and validate it is scratch-safe.
2. Create isolated PostgreSQL scratch schema and Kafka envelope fixtures or approved local broker topic.
3. Send synthetic source events through FastAPI ingest contract and Kafka raw envelope contract.
4. Consume raw Kafka envelopes into PostgreSQL scratch measurement_event.
5. Apply the same trigger contract in scratch form or worker-simulated contract.
6. Verify 1min bucket rows and queue rows.
7. Run mean rollup worker for 15min and 1h.
8. Run peak feature worker for 15min and rolling 1h peak input.
9. Run QA eligibility worker.
10. Record latency_event rows for every stage.
11. Read back all counts and latency summaries.
12. Report cleanup commands.
```

## 7. Acceptance criteria

Scratch DB integration can be called complete only if all conditions hold.

- Kafka fixture/topic metadata and PostgreSQL scratch object names are reported.
- Row counts are reported for raw, event, 1min, queue, 15min, 1h, peak_feature, peak_input, promotion_check.
- Latency metrics are reported for every stage in section 4.
- `NULL` and observed `0` are proven distinct.
- mean branch and peak branch are proven separate.
- queue idempotency key is proven with repeated event or repeated dirty bucket input.
- policy miss/ambiguous produces issue records and no rollup queue.
- no canonical writes occur.
- cleanup commands are documented.

## 8. Blockers before scratch execution

- Scratch DB target names must be approved.
- DB credentials and tunnels must already be available outside the chat; secrets are not requested here.
- DDL draft must be reviewed before being applied to scratch.
- COV/cumulative policies must remain blocked until their evidence rules are implemented.
- Production/canonical write remains gated by Viowlet approval.

## 9. Report format

Final latency/correctness report should start with direct status.

```text
AWS/live: tested / not tested / partially tested
scratch DB integration: tested / not tested / partially tested
local dry-run: tested / not tested / partially tested
```

Then include:

```text
run_id
object names
time window
source count
event count
row counts by branch
latency summary by metric
QA block reasons
promotion readiness result
cleanup commands
remaining blockers
```
