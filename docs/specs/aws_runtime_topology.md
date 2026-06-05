# AWS Phase 1 Runtime Topology

갱신일: 2026-06-04
상태: Phase 1 AWS two-tier topology contract
범위: FastAPI -> Kafka -> PostgreSQL -> Grafana 실행 경계

## 1. 목적

SKN25/CMS Phase 1은 AWS 내부에서 다음 ingestion path가 실제로 동작하는지 검증한다.

```text
FastAPI /ingest/measurements
-> Kafka measurement_raw_v1
-> kafka_to_postgres_consumer
-> PostgreSQL live.measurement_event
-> Grafana SELECT-only evidence
```

이 문서는 production HA 구성이 아니라 Phase 1 smoke/demo 구성을 정의한다. Kubernetes, MSK, multi-broker Kafka, S3/Spark execution은 Phase 1 실행 범위가 아니다.

## 2. 현재 AWS 노드

| Node | Public IP | Private IP | Instance | AZ | 역할 |
|---|---:|---:|---|---|---|
| `cms-db` | `13.209.98.228` | `172.31.47.236` | `t3.large` | `ap-northeast-2c` | PostgreSQL `cms`, Grafana target, legacy/debug MongoDB |
| `cms-stream` | `43.202.114.249` | `172.31.26.245` | `t3.large` | `ap-northeast-2b` | FastAPI, Kafka single broker, consumer, replay/smoke runner |

확인된 `cms-stream` 상태:

```text
vCPU: 2
RAM: 7.6 GiB
Disk: 100 GiB gp3 root volume
Docker: not installed yet
Open listener: SSH only
```

확인된 `cms-db` 상태:

```text
vCPU: 2
RAM: 7.6 GiB
Disk: 200 GiB root volume, 약 58 GiB free
PostgreSQL DB size: 약 77 GiB
Containers: cms-postgres, cms-mongo
```

## 3. 권장 서비스 배치

### `cms-db`

```text
cms-postgres
cms-grafana
cms-mongo legacy/debug only, not Phase 1 live path
```

`cms-db`는 source of record 성격의 PostgreSQL을 보호한다. Kafka, FastAPI, replay producer는 올리지 않는다.

### `cms-stream`

```text
cms-api
cms-kafka
cms-kafka-to-postgres-consumer
replay/smoke runner
```

`cms-stream`은 stream/app tier다. Kafka는 single broker KRaft mode로 시작한다. Phase 1에서는 HA broker cluster가 아니라 ingestion path 검증이 목표다.

## 4. Kafka contract

| 항목 | 값 |
|---|---|
| Raw topic | `measurement_raw_v1` |
| DLQ topic | `measurement_dead_letter_v1` |
| Consumer group | `postgres-live-ingest` |
| Producer key | `meter_urn|measurement` |
| Payload version | `measurement_raw_v1`, canonical schema version 아님 |

Kafka ordering은 global order가 아니라 같은 topic partition 안의 order다. Producer는 같은 `meter_urn|measurement` key를 사용해 series 단위 순서를 유지한다.

## 5. Security Group 요구사항

현재 확인 결과 `cms-stream -> cms-db` private network 접근은 막혀 있다. Phase 1 smoke 전에 아래 inbound rule이 필요하다.

### `cms-db` inbound 추가 필요

| Port | Source | Purpose |
|---:|---|---|
| `5432/tcp` | `172.31.26.245` 또는 `cms-stream` SG | consumer -> PostgreSQL |

선택 사항:

| Port | Source | Purpose |
|---:|---|---|
| `3000/tcp` | Viowlet current IP | Grafana browser access |

### `cms-stream` inbound

| Port | Source | Purpose |
|---:|---|---|
| `22/tcp` | Viowlet current IP | SSH |
| `8000/tcp` | Viowlet current IP only, 필요 시 | FastAPI smoke |

Kafka ports are not public.

```text
9092/tcp public open 금지
9093/tcp public open 금지
```

## 6. Runtime boundary

허용:

```text
local/import-safe tests
AWS container install/bootstrap after approval
Kafka topic creation after approval
FastAPI -> Kafka smoke after approval
Kafka -> PostgreSQL controlled smoke after approval
Grafana SELECT-only query smoke after approval
```

금지 또는 별도 승인 필요:

```text
production/canonical write
DDL 적용
permission 변경
destructive cleanup
S3/Spark execution
Kafka public exposure
.env/secrets disclosure
full replay/load without run scope
```

## 7. Capacity 판단

`t3.large` 2대 구성은 Phase 1 smoke/demo에 적합하다. 단, `cms-stream`은 다음 제한을 둔다.

```text
Kafka single broker
짧은 retention
small batch replay부터 시작
consumer 1개
long full replay 전 CPU/RAM/disk 관측
```

장시간 full replay나 high-throughput load가 필요하면 `cms-stream`만 `t3.xlarge`로 scale-up한다.

## 8. 다음 실행 전 checklist

1. `cms-stream`에 Docker/Compose 설치 여부 확인 및 설치 승인.
2. `cms-db` Security Group에 `5432 from cms-stream private IP` 추가.
3. `cms-stream` deploy root 생성.
4. non-secret env template 복사 후 secret 값은 서버에서 직접 입력.
5. Kafka topic 생성.
6. FastAPI health 확인.
7. one-event smoke.
8. duplicate smoke.
9. poison/DLQ smoke.
10. Grafana SELECT-only evidence 확인.
