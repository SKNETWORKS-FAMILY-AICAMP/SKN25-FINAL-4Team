# Live soak 운영 조건

## 목적

Phase 1 live ingestion 경로를 장시간 운영하기 전에 중단 조건, 성공 조건, 시간 증분 절차, 관측 지표를 고정한다.

대상 경로는 다음으로 제한한다.

```text
FastAPI /ingest/measurements
-> Kafka measurement_raw_v1
-> kafka_to_postgres_consumer
-> PostgreSQL live.measurement_event
-> Prometheus / Grafana 관측
```

## 실행 경계

허용 범위:

```text
live.measurement_event write
measurement_raw_v1 publish
measurement_dead_letter_v1 publish
ops.pipeline_metric 기록
Prometheus/Grafana 조회
```

금지 범위:

```text
canonical write
promotion 실행
production DDL
권한 변경
destructive cleanup
Kafka public exposure
secrets report
unbounded full replay
```

## 시간 증분의 의미

점진적 시간 늘림은 처리량을 갑자기 키우는 방식이 아니라, 같은 live path를 짧은 시간부터 긴 시간까지 단계적으로 유지하면서 누적 장애를 확인하는 절차다.

목적:

```text
1. 짧은 smoke에서 보이지 않는 누적 lag 확인
2. Kafka offset/consumer commit 안정성 확인
3. PostgreSQL connection/WAL/disk 증가 추세 확인
4. container restart, memory leak, exporter scrape 누락 확인
5. 운영 중단 기준을 실제 수치로 보정
```

예시 ladder:

```text
T0: 5분     기능 확인
T1: 15분    짧은 안정성 확인
T2: 30분    lag 회복성 확인
T3: 1시간   운영 후보 확인
T4: 3시간   장시간 전 중간 gate
T5: 6시간   반일 전 안정성 gate
T6: 24시간  1일 soak
```

각 단계는 이전 단계가 성공 조건을 만족할 때만 다음 단계로 이동한다.

## 기본 부하 조건

초기 live soak는 capacity max를 목표로 하지 않는다. 현재까지의 capacity evidence 기준으로 다음 범위를 사용한다.

```text
smoke 안정권: 50-100 events/sec
short soak 안정권: 100-200 events/sec
1h 후보: 200-300 events/sec
상한 탐색: 300-400 events/sec
금지: 첫 장시간 run에서 400 events/sec 초과
```

단일 consumer 기준으로 consumer lag가 계속 증가하면 처리량을 낮춘다.

## 성공 조건

각 run은 다음 조건을 모두 만족해야 성공으로 본다.

```text
consumer processed >= produced * 0.999
PostgreSQL inserted + duplicate + DLQ = processed
consumer committed = processed
DLQ 증가 = 0 또는 원인 식별 완료
Kafka consumer lag가 run 종료 후 5분 내 안정화 또는 감소 추세
container restart count 증가 없음
Prometheus targets all up
Grafana datasource query 정상
PostgreSQL live.measurement_event read-back 정상
canonical/promotion write 없음
```

장시간 gate 추가 조건:

```text
DB disk free >= 20%
Kafka disk free >= 20%
PostgreSQL active connections < 70% of max_connections
PostgreSQL deadlocks = 0
PostgreSQL exporter pg_up = 1
node exporter up = 1 for cms-stream and cms-db
consumer retry율 < 0.1%
```

## 중단 조건

다음 조건 중 하나라도 발생하면 run을 중단하고 원인 분석으로 전환한다.

```text
DLQ count 증가가 5분 이상 지속
consumer lag가 10분 연속 증가
consumer lag > 10000 for 5 minutes
consumer committed < processed
PostgreSQL write error 발생
PostgreSQL deadlocks > 0
PostgreSQL active connections >= 80% of max_connections
DB disk free < 20%
Kafka disk free < 20%
node memory available < 1 GiB for 5 minutes
CPU busy > 85% for 10 minutes
container restart 발생
Prometheus target down for 2 scrape intervals
Grafana datasource query 실패
p95 ingest latency > 2s for 5 minutes
p99 ingest latency > 5s for 5 minutes
```

즉시 중단 조건:

```text
canonical write 감지
promotion 실행 감지
production DDL 감지
secret 출력 감지
public Kafka/Grafana/Prometheus exposure 감지
```

## 단계별 gate

### T0 5분

목적: 기능 확인.

```text
목표: consumer/process/write/read-back 정상
성공 기준: DLQ 0, target all up, live rows 증가
다음 단계: T1 15분
```

### T1 15분

목적: 짧은 안정성 확인.

```text
목표: lag가 폭증하지 않음
성공 기준: lag 증가 추세 없음, DB connection 안정
다음 단계: T2 30분
```

### T2 30분

목적: lag 회복성 확인.

```text
목표: bounded producer 종료 후 consumer lag 감소 확인
성공 기준: run 종료 후 5분 내 lag 감소 또는 0 근접
다음 단계: T3 1시간
```

### T3 1시간

목적: 운영 후보 확인.

```text
목표: 1시간 동안 restart/error 없이 유지
성공 기준: system/PostgreSQL 지표 안정, deadlocks 0
다음 단계: T4 3시간
```

### T4 이상

목적: 장시간 운영 전 누적 위험 확인.

```text
목표: disk/WAL/memory/lag 추세 확인
성공 기준: linear growth가 예측 가능하고 중단 조건 미충족
다음 단계: 6시간 또는 24시간 soak
```

## 관측 지표

Kafka:

```text
kafka_consumergroup_lag_sum{consumergroup="postgres-live-ingest",topic="measurement_raw_v1"}
kafka_topic_partition_current_offset{topic="measurement_raw_v1"}
kafka_topic_partition_current_offset{topic="measurement_dead_letter_v1"}
```

Node:

```text
up{job="node-exporter-stream"}
up{job="node-exporter-db"}
node_cpu_seconds_total
node_memory_MemAvailable_bytes
node_filesystem_avail_bytes
```

PostgreSQL:

```text
pg_up{job="postgres-exporter"}
pg_stat_activity_count{datname="cms"}
pg_stat_database_xact_commit{datname="cms"}
pg_stat_database_xact_rollback{datname="cms"}
pg_stat_database_deadlocks{datname="cms"}
```

Database read-back:

```text
live.measurement_event row count
inserted_by_consumer count
kafka offset distinct count
duplicate count
policy_lookup_status distribution
```

## 보고 형식

각 run report는 다음 항목을 포함한다.

```text
run_id
start_ts / end_ts / duration
producer source and event count
consumer processed / inserted / duplicate / committed / DLQ / retry
Kafka lag before / max / after
PostgreSQL row count before / after
PostgreSQL active connection max
PostgreSQL deadlocks
CPU/memory/disk min/max summary
Prometheus target health
Grafana proxy query status
stop condition triggered 여부
next gate decision
```

## 다음 실행 제안

현재 exporter gate가 통과된 상태에서는 바로 24시간 run으로 가지 않는다. 먼저 다음 순서로 진행한다.

```text
T0 5분 smoke
T1 15분 short soak
T2 30분 lag recovery soak
T3 1시간 candidate soak
```

T3까지 성공하면 3시간 또는 6시간 run으로 확장한다.
