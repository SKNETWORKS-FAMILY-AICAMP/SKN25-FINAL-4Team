# Live Schema Migration Plan

**갱신일:** 2026-06-04  
**상태:** review-only draft  
**범위:** `live`, `qa`, `mart` schema의 offline DDL 초안과 trigger/worker 경계를 정의한다. 이 문서는 DB 실행 승인서가 아니다.

## 1. 목적

이 계획은 live event pipeline의 DB object를 실제 DB에 적용하기 전 검토하기 위한 migration packet이다.

대상 flow는 다음과 같다.

```text
source event
-> FastAPI POST /ingest/measurements
-> Kafka measurement_raw_v1
-> kafka_to_postgres_consumer
-> live.measurement_event
-> common trigger + live.measurement_policy
-> live.measurement_1min + live.bucket_queue
-> workers
-> QA eligibility
-> approval-gated promotion
```

## 2. 산출물

```text
scripts/migrations/live_schema_draft.sql
```

해당 SQL은 review-only draft다. production, AWS, canonical 환경에 직접 실행하지 않는다.

## 3. Migration order

권장 순서는 다음과 같다.

```text
1. CREATE SCHEMA live, qa, mart, ops
2. live.measurement_policy
3. live.measurement_event
4. live.measurement_1min / live.measurement_15min / live.measurement_1h
5. live.bucket_queue
6. qa.live_measurement_issue
7. live.promotion_check / live.promotion_run
8. ops.worker_heartbeat / ops.pipeline_latency_event / ops.kafka_consumer_lag / ops.fastapi_ingest_metric
9. mart.peak_feature_15min / mart.peak_input_15min
10. live.handle_measurement_event_insert() trigger function
11. measurement_event_insert_live_trigger enable
12. kafka_to_postgres_consumer and worker rollout
```

이 순서는 trigger가 참조하는 policy, bucket, issue, queue object가 먼저 존재하도록 한다.

## 4. Trigger responsibility

Trigger 허용 작업은 다음뿐이다.

```text
policy lookup
live.measurement_1min upsert
live.bucket_queue enqueue
qa.live_measurement_issue log
```

Trigger 금지 작업은 다음과 같다.

```text
15min/1h mean rollup
peak_value / peak_ts calculation
mart write
QA eligibility full evaluation
canonical write
external API call
bulk scan
long transaction
```

## 5. Queue contract

`live.bucket_queue` idempotency key는 다음 조합이다.

```text
(meter_urn, measurement, resolution, bucket_ts, job_kind, policy_version)
```

허용 job은 다음 세 종류다.

```text
(job_kind='mean_rollup', resolution='15min')
(job_kind='mean_rollup', resolution='1h')
(job_kind='peak_feature', resolution='15min')
```

`peak_feature / 1h`는 현재 contract에 없다. 1h peak feature는 우선 `mart.peak_input_15min`의 rolling 1h feature로 관리한다.

## 6. Rollback boundary

Rollback은 data deletion 중심이 아니라 trigger disable, worker stop, queue status reconciliation 중심으로 설계한다.

```text
1. Disable trigger
2. Stop workers
3. Mark pending/running queue rows as failed or pending for retry
4. Keep live.measurement_event ledger append-only
5. Rebuild live.measurement_1min from event ledger + policy if required
6. Rebuild live.measurement_15min/1h from live.measurement_1min if required
7. Rebuild mart peak features from live branch if required
```

Canonical rollback은 이 migration packet의 범위가 아니다. Canonical write는 별도 approval, promotion_id, reconcile plan을 요구한다.

## 7. Reconcile boundary

Source of truth는 다음 순서로 둔다.

```text
live.measurement_event
-> live.measurement_1min
-> live.measurement_15min / live.measurement_1h
-> live.promotion_check
-> controlled promotion
```

Peak feature branch는 별도다.

```text
live.measurement_1min
-> mart.peak_feature_15min
-> mart.peak_input_15min
```

Peak feature row는 canonical candidate가 아니다.

## 8. Review blockers before DB execution

실제 DB 적용 전 다음 사항을 확정해야 한다.

- `live.measurement_event` idempotency source: `(source_system, source_event_id)` 우선, fallback `(raw_payload_hash, meter_urn, measurement, event_ts)` 기준. Kafka offset은 business idempotency key가 아님
- `qa.live_measurement_issue` severity 및 issue_kind enum 확정
- `live.measurement_1min` expected_points 계산을 policy JSON에서 어떻게 구체화할지 확정
- COV `state_hold_last` evidence fields 확정
- cumulative/delta/reset policy가 없는 series block 처리 확정
- trigger latency budget 확정
- scratch run_id, schema, Kafka fixture/topic metadata 승인

## 9. Verification before execution

DB 실행 전 최소 검증은 다음이다.

```text
SQL token check
contract tests
scratch guard tests
independent review
```

실제 DB write가 필요한 검증은 별도 scratch approval 이후 수행한다.
