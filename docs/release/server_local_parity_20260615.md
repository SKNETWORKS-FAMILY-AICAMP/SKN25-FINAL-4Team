# Server/Local Parity Check - 2026-06-15

## Summary

```json
{
  "total": 16,
  "exact_match": 12,
  "mismatch": 4,
  "missing_remote": 0
}
```

## Compared files

| Host | Local path | Remote path | Result | Note |
|---|---|---|---|---|
| PC1 | `docker/compose.edge_stream.yml` | `/home/skn25/cms-stream-deploy/docker/compose.local.stream.yml` | DIFF |  |
| PC1 | `scripts/live/run_consumer_service.py` | `/home/skn25/cms-stream-deploy/scripts/live/run_consumer_service.py` | MATCH |  |
| PC1 | `scripts/stream/bucket_queue_worker.py` | `/home/skn25/cms-stream-deploy/scripts/stream/bucket_queue_worker.py` | MATCH |  |
| PC1 | `scripts/stream/consumer_service.py` | `/home/skn25/cms-stream-deploy/scripts/stream/consumer_service.py` | MATCH |  |
| PC1 | `src/cms/data/runtime_consumer_loop.py` | `/home/skn25/cms-stream-deploy/src/cms/data/runtime_consumer_loop.py` | MATCH |  |
| PC1 | `src/cms/data/runtime_postgres.py` | `/home/skn25/cms-stream-deploy/src/cms/data/runtime_postgres.py` | MATCH |  |
| PC1 | `src/cms/service/api.py` | `/home/skn25/cms-stream-deploy/src/cms/service/api.py` | MATCH |  |
| PC3 | `docker/compose.model_serving.yml` | `/home/skn25/cms-stream-deploy/docker/compose.model_serving.yml` | MATCH |  |
| PC3 | `docker/model_serving_containerfile` | `/home/skn25/cms-stream-deploy/docker/model_serving_containerfile` | MATCH |  |
| PC3 | `docker/requirements.model_serving.txt` | `/home/skn25/cms-stream-deploy/docker/requirements.model_serving.txt` | MATCH |  |
| PC3 | `scripts/serving/run_model_serving.py` | `/home/skn25/cms-stream-deploy/scripts/serving/run_model_serving.py` | MATCH |  |
| PC3 | `scripts/serving/run_operational_scheduler.py` | `/home/skn25/cms-stream-deploy/scripts/serving/run_operational_scheduler.py` | MATCH |  |
| PC3 | `src/cms/modeling/pmax_forecast_adapter.py` | `/home/skn25/cms-stream-deploy/src/cms/modeling/pmax_forecast_adapter.py` | MATCH |  |
| PC2 | `docker/compose.local.kafka-broker.yml` | `/home/skn25/cms-local/docker/compose.local.kafka-broker.yml` | DIFF | sanitized template may differ from server concrete compose |
| AWS | `docker/compose.aws.db.observability.yml` | `/home/ubuntu/cms-deploy/compose.observability.yml` | DIFF | repo file is sanitized template; exact hash can differ from server concrete compose |
| AWS | `docker/compose.aws.db.yml` | `/home/ubuntu/cms-deploy/compose.yml` | DIFF | repo file is sanitized template; exact hash can differ from server concrete compose |

## Interpretation

- `MATCH`: remote source file and local repo file have the same SHA-256 hash.
- `DIFF` on sanitized compose templates is acceptable when the remote file contains server-local concrete settings and the repo file intentionally stores a secret-safe template.
- PC1/PC3 Python service source parity is exact for the checked runtime-critical files.
