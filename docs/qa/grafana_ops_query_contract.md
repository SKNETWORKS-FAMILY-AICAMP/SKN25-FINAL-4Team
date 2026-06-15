# Grafana Ops Query Contract

**갱신일:** 2026-06-15
**상태:** active AWS table + target contract split
**범위:** Grafana PostgreSQL datasource에서 사용할 Kafka Phase 1 live pipeline monitoring query를 정의한다. 모든 query는 `SELECT` 전용이다.

## 1. Active AWS source tables

현재 AWS에서 active dashboard query가 직접 참조할 수 있는 table은 다음이다.

```text
live.measurement_event
ops.pipeline_metric
qa.meter_tag
qa.bad_row
mart.peak_feature_15min
mart.pmax_forecast_15min
ops.pmax_forecast_inference_log
qa.pmax_forecast_evaluation
qa.model_serving_evidence_packet
```

`mart.peak_input_15min` may exist as a legacy/helper projection, but P-Max runtime panels and model-serving checks must ground on `mart.peak_feature_15min`.

`qa.live_measurement_issue`, `live.measurement_policy`, `live.bucket_queue`, `ops.worker_heartbeat`, `ops.kafka_consumer_lag`, `ops.fastapi_ingest_metric`, `live.promotion_check`는 target contract 또는 future migration 대상이다. Active Grafana dashboard는 이 table들을 직접 조회하지 않는다.

Readiness status:

```text
24h test cleanup evidence = ops.pipeline_metric run_id pattern `aws_24h_%` and local gate dashboard evidence
active dashboard source policy = active AWS PostgreSQL tables + Prometheus exporter metrics only
target-only table policy = document and alert contract compatibility only; no active dashboard direct SQL until DDL approval
operator UX policy = meter_urn-first overview, measurement drill-down only after meter selection
```

이 split은 dashboard drift 방지용이다. `src/cms/contracts/observability.py`의 일부 constant는 target/live architecture 전체를 표현할 수 있지만, active dashboard JSON과 이 query contract의 PostgreSQL SQL은 위 active AWS table set을 기준으로 검증한다.

## 2. Target observability tables

아래 table은 live pipeline이 더 성숙해진 뒤 DDL review와 approval을 거쳐 추가할 target contract다.

```text
live.bucket_queue
live.measurement_policy
qa.live_measurement_issue
ops.pipeline_latency_event
ops.worker_heartbeat
ops.kafka_consumer_lag
ops.fastapi_ingest_metric
live.promotion_check
live.promotion_run
```

## 3. Readiness evidence queries

### Service-start Airflow/report evidence

서비스 시작 기준의 report/Airflow readiness query는 active AWS table만 조회한다. 대용량 `mart.peak_feature_15min`은 exact count/max scan 대신 catalog estimate로 존재·규모만 확인하고, P-Max serving/evidence tables는 작은 summary query로 확인한다.

```sql
WITH active_tables(name) AS (
  VALUES
    ('live.measurement_event'),
    ('ops.pipeline_metric'),
    ('qa.meter_tag'),
    ('qa.bad_row'),
    ('mart.peak_feature_15min'),
    ('mart.pmax_forecast_15min'),
    ('ops.pmax_forecast_inference_log'),
    ('qa.model_serving_evidence_packet')
), presence AS (
  SELECT name, to_regclass(name) AS oid, to_regclass(name) IS NOT NULL AS present
  FROM active_tables
), estimates AS (
  SELECT p.name, COALESCE(s.n_live_tup, c.reltuples)::bigint AS estimated_rows
  FROM presence p
  LEFT JOIN pg_class c ON c.oid = p.oid
  LEFT JOIN pg_stat_all_tables s ON s.relid = p.oid
), freshness AS (
  SELECT 'ops.pipeline_metric' AS name, max(metric_ts) AS latest_ts FROM ops.pipeline_metric
  UNION ALL SELECT 'qa.meter_tag', max(created_at) FROM qa.meter_tag
  UNION ALL SELECT 'qa.bad_row', max(created_at) FROM qa.bad_row
  UNION ALL SELECT 'mart.pmax_forecast_15min', max(target_ts) FROM mart.pmax_forecast_15min
  UNION ALL SELECT 'ops.pmax_forecast_inference_log', max(started_at) FROM ops.pmax_forecast_inference_log
  UNION ALL SELECT 'qa.model_serving_evidence_packet', max(created_at) FROM qa.model_serving_evidence_packet
)
SELECT p.name, p.present, e.estimated_rows, f.latest_ts
FROM presence p
LEFT JOIN estimates e USING (name)
LEFT JOIN freshness f USING (name)
ORDER BY p.name;
```

### 24h test cleanup / gate evidence

24h test cleanup 완료 상태는 destructive cleanup SQL로 검증하지 않는다. Grafana는 이미 적재된 `ops.pipeline_metric` evidence를 read-only로 조회한다.

```sql
SELECT
  run_id,
  max(metric_ts) AS latest_metric_ts,
  max(metric_value) FILTER (WHERE metric_name='producer_accepted') AS producer_accepted,
  max(metric_value) FILTER (WHERE metric_name='consumer_processed') AS consumer_processed,
  max(metric_value) FILTER (WHERE metric_name='consumer_inserted') AS consumer_inserted,
  max(metric_value) FILTER (WHERE metric_name='consumer_duplicate') AS consumer_duplicate,
  max(metric_value) FILTER (WHERE metric_name='consumer_dlq') AS consumer_dlq,
  max(metric_value) FILTER (WHERE metric_name='consumer_lag_after') AS consumer_lag_after
FROM ops.pipeline_metric
WHERE run_id LIKE 'aws_24h_%'
GROUP BY run_id
ORDER BY latest_metric_ts DESC
LIMIT 10;
```

## 4. Active panel queries

### Events last 5 minutes

```sql
SELECT count(*) AS events_last_5m
FROM live.measurement_event
WHERE consumed_at >= now() - interval '5 minutes';
```

### Active meters and series

```sql
SELECT
  count(DISTINCT meter_urn) AS active_meters_last_5m,
  count(DISTINCT meter_urn || '|' || measurement) AS active_series_last_5m
FROM live.measurement_event
WHERE consumed_at >= now() - interval '5 minutes';
```

### Meter fleet freshness

Static meter metadata comes from `docs/specs/meter_metadata.md`. Until a reviewed registry table exists, the dashboard may use a generated `meter_registry` CTE.

```sql
WITH meter_registry(meter_urn, meter_domain, meter_role, equipment_group, equipment_name, building_code, anomaly_priority) AS (...),
latest AS (
  SELECT
    r.meter_urn,
    r.meter_domain,
    r.meter_role,
    r.equipment_group,
    r.anomaly_priority,
    count(e.*) FILTER (WHERE e.consumed_at >= now() - interval '15 minutes') AS events_15m,
    count(e.*) FILTER (WHERE e.consumed_at >= now() - interval '1 hour') AS events_1h,
    max(e.consumed_at) AS latest_consumed_at,
    EXTRACT(EPOCH FROM now() - max(e.consumed_at)) AS consumed_age_sec,
    EXTRACT(EPOCH FROM max(e.consumed_at) - max(e.event_ts)) AS latest_source_to_consume_sec,
    COALESCE(max(e.policy_lookup_status), 'no_recent_event') AS policy_lookup_status,
    (max(e.consumed_at) IS NULL OR EXTRACT(EPOCH FROM now() - max(e.consumed_at)) > 900) AS stale_gt_15m
  FROM meter_registry r
  LEFT JOIN live.measurement_event e ON e.meter_urn = r.meter_urn
  GROUP BY r.meter_urn, r.meter_domain, r.meter_role, r.equipment_group, r.anomaly_priority
)
SELECT equipment_group, count(*) FILTER (WHERE stale_gt_15m) AS stale_meters
FROM latest
GROUP BY equipment_group;
```

### Recent QA tags and bad rows

```sql
SELECT * FROM (
  SELECT created_at AS issue_ts, 'meter_tag' AS source, tag AS issue_kind, meter_urn, measurement, detail
  FROM qa.meter_tag
  WHERE created_at >= now() - interval '24 hours'
  UNION ALL
  SELECT created_at AS issue_ts, 'bad_row' AS source, reason AS issue_kind, NULL::text AS meter_urn, NULL::text AS measurement, source_file || ':' || source_row_no::text AS detail
  FROM qa.bad_row
  WHERE created_at >= now() - interval '24 hours'
) q
ORDER BY issue_ts DESC
LIMIT 100;
```

### Consumer invariant from pipeline_metric

```sql
WITH latest AS (
  SELECT run_id FROM ops.pipeline_metric GROUP BY run_id ORDER BY max(metric_ts) DESC LIMIT 1
), pivot AS (
  SELECT
    max(metric_value) FILTER (WHERE metric_name='consumer_processed') AS processed,
    max(metric_value) FILTER (WHERE metric_name='consumer_inserted') AS inserted,
    max(metric_value) FILTER (WHERE metric_name='consumer_duplicate') AS duplicate,
    max(metric_value) FILTER (WHERE metric_name='consumer_retry') AS retry,
    max(metric_value) FILTER (WHERE metric_name='consumer_dlq') AS dlq,
    max(metric_value) FILTER (WHERE metric_name='consumer_committed') AS committed,
    max(metric_value) FILTER (WHERE metric_name='consumer_lag_after') AS lag_after
  FROM ops.pipeline_metric
  WHERE run_id IN (SELECT run_id FROM latest)
)
SELECT
  COALESCE(processed,0) - COALESCE(inserted,0) - COALESCE(duplicate,0) - COALESCE(dlq,0) AS invariant_delta,
  COALESCE(committed,0) - COALESCE(processed,0) AS commit_delta,
  retry,
  lag_after
FROM pivot;
```

## 5. Prometheus query basis

```promql
kafka_consumergroup_lag_sum{consumergroup="postgres-live-ingest",topic="measurement_raw_v1"}
sum by (partition) (kafka_consumergroup_lag{consumergroup="postgres-live-ingest",topic="measurement_raw_v1"})
increase(kafka_topic_partition_current_offset{topic="measurement_dead_letter_v1"}[5m])
100 * (1 - avg by (node) (rate(node_cpu_seconds_total{mode="idle"}[5m])))
100 * max by (node) (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)
100 * min by (node) (node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes{fstype!~"tmpfs|overlay"})
max(pg_up)
sum(pg_stat_activity_count{datname="cms"})
sum(increase(pg_stat_database_deadlocks{datname="cms"}[5m]))
```

## 6. Future metrics retained for contract compatibility

Target observability DDL should still provide these fields when the tables are deployed.

```text
source_to_fastapi_sec
fastapi_to_kafka_sec
kafka_to_event_sec
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
kafka_to_event_p95_sec
fastapi_ingest_request_rate
fastapi_ingest_4xx_count
fastapi_ingest_5xx_count
fastapi_ingest_p95_ms
```

## 7. Alert rules

| Rule | Severity | Query basis | Threshold |
|---|---|---|---|
| `no_live_events_5m` | P0 | `live.measurement_event` recent count | `count = 0` for 5m |
| `kafka_consumer_lag_10k` | P0 | Prometheus Kafka exporter or future `ops.kafka_consumer_lag` | `> 10000` |
| `kafka_dlq_count_nonzero` | P0 | Prometheus DLQ offset delta or future `kafka_dlq_count` | `> 0` |
| `kafka_produce_error_count_nonzero` | P0 | future `kafka_produce_error_count` | `> 0` |
| `fastapi_ingest_5xx_spike_5m` | P0 | future `fastapi_ingest_5xx_count` | `> 0` |
| `policy_miss_spike_10m` | P1 | `qa.meter_tag` now, future `qa.live_measurement_issue` | `policy_miss > 10 / 10m` |
| `peak_branch_lag_gt_mean_branch` | P1 | future `ops.pipeline_latency_event` | `> 300 sec` |
| `non_blocking_qa_warning_spike_10m` | P2 | `qa.meter_tag` now, future issue table | `severity IN ('low', 'medium', 'high') > 30 / 10m` |

## 8. Boundary

Grafana query는 read-only다. 운영자가 직접 수정할 대상은 Grafana가 아니라 별도 approval/audit이 있는 CLI 또는 future control plane이다.

금지:

```text
UPDATE live.*
DELETE live.*
INSERT canonical.*
DDL execution from dashboard
secret literal in provisioning files
```

## 8. 2026-06-10 validation evidence

- Local contract: `tests/verify/test_grafana_observability_contract.py` passed with 11 tests.
- AWS PostgreSQL read-only validation: 26 dashboard SQL queries executed inside `BEGIN READ ONLY` and `ROLLBACK`.
- AWS Prometheus validation: 10 dashboard PromQL expressions returned `status=success`.
- Grafana provisioning apply backup: `grafana/provisioning/dashboards/archive/applied_backups/20260610T010309Z`.
- Grafana health/log validation: database `ok`; no recent `status=400`, duplicate UID, or provisioning error after restart.
