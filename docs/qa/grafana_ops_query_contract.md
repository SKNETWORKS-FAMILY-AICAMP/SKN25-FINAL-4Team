# Grafana Ops Query Contract

**갱신일:** 2026-06-04  
**상태:** read-only query contract  
**범위:** Grafana PostgreSQL datasource에서 사용할 Kafka Phase 1 live pipeline monitoring query를 정의한다. 모든 query는 `SELECT` 전용이다.

## 1. Source tables

```text
live.measurement_event
live.bucket_queue
qa.live_measurement_issue
ops.pipeline_latency_event
ops.worker_heartbeat
ops.kafka_consumer_lag
ops.fastapi_ingest_metric
live.promotion_check
live.promotion_run
```

`ops.*` schema는 observability target contract다. 실제 배포 전 DDL/review가 필요하다.

## 2. Required panels

### Events last 5 minutes

```sql
SELECT count(*) AS value
FROM live.measurement_event
WHERE received_at >= now() - interval '5 minutes';
```

### FastAPI ingest health

```sql
SELECT
  date_trunc('minute', created_at) AS time,
  metric_name AS metric,
  sum(metric_value) AS value
FROM ops.fastapi_ingest_metric
WHERE created_at >= $__timeFrom() AND created_at <= $__timeTo()
  AND metric_name IN ('fastapi_ingest_request_rate', 'fastapi_ingest_4xx_count', 'fastapi_ingest_5xx_count', 'fastapi_ingest_p95_ms')
GROUP BY 1, 2
ORDER BY 1, 2;
```

### Kafka consumer lag

```sql
SELECT
  date_trunc('minute', observed_at) AS time,
  consumer_group || ':' || topic AS metric,
  max(lag) AS value
FROM ops.kafka_consumer_lag
WHERE observed_at >= $__timeFrom() AND observed_at <= $__timeTo()
GROUP BY 1, 2
ORDER BY 1, 2;
```

### Kafka DLQ / produce errors

```sql
SELECT
  date_trunc('minute', created_at) AS time,
  stage AS metric,
  sum(failed_count) AS value
FROM ops.pipeline_latency_event
WHERE created_at >= $__timeFrom() AND created_at <= $__timeTo()
  AND stage IN ('kafka_dlq_count', 'kafka_produce_error_count')
GROUP BY 1, 2
ORDER BY 1, 2;
```

### Queue by status / job / resolution

```sql
SELECT status, job_kind, resolution, count(*) AS rows, max(updated_at) AS last_update
FROM live.bucket_queue
GROUP BY status, job_kind, resolution
ORDER BY status, job_kind, resolution;
```

### Oldest pending queue age

```sql
SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(updated_at)), 0) AS value
FROM live.bucket_queue
WHERE status = 'pending';
```

### Recent blocking issues

```sql
SELECT created_at, issue_kind, severity, meter_urn, measurement, reason
FROM qa.live_measurement_issue
WHERE created_at >= now() - interval '1 hour'
ORDER BY created_at DESC
LIMIT 100;
```

### End-to-end latency p95

```sql
SELECT
  date_trunc('minute', created_at) AS time,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY end_to_end_sec) AS value
FROM ops.pipeline_latency_event
WHERE created_at >= $__timeFrom() AND created_at <= $__timeTo()
GROUP BY 1
ORDER BY 1;
```

### Stage latency p95

```sql
SELECT
  date_trunc('minute', created_at) AS time,
  stage AS metric,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_sec) AS value
FROM ops.pipeline_latency_event
WHERE created_at >= $__timeFrom() AND created_at <= $__timeTo()
GROUP BY 1, 2
ORDER BY 1, 2;
```

### Worker heartbeat

```sql
SELECT
  worker_name,
  status,
  EXTRACT(EPOCH FROM now() - heartbeat_at) AS heartbeat_age_sec,
  last_error,
  updated_at
FROM ops.worker_heartbeat
ORDER BY worker_name;
```

### Worker heartbeat missing alert value

```sql
WITH expected(worker_name) AS (
  VALUES
    ('kafka_to_postgres_consumer'),
    ('mean_rollup_worker'),
    ('peak_feature_worker'),
    ('qa_eligibility_worker'),
    ('promotion_worker'),
    ('report_worker')
)
SELECT max(
  CASE
    WHEN h.worker_name IS NULL THEN 999999
    ELSE EXTRACT(EPOCH FROM now() - h.heartbeat_at)
  END
) AS value
FROM expected e
LEFT JOIN ops.worker_heartbeat h ON h.worker_name = e.worker_name;
```

### Promotion readiness

```sql
SELECT bucket_ts, meter_urn, measurement, eligibility_status, block_reasons, checked_at
FROM live.promotion_check
WHERE checked_at >= now() - interval '1 hour'
ORDER BY checked_at DESC
LIMIT 100;
```

## 3. Alert rules

| Rule | Severity | Query basis | Threshold |
|---|---|---|---|
| `no_live_events_5m` | P0 | `live.measurement_event` recent count | `count = 0` for 5m |
| `oldest_pending_queue_age_10m` | P0 | `live.bucket_queue` oldest pending age | `> 600 sec` |
| `queue_failed_rows_present` | P0 | `live.bucket_queue` failed count | `> 0` for 3m |
| `worker_heartbeat_missing_2m` | P0 | `ops.worker_heartbeat` heartbeat age | `> 120 sec` |
| `end_to_end_p95_300s` | P0 | `ops.pipeline_latency_event.end_to_end_sec` | `p95 > 300 sec` |
| `kafka_consumer_lag_10k` | P0 | `ops.kafka_consumer_lag.lag` | `> 10000` |
| `kafka_dlq_count_nonzero` | P0 | `ops.pipeline_latency_event` DLQ counter | `> 0` |
| `kafka_produce_error_count_nonzero` | P0 | `ops.pipeline_latency_event` produce error counter | `> 0` |
| `fastapi_ingest_5xx_spike_5m` | P0 | `ops.fastapi_ingest_metric` 5xx count | `> 0` |
| `policy_miss_spike_10m` | P1 | `qa.live_measurement_issue` | `policy_miss > 10 / 10m` |
| `peak_branch_lag_gt_mean_branch` | P1 | `ops.pipeline_latency_event` branch comparison | `> 300 sec` |
| `non_blocking_qa_warning_spike_10m` | P2 | `qa.live_measurement_issue` non-blocking severity count | `severity IN ('low', 'medium', 'high') > 30 / 10m` |

## 4. Worker heartbeat contract

Target table:

```text
ops.worker_heartbeat
```

Allowed worker names:

```text
kafka_to_postgres_consumer
mean_rollup_worker
peak_feature_worker
qa_eligibility_worker
promotion_worker
report_worker
```

## 5. Pipeline latency contract

Target table:

```text
ops.pipeline_latency_event
```

Required fields:

```text
run_id
stage
event_id
meter_urn
measurement
bucket_ts
created_at
duration_sec
source_to_fastapi_sec
fastapi_to_kafka_sec
kafka_to_event_sec
kafka_to_event_p95_sec
event_to_1min_sec
event_to_queue_sec
one_min_to_15min_sec
one_min_to_1h_sec
one_min_to_peak_feature_sec
peak_feature_to_peak_input_sec
qa_eligibility_sec
promotion_ready_sec
end_to_end_sec
kafka_consumer_lag
kafka_dlq_count
kafka_produce_error_count
fastapi_ingest_request_rate
fastapi_ingest_4xx_count
fastapi_ingest_5xx_count
fastapi_ingest_p95_ms
blocked_count
failed_count
retry_count
evidence_level
```

## 6. Boundary

Grafana query는 read-only다. 운영자가 직접 수정할 대상은 Grafana가 아니라 별도 approval/audit이 있는 CLI 또는 future control plane이다.

금지:

```text
UPDATE live.*
DELETE live.*
INSERT canonical.*
UPDATE canonical.*
permission changes
DDL execution
```
