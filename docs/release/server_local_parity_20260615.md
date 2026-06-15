# Server-local parity check - 2026-06-15

## Scope

Secret-safe verification using the project `.env` for PC1~PC3 public/router-forward SSH endpoints and `/home/viowlet/.ssh/cms.pem` for AWS. The private overlay network path was intentionally excluded. Secret values, DB passwords, tokens, and concrete server-local env values are excluded.

## Summary

| Verdict | Count |
|---|---:|
| DIFF | 6 |
| MATCH | 18 |


## File parity table

| Host | Verdict | Local push path | Remote runtime path | Interpretation |
|---|---|---|---|---|
| PC1 | DIFF | `src/cms/service/api.py` | `/home/skn25/cms-stream-deploy/src/cms/service/api.py` | Source drift: local contains the safer DB-unavailable handling not yet deployed on PC1 runtime. Do not claim server-local exact match for this file. |
| PC1 | MATCH | `src/cms/data/runtime_postgres.py` | `/home/skn25/cms-stream-deploy/src/cms/data/runtime_postgres.py` | Exact hash match. |
| PC1 | MATCH | `src/cms/data/runtime_consumer_loop.py` | `/home/skn25/cms-stream-deploy/src/cms/data/runtime_consumer_loop.py` | Exact hash match. |
| PC1 | MATCH | `src/cms/data/runtime_kafka.py` | `/home/skn25/cms-stream-deploy/src/cms/data/runtime_kafka.py` | Exact hash match. |
| PC1 | MATCH | `src/cms/data/postgres_event_writer.py` | `/home/skn25/cms-stream-deploy/src/cms/data/postgres_event_writer.py` | Exact hash match. |
| PC1 | MATCH | `scripts/live/run_consumer_service.py` | `/home/skn25/cms-stream-deploy/scripts/live/run_consumer_service.py` | Exact hash match. |
| PC1 | MATCH | `scripts/stream/consumer_service.py` | `/home/skn25/cms-stream-deploy/scripts/stream/consumer_service.py` | Exact hash match. |
| PC1 | MATCH | `scripts/stream/bucket_queue_worker.py` | `/home/skn25/cms-stream-deploy/scripts/stream/bucket_queue_worker.py` | Exact hash match. |
| PC1 | DIFF | `docker/compose_edge_stream.yml` | `/home/skn25/cms-stream-deploy/docker/compose.edge_stream.yml` | Diff expected for sanitized repo template vs concrete server runtime compose/env boundary. |
| PC1 | DIFF | `docker/compose_edge_stream.yml` | `/home/skn25/cms-stream-deploy/docker/compose.local.stream.yml` | Diff expected for sanitized repo template vs concrete server runtime compose/env boundary. |
| PC1 | MATCH | `docker/dockerfile_phase1` | `/home/skn25/cms-stream-deploy/docker/Dockerfile.phase1` | Exact hash match. |
| PC2 | DIFF | `docker/compose_local_kafka_broker.yml` | `/home/skn25/cms-local/docker/compose.local.kafka-broker.yml` | Diff expected for sanitized repo template vs concrete server runtime compose/env boundary. |
| PC3 | MATCH | `scripts/serving/run_model_serving.py` | `/home/skn25/cms-stream-deploy/scripts/serving/run_model_serving.py` | Exact hash match. |
| PC3 | MATCH | `scripts/serving/run_operational_scheduler.py` | `/home/skn25/cms-stream-deploy/scripts/serving/run_operational_scheduler.py` | Exact hash match. |
| PC3 | MATCH | `scripts/serving/materialize_anomaly_features.py` | `/home/skn25/cms-stream-deploy/scripts/serving/materialize_anomaly_features.py` | Exact hash match. |
| PC3 | MATCH | `scripts/stream/canonical_promotion_worker.py` | `/home/skn25/cms-stream-deploy/scripts/stream/canonical_promotion_worker.py` | Exact hash match. |
| PC3 | MATCH | `src/cms/modeling/pmax_forecast_adapter.py` | `/home/skn25/cms-stream-deploy/src/cms/modeling/pmax_forecast_adapter.py` | Exact hash match. |
| PC3 | MATCH | `src/cms/data/model_serving_postgres.py` | `/home/skn25/cms-stream-deploy/src/cms/data/model_serving_postgres.py` | Exact hash match. |
| PC3 | MATCH | `src/cms/data/model_serving_queries.py` | `/home/skn25/cms-stream-deploy/src/cms/data/model_serving_queries.py` | Exact hash match. |
| PC3 | MATCH | `docker/compose_model_serving.yml` | `/home/skn25/cms-stream-deploy/docker/compose.model_serving.yml` | Exact hash match. |
| PC3 | MATCH | `docker/model_serving_containerfile` | `/home/skn25/cms-stream-deploy/docker/model_serving_containerfile` | Exact hash match. |
| PC3 | MATCH | `docker/requirements.model_serving.txt` | `/home/skn25/cms-stream-deploy/docker/requirements.model_serving.txt` | Exact hash match. |
| AWS | DIFF | `docker/compose_aws_db.yml` | `/home/ubuntu/cms-deploy/compose.yml` | Diff expected for sanitized repo template vs concrete server runtime compose/env boundary. |
| AWS | DIFF | `docker/compose_aws_db_observability.yml` | `/home/ubuntu/cms-deploy/compose.observability.yml` | Diff expected for sanitized repo template vs concrete server runtime compose/env boundary. |


## Reports directory review

| Requested item | Observed files | Push decision |
|---|---|---|
| server_local_consistency | `reports/server_local_consistency_20260615.md` | `reports/` is ignored and not part of DEV push. Stale report facts are not promoted as service truth. |
| `reports/pipeline_comleteness_*` | `none` | `reports/` is ignored and not part of DEV push. Stale report facts are not promoted as service truth. |
| pipeline_completeness | `reports/pipeline_completeness_assessment_20260615.md` | `reports/` is ignored and not part of DEV push. Stale report facts are not promoted as service truth. |


## Current blockers / differences

1. `src/cms/service/api.py` differs between local and PC1. Local adds defensive DB-unavailable handling for model result summary reads. PC1 runtime remains older for that function only.
2. PC1/PC2/AWS compose files differ by design because repo files are sanitized templates and servers carry concrete runtime filenames/env boundaries.
3. PC3 model-serving critical source/config files match local exactly.

## Verification commands

- `.env` key-presence check without secret output.
- SSH container inventory for PC1, PC2, PC3 via public/router-forward ports.
- AWS SSH via `/home/viowlet/.ssh/cms.pem`.
- SHA-256 hash comparison for 24 critical runtime files.
