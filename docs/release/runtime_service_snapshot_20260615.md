# CMS Runtime Service Snapshot - 2026-06-15

This document is a secret-safe runtime snapshot for DEV push preparation. It records observed service placement and file parity without embedding passwords, tokens, private keys, or concrete server-local `.env` values.

## 1. Service placement

| Host | Observed containers/services | Push relevance |
|---|---|---|
| PC1 | `cms-ingestion-api`, `cms-backend-api`, `cms-frontend`, `cms-airflow-standalone`, `cms-kafka`, `cms-kafka-to-postgres-consumer`, `cms-kafka-to-postgres-consumer-2`, `cms-kafka-to-postgres-consumer-3`, `cms-live-bucket-queue-worker`, exporters/proxy | Keep edge stream API/consumer/Airflow/frontend templates. Host Python injector is stopped; next loading should resume after FastAPI/containerized injector conversion. |
| PC2 | `cms-kafka`, `cms-prometheus`, `cms-grafana`, `cms-kafka-exporter`, `cms-node-exporter` | Keep local Kafka broker and observability templates. |
| PC3 | `cms-hybrid-model-serving-scheduler`, `cms-anomaly-feature-worker`, `cms-canonical-promotion-worker`, `cms-kafka`, exporters | Keep model-serving containerfile/compose/scheduler scripts. Rebuilt `cms:model-serving` image is `sha256:eec4e1804b2c03133776464a77296740338fc4b6903510256ae311a109bb02ab`; all three model-serving containers import `torch 2.12.0+cpu`. |
| AWS | `cms-postgres`, `cms-grafana`, `cms-postgres-exporter`, `cms-db-node-exporter` | Keep sanitized AWS DB/observability compose templates; concrete `.env` stays server-local. |

## 2. Runtime decisions from the 2026-06-15 pass

- Kafka lag is backlog in consumer offsets, not API request latency.
- 2023 event timestamps are intentional for the historical replay/virtual-clock stream.
- PC1 injector was a host Python process and has been stopped. Latest checked Kafka consumer lag was `90756` total and draining; existing backlog should continue draining before additional source events are produced.
- Future ingestion should continue through a FastAPI/containerized injector service, not another unmanaged host process.
- PC1 backend and ingestion health labels are `CMS Backend API` and `CMS Ingestion API`; the legacy skeleton health label is no longer accepted.
- PC3 model-serving now runs the rebuilt torch-capable image. P-Max operational negative prediction handling uses `clip_zero`.
- PC3 Compose rebuild was completed with `--env-file docker/model_serving.env`; keep this invocation in docs because service-level `env_file:` does not participate in Compose interpolation.

## 3. Files mirrored or normalized into repo

| Source | Repo path | Action |
|---|---|---|
| AWS `/home/ubuntu/cms-deploy/compose.yml` | `docker/compose_aws_db.yml` | Added sanitized template. |
| AWS `/home/ubuntu/cms-deploy/compose.observability.yml` | `docker/compose_aws_db_observability.yml` | Existing template retained; use with server-local `.env`. |
| PC2/PC3 `/home/skn25/cms-local/docker/compose_local_kafka_broker.yml` | `docker/compose_local_kafka_broker.yml` | Added sanitized broker template. |
| PC1 `/home/skn25/cms-stream-deploy/docker/compose.local.stream.yml` | `docker/compose_edge_stream.yml` | Repo keeps renamed edge-stream template instead of old local-name copy. |
| PC3 `/home/skn25/cms-stream-deploy/docker/compose_model_serving.yml` | `docker/compose_model_serving.yml` | Repo keeps current service template; rebuild invocation documented. |

## 4. Push exclusion policy

Push service source/config/docs/tests only. Exclude:

```text
.env and concrete docker/*.env
artifacts/
reports/
.worktrees/
_archive/
knowledge/graphify/ generated copies
graphify-out/
frontend/dist/
frontend/node_modules/
cache/coverage outputs
```

## 5. Remaining follow-up

1. Add a managed FastAPI/containerized injector service before producing additional replay events.
2. Let existing Kafka backlog drain and record lag=0 or bounded residual evidence before claiming full catch-up.
3. Run compose config/test/secret-scan gates before DEV push.
