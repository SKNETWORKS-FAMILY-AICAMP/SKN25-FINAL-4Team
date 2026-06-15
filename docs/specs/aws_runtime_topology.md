# AWS Runtime Topology

갱신일: 2026-06-15
상태: active AWS DB/observability topology plus PC1~PC3 edge-runtime handoff
범위: AWS에서 실제 사용 중인 PostgreSQL/Grafana service files와 PC1~PC3 edge services의 연결 경계를 기록한다.

## 1. 현재 결론

SKN25/CMS active runtime은 과거 AWS two-tier `cms-stream` 중심 구성이 아니라, PC1~PC3 edge cluster와 AWS DB plane을 함께 사용한다.

```text
PC1 FastAPI ingestion
-> PC1~PC3 Kafka measurement_raw_v1
-> PC1 Kafka-to-PostgreSQL consumers
-> AWS PostgreSQL live/mart/ops/qa tables
-> PC3 model-serving workers/scheduler
-> Grafana/Prometheus observability
```

AWS는 현재 PostgreSQL/Grafana/exporter plane으로 동작한다. Kafka/API/model-serving execution은 PC1~PC3 edge hosts에서 수행된다.

## 2. AWS active services

2026-06-15 secret-safe SSH inventory 기준:

| Container | Image | Role | Bind/volume evidence |
|---|---|---|---|
| `cms-postgres` | `timescale/timescaledb:latest-pg16` | PostgreSQL/TimescaleDB | `127.0.0.1:5432`, data mount `./postgres:/var/lib/postgresql/data` |
| `cms-grafana` | `grafana/grafana:11.5.2` | Grafana UI/provisioning | `127.0.0.1:3000`, provisioning mount `./grafana/provisioning:/etc/grafana/provisioning` |
| `cms-postgres-exporter` | `prometheuscommunity/postgres-exporter:v0.15.0` | PostgreSQL metrics | private bind `:9187` |
| `cms-db-node-exporter` | `prom/node-exporter:v1.8.2` | host metrics | private bind `:9100` |

Compose project:

```text
name: cms
config files:
  /home/ubuntu/cms-deploy/compose.yml
  /home/ubuntu/cms-deploy/compose.observability.yml
```

Sanitized DEV templates:

```text
docker/compose_aws_db.yml
docker/compose_aws_db_observability.yml
```

Concrete `.env` values remain server-local and must not be committed.

## 3. PC1~PC3 edge runtime relationship

| Host | Runtime role | Compose/template path |
|---|---|---|
| PC1 | ingestion API, backend API, frontend, Airflow, Kafka broker, 3x DB-writing consumers, live bucket worker | `docker/compose_edge_stream.yml` plus service-specific server env |
| PC2 | Kafka broker, Prometheus, Grafana, exporters | `docker/compose_local_kafka_broker.yml` and observability provisioning |
| PC3 | Kafka broker, canonical/anomaly/model-serving workers | `docker/compose_model_serving.yml`, `docker/model_serving_containerfile` |

PC3 model-serving Compose commands should be run with an explicit env file for interpolation:

```bash
docker compose --env-file docker/model_serving.env -f docker/compose_model_serving.yml --profile operational-scheduler config --quiet
docker compose --env-file docker/model_serving.env -f docker/compose_model_serving.yml --profile operational-scheduler up -d --build cms-canonical-promotion-worker cms-anomaly-feature-worker cms-hybrid-model-serving-scheduler
```

`env_file:` inside a service is not enough for `${POSTGRES_DB:? ...}` interpolation at Compose parse time.

## 4. Security and push boundary

- `.env`, DB password, Grafana admin password, SSH key, token, server-local runtime secrets are not committed.
- `docker/*.env.example` may be committed as non-secret templates.
- `artifacts/`, `reports/`, local runtime snapshots, generated graph outputs, and model binaries are push-excluded.
- Kafka ports stay on trusted LAN/router-managed paths; do not expose Kafka publicly by default.
- Production/canonical writes, DDL, destructive cleanup, and privilege changes remain approval-gated.

## 5. Retired historical wording

Older documentation that describes `cms-stream` as the active AWS FastAPI/Kafka/consumer node is historical. It may remain only if labelled as prior Phase 1 AWS-only plan. Current service push should use PC1~PC3 edge runtime plus AWS DB plane wording.
