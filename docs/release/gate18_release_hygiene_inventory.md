# Gate 18 Release Hygiene Inventory

- generated_at_utc: `2026-06-13T18:05:16.617150+00:00`
- scope: `git status --short` classification only
- destructive_cleanup: `not performed`
- canonical_or_prod_write: `not performed`
- verdict: `release/dev-overwrite BLOCK until cleanup manifest is approved and applied`

## Summary

- total_dirty_or_untracked_entries: `117`
- config_or_container: `21`
- docs_or_knowledge: `16`
- generated_external_or_large_artifact: `3`
- source_or_runtime_code: `63`
- tests: `14`

## Gate 24 / Gate 25 related release candidates

- `??` `docker/model_serving.env.example`
- `??` `docker/model_serving_containerfile`
- `??` `docker/requirements.model_serving.txt`
- `??` `scripts/serving/`
- `??` `src/cms/data/model_serving_queries.py`
- `??` `tests/serving/`
- `??` `tests/test_model_serving_db_contract_fit.py`

## Classification Detail

### config_or_container
- ` M` `.gitignore`
- ` M` `docker-compose.yml`
- ` M` `docker/Dockerfile.phase1`
- ` M` `docker/aws_phase1.env.example`
- `RM` `docker/Dockerfile -> docker/backend_containerfile`
- ` M` `docker/compose.aws.phase1.kafka3.override.yml`
- ` M` `docker/compose.aws.phase1.yml`
- ` M` `docker/grafana/provisioning/dashboards/json/cms_runtime_operations.json`
- ` M` `docker/grafana/provisioning/dashboards/json/cms_test_gates.json`
- ` M` `docker/requirements.phase1.txt`
- ` M` `requirements.txt`
- `??` `docker/compose.edge_stream.yml`
- `??` `docker/compose.kafka_cluster.yml`
- `??` `docker/compose.model_serving.yml`
- `??` `docker/grafana/provisioning/datasources/prometheus_edge_cluster.yaml`
- `??` `docker/kafka_cluster.env.example`
- `??` `docker/model_serving.env.example`
- `??` `docker/model_serving_containerfile`
- `??` `docker/prometheus/edge_cluster.yml`
- `??` `docker/requirements.model_serving.txt`
- `??` `docker/stream.env.example`

### docs_or_knowledge
- ` M` `README.md`
- ` M` `docs/qa/grafana_observability_plan.md`
- ` M` `docs/qa/grafana_ops_query_contract.md`
- ` M` `docs/qa/live_soak_plan.md`
- ` M` `docs/specs/data_platform_contract.md`
- ` M` `docs/specs/diagrams/README.md`
- ` M` `docs/specs/diagrams/stack_architecture_overview.svg`
- ` M` `docs/specs/project_overview.md`
- ` M` `docs/specs/runtime_architecture.md`
- `R ` `graphify-out/graph.json -> knowledge/graphify/graph.json`
- `R ` `graphify-out/graph_tree.html -> knowledge/graphify/graph_tree.html`
- `RM` `graphify-out/manifest.json -> knowledge/graphify/manifest.json`
- `??` `docs/release/`
- `??` `docs/specs/aws_a_to_z_db_contract.md`
- `??` `docs/specs/backend_frontend_api_contract.md`
- `??` `docs/specs/diagrams/erd_00_live_pipeline_contract.dbml`

### generated_external_or_large_artifact
- `??` `artifacts/`
- `??` `evaluation/`
- `??` `frontend/`

### source_or_runtime_code
- ` M` `scripts/live/run_consumer_service.py`
- `AM` `scripts/stream/consumer_service.py`
- `AM` `scripts/stream/ingest_replay.py`
- ` M` `src/cms/contracts/agent.py`
- ` M` `src/cms/contracts/core.py`
- ` M` `src/cms/contracts/ingestion.py`
- ` M` `src/cms/contracts/live_pipeline.py`
- ` M` `src/cms/contracts/pmax_forecast_15min.py`
- ` M` `src/cms/data/live_scratch_adapter.py`
- ` M` `src/cms/data/live_workers.py`
- ` M` `src/cms/data/postgres_event_writer.py`
- ` M` `src/cms/data/runtime_consumer_loop.py`
- ` M` `src/cms/data/runtime_kafka.py`
- ` M` `src/cms/data/runtime_postgres.py`
- ` M` `src/cms/data/stream_consumer.py`
- ` M` `src/cms/data/stream_consumer_runner.py`
- ` M` `src/cms/modeling/pmax_artifact_loader.py`
- ` M` `src/cms/modeling/pmax_feature_builder.py`
- ` M` `src/cms/modeling/pmax_forecast_adapter.py`
- ` M` `src/cms/service/api.py`
- ` M` `src/cms/workflow/airflow_skeleton.py`
- ` M` `src/cms/workflow/champion_airflow_skeleton.py`
- ` M` `src/cms/workflow/langgraph_skeleton.py`
- ` M` `src/cms/workflow/pmax_forecast_tasks.py`
- ` M` `src/cms/workflow/review_jobs.py`
- `??` `dags/`
- `??` `scripts/database/`
- `??` `scripts/deploy/`
- `??` `scripts/serving/`
- `??` `scripts/stream/bucket_queue_worker.py`
- `??` `scripts/verify/build_release_manifest.py`
- `??` `src/cms/contracts/anomaly_detection_1h.py`
- `??` `src/cms/contracts/evaluation.py`
- `??` `src/cms/contracts/retrieval.py`
- `??` `src/cms/data/live_bucket_queue_runner.py`
- `??` `src/cms/data/model_serving_postgres.py`
- `??` `src/cms/data/model_serving_queries.py`
- `??` `src/cms/data/model_serving_sink.py`
- `??` `src/cms/data/pmax_materialization_plan.py`
- `??` `src/cms/knowledge/`
- `??` `src/cms/modeling/anomaly/`
- `??` `src/cms/modeling/anomaly_artifact_loader.py`
- `??` `src/cms/modeling/anomaly_warning_adapter.py`
- `??` `src/cms/modeling/pmax/`
- `??` `src/cms/service/config.py`
- `??` `src/cms/service/db.py`
- `??` `src/cms/service/equipment_config.json`
- `??` `src/cms/service/errors.py`
- `??` `src/cms/service/report_export.py`
- `??` `src/cms/service/routers/`
- `??` `src/cms/service/scheduler.py`
- `??` `src/cms/workflow/airflow_runtime_policy.py`
- `??` `src/cms/workflow/anomaly_warning_tasks.py`
- `??` `src/cms/workflow/daily_report_airflow.py`
- `??` `src/cms/workflow/langgraph_review.py`
- `??` `src/cms/workflow/model_serving_airflow_skeleton.py`
- `??` `src/cms/workflow/model_serving_pipeline.py`
- `??` `src/cms/workflow/monthly_report_airflow.py`
- `??` `src/cms/workflow/nodes/`
- `??` `src/cms/workflow/report_readiness_airflow.py`
- `??` `src/cms/workflow/router/`
- `??` `src/cms/workflow/state.py`
- `??` `src/cms/workflow/weekly_report_airflow.py`

### tests
- ` M` `tests/contracts/test_pmax_forecast_15min_contract.py`
- ` M` `tests/data/test_live_workers.py`
- ` M` `tests/data/test_runtime_consumer_loop_contract.py`
- ` M` `tests/data/test_runtime_postgres_writer_contract.py`
- ` M` `tests/modeling/test_pmax_feature_builder.py`
- ` M` `tests/verify/test_aws_phase1_runtime_contract.py`
- ` M` `tests/workflow/test_champion_airflow_skeleton.py`
- ` M` `tests/workflow/test_pmax_forecast_tasks.py`
- `??` `tests/data/test_pmax_materialization_plan.py`
- `??` `tests/serving/`
- `??` `tests/test_model_serving_db_contract_fit.py`
- `??` `tests/workflow/test_airflow_dag_wiring.py`
- `??` `tests/workflow/test_airflow_runtime_policy.py`
- `??` `tests/workflow/test_report_readiness_langgraph_adapter.py`

## Cleanup policy

- Keep source/config/specs/tests that belong to approved service-stage gates.
- Separate generated artifacts, frontend bundles, evaluation outputs, and graphify output from source release candidates.
- Do not delete, rename-clean, or overwrite broad legacy/generated files without a separate Viowlet approval gate.
- Before dev overwrite or release, require a narrowed manifest, targeted tests, PC runtime evidence references, and fern PASS on release hygiene.
