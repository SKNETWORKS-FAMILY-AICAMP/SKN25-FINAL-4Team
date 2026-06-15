# Staging Push Manifest - 2026-06-15

## 1. Target remote and branch

```text
remote = https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN25-FINAL-4Team.git
branch = staging
base workspace = current local service tree
```

## 2. Push policy

Service source/config/docs/tests만 push한다. Broad `git add .`는 사용하지 않는다.

### Include

| Scope | Reason |
|---|---|
| `.gitignore`, `README.md`, `requirements.txt`, `docker-compose.yml` | root service metadata and dependency/runtime entrypoints |
| `docker/` | sanitized service compose/container/provisioning templates only |
| `src/cms/` | application/service/data/model/workflow source |
| `scripts/` | service, stream, serving, database, deploy, verify entrypoints |
| `dags/` | Airflow DAG surface |
| `frontend/` | operator UI source |
| `tests/` | contract/runtime regression tests |
| `docs/specs/` | active service/data/runtime contracts and diagrams |
| `docs/qa/` | QA/readiness/observability contracts |
| `docs/release/` | runtime snapshot, push manifest, release hygiene notes |
| `graphify-out/*` deletion | remove previously tracked generated graph output from service branch |

### Exclude

```text
.env and concrete docker/*.env
artifacts/
reports/
evaluation/
.worktrees/
_archive/
knowledge/graphify/
graphify-out/ regenerated files
frontend/dist/
frontend/node_modules/
coverage/cache outputs
model binaries and runtime evidence dumps
```

## 3. Server/local parity summary

```json
{
  "total": 16,
  "exact_match": 12,
  "mismatch": 4,
  "missing_remote": 0
}
```

Exact source parity was checked for representative PC1/PC3 service code and templates. Hash mismatches for AWS/PC2/PC1 compose templates are expected where the repo stores sanitized templates rather than concrete server-local compose/env material.

## 4. Exact changed paths to stage

총 staged path 수: 297


```text
.gitignore
README.md
dags/cms_champion_1h_model_pipeline.py
dags/cms_live_replay.py
dags/daily_report.py
dags/model_serving_pipeline.py
dags/monthly_report.py
dags/weekly_report.py
docker-compose.yml
docker/Dockerfile
docker/Dockerfile.phase1
docker/aws_phase1.env.example
docker/backend_containerfile
docker/compose.aws.db.observability.yml
docker/compose.aws.db.yml
docker/compose.aws.phase1.kafka3.override.yml
docker/compose.aws.phase1.yml
docker/compose.edge_stream.yml
docker/compose.kafka_cluster.yml
docker/compose.local.kafka-broker.yml
docker/compose.model_serving.yml
docker/grafana/provisioning/dashboards/json/cms_runtime_operations.json
docker/grafana/provisioning/dashboards/json/cms_test_gates.json
docker/grafana/provisioning/datasources/prometheus_edge_cluster.yaml
docker/kafka_cluster.env.example
docker/model_serving.env.example
docker/model_serving_containerfile
docker/prometheus/edge_cluster.yml
docker/requirements.model_serving.txt
docker/requirements.phase1.txt
docker/stream.env.example
docs/qa/aws_phase1_smoke_plan.md
docs/qa/grafana_observability_plan.md
docs/qa/grafana_ops_query_contract.md
docs/qa/live_soak_plan.md
docs/qa/pipeline_latency_test_plan.md
docs/qa/qa_contract.md
docs/release/gate18_release_hygiene_inventory.md
docs/release/gate26_release_candidate_narrowing.md
docs/release/gate27_production_live_unblock_packet.md
docs/release/gate28_model_serving_runtime_db_role.md
docs/release/gate29_operational_report_serving_canonical_plan.md
docs/release/live_clocked_cleanup_restart.md
docs/release/runtime_service_snapshot_20260615.md
docs/release/server_local_parity_20260615.md
docs/release/staging_push_manifest_20260615.md
docs/specs/aws_a_to_z_db_contract.md
docs/specs/aws_runtime_topology.md
docs/specs/backend_frontend_api_contract.md
docs/specs/data_platform_contract.md
docs/specs/diagrams/README.md
docs/specs/diagrams/erd_00_live_pipeline_contract.dbml
docs/specs/diagrams/flow_00_overall_pipeline.mmd
docs/specs/diagrams/flow_00_overall_pipeline.svg
docs/specs/diagrams/flow_01_database_pipeline.mmd
docs/specs/diagrams/flow_01_database_pipeline.svg
docs/specs/diagrams/flow_02_data_platform_pipeline.mmd
docs/specs/diagrams/flow_02_data_platform_pipeline.svg
docs/specs/diagrams/flow_03_airflow_pipeline.mmd
docs/specs/diagrams/flow_03_airflow_pipeline.svg
docs/specs/diagrams/flow_04_langgraph_pipeline.mmd
docs/specs/diagrams/flow_04_langgraph_pipeline.svg
docs/specs/diagrams/flow_05_app_pipeline.mmd
docs/specs/diagrams/flow_05_app_pipeline.svg
docs/specs/diagrams/sequence_00_overall_pipeline.mmd
docs/specs/diagrams/sequence_00_overall_pipeline.svg
docs/specs/diagrams/sequence_01_database_pipeline.mmd
docs/specs/diagrams/sequence_01_database_pipeline.svg
docs/specs/diagrams/sequence_02_data_platform_pipeline.mmd
docs/specs/diagrams/sequence_02_data_platform_pipeline.svg
docs/specs/diagrams/sequence_03_airflow_pipeline.mmd
docs/specs/diagrams/sequence_03_airflow_pipeline.svg
docs/specs/diagrams/sequence_04_langgraph_pipeline.mmd
docs/specs/diagrams/sequence_04_langgraph_pipeline.svg
docs/specs/diagrams/sequence_05_app_pipeline.mmd
docs/specs/diagrams/sequence_05_app_pipeline.svg
docs/specs/diagrams/stack_architecture_overview.svg
docs/specs/kafka_ingestion_implementation_plan.md
docs/specs/project_overview.md
docs/specs/runtime_architecture.md
frontend/.dockerignore
frontend/.gitignore
frontend/Dockerfile
frontend/README.md
frontend/containerfile
frontend/eslint.config.js
frontend/nginx.conf
frontend/package-lock.json
frontend/package.json
frontend/public/favicon.svg
frontend/public/fig1_meter_hierarchy.png
frontend/public/fig2_hvac_meters.png
frontend/public/icons.svg
frontend/src/api/client.js
frontend/src/app.jsx
frontend/src/assets/hero.png
frontend/src/components/common/equipment_icon.jsx
frontend/src/components/common/simulator_clock.jsx
frontend/src/components/login_screen.jsx
frontend/src/components/panels/anomaly_chart_panel.jsx
frontend/src/components/panels/anomaly_panel.jsx
frontend/src/components/panels/billing_panel.jsx
frontend/src/components/panels/chat_history_panel.jsx
frontend/src/components/panels/chat_panel.jsx
frontend/src/components/panels/chat_workspace_panel.jsx
frontend/src/components/panels/control_panel.jsx
frontend/src/components/panels/daily_report_panel.jsx
frontend/src/components/panels/dashboard_panel.jsx
frontend/src/components/panels/equipment_panel.jsx
frontend/src/components/panels/forecast_panel.jsx
frontend/src/components/panels/maintenance_panel.jsx
frontend/src/components/panels/report_panel.jsx
frontend/src/components/panels/settings_panel.jsx
frontend/src/components/panels/topology_panel.jsx
frontend/src/components/panels/users_panel.jsx
frontend/src/index.css
frontend/src/main.jsx
frontend/src/theme.js
frontend/vite.config.js
graphify-out/graph.json
graphify-out/graph_tree.html
graphify-out/manifest.json
requirements.txt
scripts/database/apply_schema.py
scripts/database/check_schema_drift.py
scripts/database/migrations/active_peak_view.sql
scripts/database/migrations/bucket_worker_grants.sql
scripts/database/migrations/gate28_model_serving_runtime_roles.sql
scripts/database/migrations/least_privilege_runtime_roles.sql
scripts/database/migrations/live_event_trigger.sql
scripts/database/migrations/model_serving_runtime_access.sql
scripts/database/migrations/model_serving_tables.sql
scripts/database/migrations/pmax_policy_2023.sql
scripts/database/schema_inventory.py
scripts/database/verify/gate28_model_serving_runtime_privilege_check.sql
scripts/deploy/deploy_pc1_phase1_runtime.sh
scripts/live/build_clocked_day_cache.py
scripts/live/run_consumer_service.py
scripts/live/run_live_stream_injector.py
scripts/serving/materialize_anomaly_features.py
scripts/serving/plan_pmax_materialization.py
scripts/serving/run_model_serving.py
scripts/serving/run_operational_scheduler.py
scripts/serving/validate_artifacts.py
scripts/stream/bucket_queue_worker.py
scripts/stream/canonical_promotion_worker.py
scripts/stream/consumer_service.py
scripts/stream/ingest_replay.py
scripts/verify/build_release_manifest.py
src/cms/contracts/agent.py
src/cms/contracts/anomaly_detection_1h.py
src/cms/contracts/core.py
src/cms/contracts/evaluation.py
src/cms/contracts/ingestion.py
src/cms/contracts/live_pipeline.py
src/cms/contracts/pmax_forecast_15min.py
src/cms/contracts/retrieval.py
src/cms/data/anomaly_feature_materializer.py
src/cms/data/canonical_promotion_runner.py
src/cms/data/live_bucket_queue_runner.py
src/cms/data/live_scratch_adapter.py
src/cms/data/live_workers.py
src/cms/data/model_serving_postgres.py
src/cms/data/model_serving_queries.py
src/cms/data/model_serving_sink.py
src/cms/data/pmax_materialization_plan.py
src/cms/data/postgres_event_writer.py
src/cms/data/runtime_consumer_loop.py
src/cms/data/runtime_kafka.py
src/cms/data/runtime_postgres.py
src/cms/data/stream_consumer.py
src/cms/data/stream_consumer_runner.py
src/cms/knowledge/domain_knowledge.py
src/cms/knowledge/embedding.py
src/cms/knowledge/meter_metadata.json
src/cms/knowledge/rerank/__init__.py
src/cms/knowledge/rerank/backends.py
src/cms/knowledge/rerank/interfaces.py
src/cms/knowledge/rerank/reranker.py
src/cms/knowledge/retrieval/__init__.py
src/cms/knowledge/retrieval/backends.py
src/cms/knowledge/retrieval/interfaces.py
src/cms/knowledge/retrieval/keywords.py
src/cms/knowledge/retrieval/pipeline.py
src/cms/knowledge/retrieval/retriever.py
src/cms/knowledge/retrieval/router.py
src/cms/modeling/anomaly/__init__.py
src/cms/modeling/anomaly/artifact_io.py
src/cms/modeling/anomaly/artifacts.py
src/cms/modeling/anomaly/bias.py
src/cms/modeling/anomaly/catboost_model.py
src/cms/modeling/anomaly/catboost_runtime.py
src/cms/modeling/anomaly/config.py
src/cms/modeling/anomaly/data.py
src/cms/modeling/anomaly/db.py
src/cms/modeling/anomaly/ensemble.py
src/cms/modeling/anomaly/features.py
src/cms/modeling/anomaly/lightgbm_model.py
src/cms/modeling/anomaly/lightgbm_runtime.py
src/cms/modeling/anomaly/lstm.py
src/cms/modeling/anomaly/mapping.py
src/cms/modeling/anomaly/model.py
src/cms/modeling/anomaly/naive.py
src/cms/modeling/anomaly/plots.py
src/cms/modeling/anomaly/predictor.py
src/cms/modeling/anomaly/preprocessing.py
src/cms/modeling/anomaly/readme.md
src/cms/modeling/anomaly/recurrent.py
src/cms/modeling/anomaly/resources/meter_tags.csv
src/cms/modeling/anomaly/ridge.py
src/cms/modeling/anomaly/ridge_runtime.py
src/cms/modeling/anomaly/router.py
src/cms/modeling/anomaly/selectors.py
src/cms/modeling/anomaly_artifact_loader.py
src/cms/modeling/anomaly_warning_adapter.py
src/cms/modeling/pmax/__init__.py
src/cms/modeling/pmax/batch_inference.py
src/cms/modeling/pmax/csv_store.py
src/cms/modeling/pmax/inference.py
src/cms/modeling/pmax/operations.py
src/cms/modeling/pmax/promotion.py
src/cms/modeling/pmax/training.py
src/cms/modeling/pmax/validation.py
src/cms/modeling/pmax_artifact_loader.py
src/cms/modeling/pmax_feature_builder.py
src/cms/modeling/pmax_forecast_adapter.py
src/cms/service/api.py
src/cms/service/config.py
src/cms/service/db.py
src/cms/service/equipment_config.json
src/cms/service/errors.py
src/cms/service/report_export.py
src/cms/service/routers/__init__.py
src/cms/service/routers/anomalies.py
src/cms/service/routers/auth.py
src/cms/service/routers/chat.py
src/cms/service/routers/cms.py
src/cms/service/routers/control.py
src/cms/service/routers/forecast.py
src/cms/service/routers/notifications.py
src/cms/service/routers/report.py
src/cms/service/routers/settings.py
src/cms/service/routers/simulator.py
src/cms/service/routers/users.py
src/cms/service/scheduler.py
src/cms/workflow/airflow_runtime_policy.py
src/cms/workflow/airflow_skeleton.py
src/cms/workflow/anomaly_warning_tasks.py
src/cms/workflow/champion_airflow_skeleton.py
src/cms/workflow/daily_report_airflow.py
src/cms/workflow/langgraph_review.py
src/cms/workflow/langgraph_skeleton.py
src/cms/workflow/model_serving_airflow_skeleton.py
src/cms/workflow/model_serving_pipeline.py
src/cms/workflow/monthly_report_airflow.py
src/cms/workflow/nodes/__init__.py
src/cms/workflow/nodes/review_nodes.py
src/cms/workflow/pmax_forecast_tasks.py
src/cms/workflow/replay_clock.py
src/cms/workflow/report_freshness.py
src/cms/workflow/report_readiness_airflow.py
src/cms/workflow/review_jobs.py
src/cms/workflow/router/__init__.py
src/cms/workflow/router/anomaly_agent.py
src/cms/workflow/router/cms_agent.py
src/cms/workflow/router/forecast_agent.py
src/cms/workflow/router/graph.py
src/cms/workflow/router/llm_client.py
src/cms/workflow/router/rag_agent.py
src/cms/workflow/router/reporting_agent.py
src/cms/workflow/router/state.py
src/cms/workflow/state.py
src/cms/workflow/weekly_report_airflow.py
tests/contracts/test_pmax_forecast_15min_contract.py
tests/data/test_anomaly_feature_materializer.py
tests/data/test_canonical_promotion_runner.py
tests/data/test_live_bucket_queue_runner.py
tests/data/test_live_workers.py
tests/data/test_pmax_materialization_plan.py
tests/data/test_runtime_consumer_loop_contract.py
tests/data/test_runtime_postgres_writer_contract.py
tests/live/test_live_stream_injector.py
tests/modeling/test_pmax_feature_builder.py
tests/modeling/test_pmax_forecast_adapter.py
tests/service/test_fastapi_app.py
tests/serving/test_run_model_serving_cli_guards.py
tests/test_model_serving_db_contract_fit.py
tests/verify/test_aws_phase1_runtime_contract.py
tests/verify/test_gate28_model_serving_runtime_role_plan.py
tests/workflow/test_airflow_dag_wiring.py
tests/workflow/test_airflow_runtime_policy.py
tests/workflow/test_champion_airflow_skeleton.py
tests/workflow/test_langgraph_review.py
tests/workflow/test_pmax_forecast_tasks.py
tests/workflow/test_replay_clock.py
tests/workflow/test_report_freshness.py
tests/workflow/test_report_readiness_langgraph_adapter.py
```
