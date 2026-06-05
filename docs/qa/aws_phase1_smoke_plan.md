# AWS Phase 1 Smoke Plan

갱신일: 2026-06-04
상태: 실행 전 smoke contract
범위: `cms-stream` -> Kafka -> `cms-db` PostgreSQL -> Grafana evidence

## 1. Evidence level

이 smoke는 실제 AWS container와 Kafka/PostgreSQL을 사용하므로 `aws_phase1_smoke` evidence로 분류한다.

이전 단계인 `local/import-safe mocked adapter integration`과 구분한다.

## 2. 사전 조건

- `cms-stream` Docker/Compose 설치 완료.
- `cms-db` PostgreSQL 접근 허용: `5432/tcp from 172.31.26.245`.
- Kafka public port 미개방.
- PostgreSQL secret은 채팅/문서에 보존하지 않음.
- `.env`는 서버에서 직접 작성하고 repository에 저장하지 않음.

## 3. Live source archive readiness

`cms-stream`의 live stream injector는 다음 source archive를 읽는다.

```text
/home/ubuntu/cms-stream-deploy/data/live_source/harmonized
```

검증 기준:

```text
*_harmonized.csv.gz count recorded
source archive bytes recorded
source event_ts 보존
received_at은 ingest 시점 wall-clock으로 분리
PostgreSQL write 없음
```

## 4. Topic readiness

대상 topic:

```text
measurement_raw_v1
measurement_dead_letter_v1
```

검증:

```text
topic exists
partition count recorded
retention policy recorded
consumer group can be created
```

## 5. Smoke cases

### TC1. FastAPI validation failure

Input:

```text
missing meter_urn or missing value
```

Expected:

```text
HTTP 422-style payload
Kafka publish 없음
DLQ publish 없음
PostgreSQL write 없음
```

### TC2. FastAPI -> Kafka accepted event

Input:

```text
valid one measurement event
```

Expected:

```text
HTTP 202
Kafka topic = measurement_raw_v1
Kafka key = meter_urn|measurement
PostgreSQL write는 FastAPI에서 직접 발생하지 않음
```

### TC3. Kafka -> PostgreSQL idempotent insert

Input:

```text
TC2 message consumed by kafka_to_postgres_consumer
```

Expected:

```text
live.measurement_event row inserted
business idempotency key recorded
Kafka topic/partition/offset recorded as transport metadata
commit offset only after DB transaction success
```

### TC4. Duplicate live event

Input:

```text
same source_system + source_event_id
```

Expected:

```text
idempotent_noop
row count unchanged
commit offset after successful duplicate/noop transaction
```

### TC5. Poison Kafka message -> DLQ

Input:

```text
non-object value or invalid value_numeric with no valid value_text
```

Expected:

```text
measurement_dead_letter_v1 publish
commit offset only after DLQ ack
PostgreSQL write 없음
```

### TC6. DLQ failure safety

Input:

```text
invalid message while DLQ producer unavailable
```

Expected:

```text
commit_offset = false
message retry 가능
```

### TC7. Grafana query safety

Expected:

```text
queries are SELECT-only
kafka_to_postgres_consumer heartbeat visible
kafka produce error count visible
fastapi ingest 5xx count visible
fastapi ingest p95 visible
```

## 6. Latency metrics

최소 smoke 기록:

```text
fastapi_to_kafka_sec
kafka_to_event_sec
end_to_end_sec
```

확장 smoke 기록:

```text
source_to_fastapi_sec
fastapi_to_kafka_sec
kafka_to_event_sec
event_to_1min_sec
event_to_queue_sec
one_min_to_15min_sec
one_min_to_1h_sec
qa_eligibility_sec
promotion_ready_sec
end_to_end_sec
```

## 7. 금지 경계

```text
canonical write 금지
promotion 실행 금지
production DDL 금지
Kafka public exposure 금지
secrets report 금지
bounded live run/load 금지, 별도 run scope 승인 필요
S3/Spark execution 금지
```

## 8. 보고 형식

Smoke report는 다음을 포함한다.

```text
run_id
AWS node names
container names
topic names
source event count
Kafka produced count
consumer processed count
PostgreSQL inserted count
DLQ count
duplicate count
commit/no-commit decisions
latency p50/p95/max
failures and retry notes
cleanup/reconcile notes
```
