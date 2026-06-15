# Gate 29 Operational Report Serving and Canonical Promotion Plan

- 갱신일: 2026-06-15 00:10 KST
- 상태: 실행 계획 / 구현 전 승인·작업 분해 문서
- 범위: 09:00 보고서에 들어갈 P-Max/Anomaly 결과 생성, 결과 저장·인식, continuous canonical promotion, 최종 E2E 검증
- 문서 성격: 계획과 acceptance gate. 이 문서는 DB write, DDL, canonical promotion, production model-serving write를 승인하지 않는다.
- 작성 기준: 2026-06-15 00시 전후 repo/DB/Airflow/PC3 runtime read-only 확인 결과

## 1. 현재 판정

운영 기준 판정은 `BLOCK`이다.

현재 시스템은 다음을 증명했다.

- `daily_report` Airflow DAG는 09:00 KST 스케줄로 등록되어 있다.
- `daily_report`는 read-only readiness report이며 모델을 실행하지 않는다.
- P-Max/anomaly one-shot 또는 hybrid/reference run 결과가 일부 mart/ops/qa에 저장된 적은 있다.
- `live.bucket_queue` worker는 live rollup, peak feature, promotion check를 계속 만들 수 있다.

현재 시스템은 다음을 증명하지 못했다.

- 09:00 보고서 전에 자동으로 P-Max/anomaly를 실행하는 standing scheduler 또는 daemon.
- 2026-06-15 09:00 KST 기준 P-Max/anomaly 결과 row.
- strict live anomaly 입력인 `mart.anomaly_feature_1h` materialization.
- `live.promotion_check` pass row를 `canonical.measurement_*`로 지속 승격하는 continuous promotion writer.
- report가 stale model rows를 정상 결과로 오인하지 않게 막는 freshness gate.

## 2. 확인된 런타임 증거

### 2.1 Airflow

확인된 DAG 상태:

```text
daily_report                   is_paused=False
weekly_report                  is_paused=False
monthly_report                 is_paused=False
model_serving_pipeline         is_paused=True
```

`daily_report` schedule:

```text
0 9 * * *
timezone=Asia/Seoul
```

`model_serving_pipeline`은 `schedule=None`, `is_paused_upon_creation=True`인 manual paused DAG이다. 현재 운영 자동 실행 경로가 아니다.

### 2.2 Model-serving output freshness

2026-06-15 09:00 KST 기준 output row:

```text
mart.pmax_forecast_15min where base_ts='2026-06-15 09:00:00+09' = 0
mart.anomaly_warning_1h where forecast_origin_ts='2026-06-15 09:00:00+09' = 0
```

최신 저장 결과:

```text
P-Max latest base_ts      = 2024-01-01 08:00:00+09
P-Max latest created_at   = 2026-06-14 23:18:30+09
Anomaly latest origin_ts  = 2024-01-01 08:00:00+09
```

현재 `mart.anomaly_feature_1h`:

```text
rows = 0
```

### 2.3 PC3 runtime

확인 결과:

```text
standing model-serving container = 없음
run_model_serving process        = 없음
systemd/cron timer               = 없음
```

PC3에는 model-serving script/artifact 관련 파일이 있으나, 자동 실행 daemon은 확인되지 않았다.

### 2.4 Canonical promotion

확인된 promotion evidence:

```text
live.promotion_check pass = 13,686
```

source별 pass:

```text
live.measurement_15min   pass = 5,935
live.measurement_1h      pass = 1,959
mart.peak_feature_15min  pass = 5,792
```

현재 canonical/live row 차이:

```text
canonical.measurement_15min rows = 113
canonical.measurement_1h    rows = 14
live.measurement_15min      rows = 21,918
live.measurement_1h         rows = 5,507
```

DB trigger/function inventory:

```text
canonical promotion trigger/function = 0
```

즉 `promotion_check`는 생성되지만 canonical writer가 지속 동작하지 않는다.

### 2.5 Kafka drain

마지막 확인 상태:

```text
measurement_raw_v1 partition 0 lag = 528,512
measurement_raw_v1 partition 1 lag = 501,630
measurement_raw_v1 partition 2 lag = 0
measurement_dead_letter_v1 offsets = 0, 0, 0
consumer = Up
bucket worker = Up
```

Kafka drain 자체도 아직 완료가 아니다.

## 3. 목표 상태

목표 운영 흐름은 아래와 같다.

```text
FastAPI /ingest/measurements
-> Kafka measurement_raw_v1
-> postgres-live-ingest consumer
-> live.measurement_event
-> live trigger/policy lookup
-> live.measurement_1min
-> live.bucket_queue
-> live.measurement_15min / live.measurement_1h / mart.peak_feature_15min
-> live.promotion_check
-> controlled continuous canonical promotion worker
-> canonical.measurement_15min / canonical.measurement_1h
-> anomaly feature materializer
-> mart.anomaly_feature_1h
-> scheduled model-serving worker
-> mart.pmax_forecast_15min / mart.anomaly_warning_1h
-> ops.*_inference_log / qa.model_serving_evidence_packet
-> daily_report freshness gate
-> 09:00 report
```

보고서 기준 목표:

- 09:00 KST `daily_report`는 모델을 직접 실행하지 않는다.
- 08:55 KST 또는 그 이전 scheduler가 `base_ts=09:00 KST` model-serving을 완료한다.
- report는 `base_ts=09:00 KST` / `forecast_origin_ts=09:00 KST` rows만 정상 최신 결과로 인식한다.
- rows가 없거나 stale이면 report status는 `blocked` 또는 `degraded`가 된다.

## 4. 구현 원칙

1. `canonical.*` write는 별도 worker와 approval boundary를 둔다.
2. `live.promotion_check pass`는 canonical write와 같지 않다. pass evidence를 읽어 canonical writer가 별도 idempotent upsert를 수행해야 한다.
3. `mart.peak_feature_15min`은 P-Max direct input이다. `active_peak_feature_15min`은 holdout/exclusion helper view로만 사용한다.
4. strict anomaly serving input은 `mart.anomaly_feature_1h`이다. 이 테이블은 canonical/live observed 1h facts에서 materialize해야 하며 reference/backfill은 nonprod 또는 warm-start로만 라벨링한다.
5. model-serving output은 observed fact가 아니다. `mart/ops/qa` serving output으로 저장한다.
6. report는 model-serving output freshness를 검증해야 하며, stale output을 최신 결과로 표시하면 안 된다.
7. 모든 long-running run은 run_id, job_id, promotion_id, source scope, cleanup/reconcile key를 남긴다.
8. broad/unbounded canonical mutation, DELETE, TRUNCATE, privilege broadening은 별도 승인 없이는 금지한다.

## 5. 작업 분해

### Gate 29.0: 기준선 freeze와 read-only inventory

**목표:** 구현 전에 현재 DB/runtime 상태를 기준선으로 저장한다.

**담당:** Orchestrator 검증, frieren 데이터 QA 검토

**Files:**

- Create: `reports/evidence/gate29_runtime_baseline_<timestamp>.md` 또는 JSON
- No production DB writes

**절차:**

1. Kafka lag, DLQ, consumer status를 기록한다.
2. `live.measurement_event` run scope row count와 `policy_lookup_status`를 기록한다.
3. `live.measurement_policy` count를 기록한다.
4. `live.bucket_queue` status count를 기록한다.
5. `live.promotion_check` source/status count를 기록한다.
6. `canonical.measurement_15min/1h` row count/max bucket을 기록한다.
7. `mart.peak_feature_15min`, `mart.anomaly_feature_1h`, `mart.pmax_forecast_15min`, `mart.anomaly_warning_1h`, `ops.*_inference_log`, `qa.model_serving_evidence_packet` freshness를 기록한다.
8. Airflow DAG list/runs/import errors를 기록한다.
9. PC3 model-serving process/container/timer absence or presence를 기록한다.

**Acceptance:**

- Evidence artifact가 생성된다.
- 모든 secret은 redacted 처리된다.
- DB query는 `BEGIN READ ONLY` 또는 equivalent read-only mode로 수행된다.

### Gate 29.1: Kafka drain과 live rollup catch-up

**목표:** `measurement_raw_v1` backlog를 새 생산 없이 drain하고 live rollup/mart queue가 안정적으로 처리되는지 확인한다.

**담당:** himmel infra/runtime, frieren QA count 검토

**절차:**

1. full source producer completion 여부를 log/pid로 확인한다.
2. 새 producer를 시작하지 않는다.
3. Kafka lag가 0이 될 때까지 consumer/worker health를 관찰한다.
4. DLQ가 0인지 확인한다.
5. `live.bucket_queue` pending/error count를 확인한다.
6. `policy_miss`가 새로 증가하지 않는지 확인한다.

**Acceptance:**

```text
Kafka lag total = 0
DLQ = 0
consumer Up
bucket worker Up
policy_miss = 0 for live_20230101_from_start scope
live.bucket_queue error = 0
pending은 worker가 처리 중인 bounded tail만 허용
```

**Block 조건:**

- DLQ 증가.
- consumer 재시작 루프.
- `policy_miss` 재발.
- `live.bucket_queue` error 증가.

### Gate 29.2: continuous canonical promotion worker

**목표:** `live.promotion_check pass`를 `canonical.measurement_15min/1h`로 지속 승격하는 idempotent worker를 구현한다.

**담당:** himmel DB contract, stark implementation, frieren QA policy, fern independent review

**Files:**

- Create: `src/cms/data/canonical_promotion_runner.py`
- Create: `scripts/stream/canonical_promotion_worker.py`
- Create: `tests/data/test_canonical_promotion_runner.py`
- Create: `tests/stream/test_canonical_promotion_worker.py`
- Optional gated SQL: `scripts/database/migrations/canonical_promotion_runtime_access.sql`
- Optional verify SQL: `scripts/database/verify/canonical_promotion_boundary_check.sql`

**Input:**

- `live.promotion_check`
- `live.measurement_policy`
- `live.measurement_15min`
- `live.measurement_1h`

**Output:**

- `canonical.measurement_15min`
- `canonical.measurement_1h`
- promotion audit/evidence table if an approved table already exists; otherwise append audit metadata in existing canonical provenance columns only if schema supports it.

**Explicit exclusions:**

- `mart.peak_feature_15min` is never promoted to canonical.
- `reference.corrected_resampled_*` is not promoted as observed canonical truth.
- No DELETE/TRUNCATE.
- No unbounded backfill by default.

**Selection rule:**

A row is promotable only when all conditions hold:

```text
promotion_check.eligibility_status = 'pass'
source_table in ('live.measurement_15min', 'live.measurement_1h')
live.measurement_policy.enabled = true
live.measurement_policy.canonical_eligible = true
policy_version matches the source rollup row
source rollup has non-null provenance/source_event_ids/source_bucket_refs where schema supports them
source row is not from reference/corrected/backfill lineage
```

**Idempotency:**

- Worker claims bounded candidate rows using `FOR UPDATE SKIP LOCKED` or equivalent stable selection.
- Upsert key must match canonical table's actual unique key. Do not guess; introspect catalog first.
- Re-running the same `promotion_id` must not duplicate canonical rows.
- Existing canonical rows may be updated only when the source row is the same business key and newer/approved by deterministic rule.

**Runtime gates:**

```text
--dry-run default
--runtime required for DB writes
CMS_ENABLE_CANONICAL_PROMOTION=1 required for DB writes
promotion_id required
batch_size required
source_table allowlist required
```

**Tests:**

1. Builds SQL without canonical writes when dry-run.
2. Rejects `mart.peak_feature_15min` as source.
3. Rejects `reference.*` and `corrected` lineage.
4. Requires `canonical_eligible=true`.
5. Requires `eligibility_status='pass'`.
6. Uses bounded batch and promotion_id.
7. Upsert command targets only `canonical.measurement_15min` and `canonical.measurement_1h`.
8. Rejects DELETE/TRUNCATE/broad schema operations in generated SQL.

**Verification:**

- Local targeted tests pass.
- SQL command is reviewed by fern before runtime enablement.
- Runtime smoke promotes a small bounded scope with promotion_id and read-back count.
- After smoke, canonical row delta equals promoted source row count, allowing idempotent no-op reruns.

### Gate 29.3: anomaly feature materialization

**목표:** strict anomaly serving input인 `mart.anomaly_feature_1h`를 observed 1h facts에서 생성한다.

**담당:** frieren data policy, stark implementation, himmel DB contract, fern review

**Files:**

- Create: `src/cms/data/anomaly_feature_materializer.py`
- Create: `scripts/serving/run_anomaly_feature_materializer.py`
- Create: `tests/data/test_anomaly_feature_materializer.py`
- Modify only if required: `src/cms/data/model_serving_queries.py`
- Modify only if required: `src/cms/contracts/anomaly_detection_1h.py`

**Input policy:**

Production strict path:

```text
canonical.measurement_1h
-> mart.anomaly_feature_1h
```

Warm-start path, if explicitly approved and labelled:

```text
live.measurement_1h or reference.corrected_resampled_1h
-> mart.anomaly_feature_1h or no-write evidence
source_mode must be hybrid_warm_start or reference_backfill
```

**Output schema target:**

`mart.anomaly_feature_1h` current columns:

```text
bucket_ts
meter_urn
feature_set
p_value
u1_value
pf_value
qv_value
tdiff_value
derived_features
input_quality
source_refs
created_at
```

**Feature contract:**

- History requirement: 343h.
- Electric feature families: `P`, `U1`, optional `PF` depending meter availability.
- Heat feature family: `P`, `qv`, `Tdiff` where defined.
- Derived features follow `ANOMALY_DETECTION_DERIVED_FEATURE_COLUMNS`.
- Missing values produce `input_quality` degradation, not silent success.
- `source_refs` must name the source table and bucket range.

**Tests:**

1. Builds rows for an electric meter with `P/U1/PF`.
2. Builds rows for an electric no-PF meter with explicit feature_set.
3. Builds rows for heat meters when `qv/Tdiff` are available.
4. Distinguishes missing observation from observed zero.
5. Blocks reference/corrected source in production strict mode.
6. Emits degraded input_quality when history is incomplete.
7. Upsert is idempotent on `(bucket_ts, meter_urn)` or the actual DB primary key.

**Acceptance:**

```text
mart.anomaly_feature_1h row count > 0
required model meters have 343h materialization coverage where source data exists
no reference/corrected source_refs in strict production rows
model-serving anomaly DB read branch can read feature rows without using reference_backfill
```

### Gate 29.4: scheduled model-serving before report

**목표:** 09:00 report 전에 P-Max와 anomaly output을 생성한다.

**담당:** himmel Airflow/runtime, stark runner integration, frieren output QA, fern review

**Files:**

- Modify: `src/cms/workflow/model_serving_airflow_skeleton.py` or create a production-safe scheduled module with a new DAG id.
- Create or modify: `dags/model_serving_pipeline.py`
- Modify: `scripts/serving/run_model_serving.py` only if runtime flags/source modes are insufficient.
- Add tests: `tests/workflow/test_model_serving_schedule_contract.py`

**Schedule decision:**

For 09:00 report freshness, run model-serving at `55 * * * *` KST with the next hour as `base_ts`.

Example:

```text
run time: 2026-06-15 08:55 KST
base_ts / forecast_origin_ts: 2026-06-15 09:00 KST
P-Max input_end_ts: 2026-06-15 08:45 KST
Anomaly input_end_ts: 2026-06-15 08:00 KST
report time: 2026-06-15 09:00 KST
```

**Runtime gates:**

```text
ALLOW_MODEL_SERVING_WRITE=1
ALLOW_PRODUCTION_MODEL_SERVING_WRITE=1 only in production write mode
canonical_writes_enabled=false always
reference/hybrid flags cannot be combined with production write unless explicitly allowed as non-production warm-start
```

**Output:**

- `mart.pmax_forecast_15min`
- `ops.pmax_forecast_inference_log`
- `mart.anomaly_warning_1h`
- `ops.anomaly_warning_inference_log`
- `qa.model_serving_evidence_packet`

**Acceptance:**

For a target `base_ts=T`:

```text
mart.pmax_forecast_15min rows where base_ts=T > 0
ops.pmax_forecast_inference_log status='success' for T
mart.anomaly_warning_1h rows where forecast_origin_ts=T > 0, when anomaly source coverage exists
ops.anomaly_warning_inference_log status='success' or explicit degraded/block reason for T
qa.model_serving_evidence_packet rows for T with writes_enabled=true and dry_run=false
```

**Block 조건:**

- stale `2024-01-01` rows are selected for a `2026-06-15` report.
- anomaly falls back to reference without explicit `reference_backfill` label.
- `canonical_writes_enabled=true` appears in model-serving batch.
- production write uses broad `cms` role without a verified dedicated runtime boundary, unless Viowlet explicitly accepts the temporary risk.

### Gate 29.5: report freshness gate and recognition path

**목표:** report가 model output을 정확히 인식하고 stale result를 차단한다.

**담당:** stark backend/report integration, frieren QA criteria, fern review

**Files:**

- Modify: `src/cms/workflow/report_readiness_airflow.py`
- Modify if needed: `src/cms/workflow/daily_report_airflow.py`
- Modify if needed: backend report API/router files under `src/cms/service/`
- Tests: `tests/workflow/test_report_model_freshness_gate.py`
- Tests: service route tests for model result freshness if backend exposes report status

**Recognition rule:**

For report timestamp `R`:

```text
required_pmax_base_ts = R
required_anomaly_forecast_origin_ts = R
```

Report status:

```text
PASS      if required rows and success logs exist for R
DEGRADED  if P-Max exists but anomaly is blocked with explicit reason, or warm-start mode is used and labelled
BLOCK     if neither current P-Max nor current anomaly status exists for R
```

**Acceptance:**

- Report no longer treats latest stale rows as current.
- Report includes exact `run_id`, `base_ts`, `forecast_origin_ts`, row counts, source_mode, quality_status, completed_at.
- Missing model output is visible as `blocked` or `degraded`, not silently omitted.

### Gate 29.6: observability and alerting

**목표:** operator가 promotion/model/report freshness를 Grafana/Prometheus에서 확인할 수 있게 한다.

**담당:** himmel observability, frieren metric QA

**Metrics:**

- Kafka lag by partition.
- DLQ offsets.
- `live.bucket_queue` pending/error/done.
- `live.promotion_check pass/block` count.
- canonical promotion lag:
  - eligible pass rows not yet in canonical.
- latest canonical bucket by resolution.
- latest `mart.anomaly_feature_1h.bucket_ts`.
- latest `mart.pmax_forecast_15min.base_ts`.
- latest `mart.anomaly_warning_1h.forecast_origin_ts`.
- `daily_report` latest DAG status and freshness verdict.

**Acceptance:**

- No wide `live.measurement_event` scan in Grafana panels.
- Dashboard uses Prometheus or bounded DB queries only.
- Model/report freshness alerts distinguish `stale`, `missing`, `degraded`, `success`.

### Gate 29.7: final operational E2E gate

**목표:** 09:00 report 기준으로 source부터 report까지 한 번에 증명한다.

**담당:** Orchestrator synthesis, fern independent review

**Required evidence:**

1. Kafka lag 0, DLQ 0.
2. `policy_miss=0` for active run scope.
3. `live.measurement_15min/1h` row counts increased and latest buckets match source availability.
4. `live.promotion_check` pass rows consumed by canonical promoter.
5. `canonical.measurement_15min/1h` row counts increased with correct promotion_id.
6. `mart.anomaly_feature_1h` materialized for required meters/windows.
7. `mart.pmax_forecast_15min` rows exist for report `base_ts`.
8. `mart.anomaly_warning_1h` rows or explicit degraded/block log exists for report `forecast_origin_ts`.
9. `ops.*_inference_log` success/degraded rows exist for same timestamp.
10. `qa.model_serving_evidence_packet` exists and points to same run_id/timestamp.
11. `daily_report` run reads the same timestamp and returns PASS/DEGRADED/BLOCK correctly.
12. fern returns PASS or explicit REQUEST_CHANGES with remaining blockers.

## 6. Role handoff

### himmel

- DB catalog introspection for canonical table keys and role boundaries.
- Canonical promotion SQL contract and privilege boundary.
- Airflow schedule/runtime deployment boundary.
- Grafana/Prometheus bounded metrics.

### stark

- Implement `canonical_promotion_runner.py` and CLI worker.
- Implement `anomaly_feature_materializer.py` and CLI runner.
- Wire scheduled model-serving execution.
- Add targeted tests.

### frieren

- Verify cadence/canonical eligibility policy against `docs/specs/measurement_processing_policy.md` and `docs/qa/qa_contract.md`.
- Define anomaly feature completeness, input_quality, and report degraded/block criteria.
- Review row count and coverage evidence.

### fern

- Independent review before any runtime write gate.
- Check no reference/corrected leakage into strict live/canonical path.
- Check idempotency, rollback/reconcile, source-mode labels, report freshness logic.
- Return `PASS`, `REQUEST_CHANGES`, or `BLOCK`.

### Orchestrator

- Keep task graph and acceptance gates aligned.
- Verify worker outputs with direct file/test/DB evidence.
- Do not claim E2E until Gate 29.7 passes.

## 7. Execution order

1. Gate 29.0 baseline inventory.
2. Gate 29.1 drain/catch-up observation while no new producer is started.
3. Gate 29.2 canonical promotion contract and local tests.
4. fern review for Gate 29.2.
5. Bounded canonical promotion smoke after explicit approval.
6. Gate 29.3 anomaly feature materialization contract and local tests.
7. fern review for Gate 29.3.
8. Bounded anomaly feature materialization smoke after explicit approval.
9. Gate 29.4 scheduled model-serving dry-run/no-write proof.
10. Production write gate only after role boundary and freshness criteria are verified.
11. Gate 29.5 report freshness gate.
12. Gate 29.6 observability.
13. Gate 29.7 final E2E and fern final review.

## 8. Stop conditions

Stop and report `BLOCK` if any of these occur:

- Kafka DLQ increases above 0.
- `policy_miss` reappears after full policy rollout.
- Canonical promoter cannot identify the exact canonical unique key.
- Promotion source includes `mart.peak_feature_15min`, `reference.*`, or corrected/backfill lineage in strict mode.
- Anomaly feature materializer cannot produce `mart.anomaly_feature_1h` from observed 1h facts.
- Model-serving write requires broad canonical privileges or uses `canonical_writes_enabled=true`.
- Report selects stale latest rows instead of exact report timestamp rows.
- Any worker needs destructive cleanup, broad DDL, or privilege changes without approval.

## 9. Immediate next command set after approval

Read-only baseline commands first:

```bash
# Kafka lag/DLQ on PC1
docker exec cms-kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --describe \
  --group postgres-live-ingest

docker exec cms-kafka /opt/kafka/bin/kafka-get-offsets.sh \
  --bootstrap-server localhost:9092 \
  --topic measurement_dead_letter_v1
```

Read-only DB checks:

```sql
BEGIN READ ONLY;

SELECT policy_lookup_status, count(*)
FROM live.measurement_event
WHERE source_event_id LIKE 'live_20230101_from_start:%'
GROUP BY policy_lookup_status
ORDER BY policy_lookup_status;

SELECT status, count(*)
FROM live.bucket_queue
GROUP BY status
ORDER BY status;

SELECT source_table, eligibility_status, count(*)
FROM live.promotion_check
GROUP BY source_table, eligibility_status
ORDER BY source_table, eligibility_status;

SELECT count(*) AS c15, max(bucket_ts) AS max15
FROM canonical.measurement_15min;

SELECT count(*) AS c1h, max(bucket_ts) AS max1h
FROM canonical.measurement_1h;

SELECT count(*) AS pmax_report_rows
FROM mart.pmax_forecast_15min
WHERE base_ts = timestamptz '2026-06-15 09:00:00+09';

SELECT count(*) AS anomaly_report_rows
FROM mart.anomaly_warning_1h
WHERE forecast_origin_ts = timestamptz '2026-06-15 09:00:00+09';

COMMIT;
```

Airflow/runtime checks:

```bash
docker exec cms-airflow-standalone airflow dags list
docker exec cms-airflow-standalone airflow dags list-import-errors
docker exec cms-airflow-standalone airflow dags list-runs -d daily_report
docker exec cms-airflow-standalone airflow dags list-runs -d model_serving_pipeline
```

## 10. Final acceptance statement

Gate 29 is complete only when this statement can be backed by real read-back evidence:

```text
For report_ts=<T>, source ingestion is drained or bounded-current, live rollups are policy-resolved, canonical promotion has continuously promoted eligible observed rows, anomaly features exist for the required 1h history, model-serving generated P-Max and anomaly outputs for T before the report run, qa evidence packets link to the same run IDs, and daily_report consumed T-specific rows or explicitly marked the report degraded/blocked.
```

Until then, the correct status is:

```text
Raw/live ingestion: in progress
Live policy/rollup: partial, improving
Canonical promotion: not continuous yet
Anomaly feature strict path: not ready
Model-serving scheduler: not ready
09:00 report with current model values: not ready
```
