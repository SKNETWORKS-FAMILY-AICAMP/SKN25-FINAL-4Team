# Service E2E push file checklist - 2026-06-15

## Policy

- Keep only service E2E source, runtime config, service docs, diagrams, Airflow DAGs, frontend source, and operation scripts.
- Exclude tests, reports, evaluation outputs, generated artifacts, local env files, caches, and historical gate notes.
- Project-owned pushed paths must be snake_case. Tool-required ecosystem filenames are allowed only where the tool contract requires the exact name.

## Counts

- Push file rows checked: 342
- Tests ignored: staged deletions remove `tests/` from the index; local ignored copies remain available for verification.
- Server-local parity rows checked: 24

## Excluded local-only paths

| Path | Reason |
|---|---|
| `tests/` | user requested test files/folders ignored |
| `reports/` | generated/stale evidence reports; reviewed but not pushed |
| `reports/pipeline_comleteness_*` | requested misspelled glob checked: absent |
| `reports/pipeline_completeness_assessment_20260615.md` | reviewed as stale local report; ignored |
| `reports/server_local_consistency_20260615.md` | reviewed as stale local report; ignored |
| `evaluation/` | local evaluation workspace |
| `reports/retired_docs/` | preserved historical release gate docs, ignored |
| `frontend/dist/` | generated frontend build |
| `artifacts/` | runtime/model artifacts |
| `graphify-out/` | generated graph output |

## File-by-file push checklist

| # | Path | Class | Decision |
|---:|---|---|---|
| 1 | `.gitattributes` | project config | keep |
| 2 | `.gitignore` | project config | keep |
| 3 | `dags/cms_champion_1h_model_pipeline.py` | airflow service dag | keep |
| 4 | `dags/cms_live_replay.py` | airflow service dag | keep |
| 5 | `dags/daily_report.py` | airflow service dag | keep |
| 6 | `dags/model_serving_pipeline.py` | airflow service dag | keep |
| 7 | `dags/monthly_report.py` | airflow service dag | keep |
| 8 | `dags/weekly_report.py` | airflow service dag | keep |
| 9 | `docker/aws_phase1.env.example` | service runtime config | keep |
| 10 | `docker/backend_containerfile` | service runtime config | keep |
| 11 | `docker/compose_aws_db.yml` | service runtime config | keep |
| 12 | `docker/compose_aws_db_observability.yml` | service runtime config | keep |
| 13 | `docker/compose_aws_phase1.yml` | service runtime config | keep |
| 14 | `docker/compose_aws_phase1_kafka3_override.yml` | service runtime config | keep |
| 15 | `docker/compose_edge_stream.yml` | service runtime config | keep |
| 16 | `docker/compose_kafka_cluster.yml` | service runtime config | keep |
| 17 | `docker/compose_local_kafka_broker.yml` | service runtime config | keep |
| 18 | `docker/compose_model_serving.yml` | service runtime config | keep |
| 19 | `docker/dockerfile_phase1` | service runtime config | keep |
| 20 | `docker/grafana/provisioning/alerting/contact_points.yaml` | service runtime config | keep |
| 21 | `docker/grafana/provisioning/alerting/live_pipeline_alerts.yaml` | service runtime config | keep |
| 22 | `docker/grafana/provisioning/dashboards/archive/cms_live_pipeline_overview.json` | service runtime config | keep |
| 23 | `docker/grafana/provisioning/dashboards/archive/cms_live_soak_gates.json` | service runtime config | keep |
| 24 | `docker/grafana/provisioning/dashboards/archive/cms_phase1_status.json` | service runtime config | keep |
| 25 | `docker/grafana/provisioning/dashboards/archive/cms_phase1b_kafka_exporter.json` | service runtime config | keep |
| 26 | `docker/grafana/provisioning/dashboards/archive/cms_phase1c_system_postgres.json` | service runtime config | keep |
| 27 | `docker/grafana/provisioning/dashboards/archive/cms_pmax_forecast.json` | service runtime config | keep |
| 28 | `docker/grafana/provisioning/dashboards/cms_live_dashboards.yaml` | service runtime config | keep |
| 29 | `docker/grafana/provisioning/dashboards/json/cms_runtime_operations.json` | service runtime config | keep |
| 30 | `docker/grafana/provisioning/dashboards/json/cms_test_gates.json` | service runtime config | keep |
| 31 | `docker/grafana/provisioning/datasources/postgres_cms_live.yaml` | service runtime config | keep |
| 32 | `docker/grafana/provisioning/datasources/prometheus_cms_stream.yaml` | service runtime config | keep |
| 33 | `docker/grafana/provisioning/datasources/prometheus_edge_cluster.yaml` | service runtime config | keep |
| 34 | `docker/kafka_cluster.env.example` | service runtime config | keep |
| 35 | `docker/model_serving.env.example` | service runtime config | keep |
| 36 | `docker/model_serving_containerfile` | service runtime config | keep |
| 37 | `docker/prometheus/edge_cluster.yml` | service runtime config | keep |
| 38 | `docker/prometheus/phase1.yml` | service runtime config | keep |
| 39 | `docker/requirements.model_serving.txt` | service runtime config | keep |
| 40 | `docker/requirements.phase1.txt` | service runtime config | keep |
| 41 | `docker/stream.env.example` | service runtime config | keep |
| 42 | `docker_compose.yml` | service runtime config | keep |
| 43 | `docs/ontology/cms.ttl` | service documentation | keep |
| 44 | `docs/ontology/cms_protege.owl` | service documentation | keep |
| 45 | `docs/ontology/cms_shapes.ttl` | service documentation | keep |
| 46 | `docs/qa/aws_phase1_smoke_plan.md` | service documentation | keep |
| 47 | `docs/qa/grafana_observability_plan.md` | service documentation | keep |
| 48 | `docs/qa/grafana_ops_query_contract.md` | service documentation | keep |
| 49 | `docs/qa/live_soak_plan.md` | service documentation | keep |
| 50 | `docs/qa/pipeline_latency_test_plan.md` | service documentation | keep |
| 51 | `docs/qa/qa_contract.md` | service documentation | keep |
| 52 | `docs/reference/measurement_glossary.md` | service documentation | keep |
| 53 | `docs/reference/source_inventory.md` | service documentation | keep |
| 54 | `docs/release/runtime_service_snapshot_20260615.md` | service documentation | keep |
| 55 | `docs/release/server_local_parity_20260615.md` | service documentation | keep |
| 56 | `docs/release/service_e2e_file_checklist_20260615.md` | service documentation | keep |
| 57 | `docs/release/staging_push_manifest_20260615.md` | service documentation | keep |
| 58 | `docs/specs/aws_a_to_z_db_contract.md` | service documentation | keep |
| 59 | `docs/specs/aws_runtime_topology.md` | service documentation | keep |
| 60 | `docs/specs/backend_frontend_api_contract.md` | service documentation | keep |
| 61 | `docs/specs/data_platform_contract.md` | service documentation | keep |
| 62 | `docs/specs/diagrams/erd_00_live_pipeline_contract.dbml` | service documentation | keep |
| 63 | `docs/specs/diagrams/flow_00_overall_pipeline.mmd` | service documentation | keep |
| 64 | `docs/specs/diagrams/flow_00_overall_pipeline.svg` | service documentation | keep |
| 65 | `docs/specs/diagrams/flow_01_database_pipeline.mmd` | service documentation | keep |
| 66 | `docs/specs/diagrams/flow_01_database_pipeline.svg` | service documentation | keep |
| 67 | `docs/specs/diagrams/flow_02_data_platform_pipeline.mmd` | service documentation | keep |
| 68 | `docs/specs/diagrams/flow_02_data_platform_pipeline.svg` | service documentation | keep |
| 69 | `docs/specs/diagrams/flow_03_airflow_pipeline.mmd` | service documentation | keep |
| 70 | `docs/specs/diagrams/flow_03_airflow_pipeline.svg` | service documentation | keep |
| 71 | `docs/specs/diagrams/flow_04_langgraph_pipeline.mmd` | service documentation | keep |
| 72 | `docs/specs/diagrams/flow_04_langgraph_pipeline.svg` | service documentation | keep |
| 73 | `docs/specs/diagrams/flow_05_app_pipeline.mmd` | service documentation | keep |
| 74 | `docs/specs/diagrams/flow_05_app_pipeline.svg` | service documentation | keep |
| 75 | `docs/specs/diagrams/mermaid_render_config.json` | service documentation | keep |
| 76 | `docs/specs/diagrams/readme.md` | service documentation | keep |
| 77 | `docs/specs/diagrams/sequence_00_overall_pipeline.mmd` | service documentation | keep |
| 78 | `docs/specs/diagrams/sequence_00_overall_pipeline.svg` | service documentation | keep |
| 79 | `docs/specs/diagrams/sequence_01_database_pipeline.mmd` | service documentation | keep |
| 80 | `docs/specs/diagrams/sequence_01_database_pipeline.svg` | service documentation | keep |
| 81 | `docs/specs/diagrams/sequence_02_data_platform_pipeline.mmd` | service documentation | keep |
| 82 | `docs/specs/diagrams/sequence_02_data_platform_pipeline.svg` | service documentation | keep |
| 83 | `docs/specs/diagrams/sequence_03_airflow_pipeline.mmd` | service documentation | keep |
| 84 | `docs/specs/diagrams/sequence_03_airflow_pipeline.svg` | service documentation | keep |
| 85 | `docs/specs/diagrams/sequence_04_langgraph_pipeline.mmd` | service documentation | keep |
| 86 | `docs/specs/diagrams/sequence_04_langgraph_pipeline.svg` | service documentation | keep |
| 87 | `docs/specs/diagrams/sequence_05_app_pipeline.mmd` | service documentation | keep |
| 88 | `docs/specs/diagrams/sequence_05_app_pipeline.svg` | service documentation | keep |
| 89 | `docs/specs/diagrams/stack_architecture_overview.svg` | service documentation | keep |
| 90 | `docs/specs/kafka_ingestion_implementation_plan.md` | service documentation | keep |
| 91 | `docs/specs/knowledge_db_contract.md` | service documentation | keep |
| 92 | `docs/specs/live_schema_migration_plan.md` | service documentation | keep |
| 93 | `docs/specs/llm_contract.md` | service documentation | keep |
| 94 | `docs/specs/measurement_processing_policy.md` | service documentation | keep |
| 95 | `docs/specs/meter_metadata.md` | service documentation | keep |
| 96 | `docs/specs/ontology_schema.md` | service documentation | keep |
| 97 | `docs/specs/project_overview.md` | service documentation | keep |
| 98 | `docs/specs/runtime_architecture.md` | service documentation | keep |
| 99 | `frontend/.dockerignore` | frontend service source | keep |
| 100 | `frontend/.gitignore` | frontend service source | keep |
| 101 | `frontend/containerfile` | frontend service source | keep |
| 102 | `frontend/dockerfile` | frontend service source | keep |
| 103 | `frontend/eslint.config.js` | frontend service source | keep |
| 104 | `frontend/nginx.conf` | frontend service source | keep |
| 105 | `frontend/package-lock.json` | frontend service source | keep |
| 106 | `frontend/package.json` | frontend service source | keep |
| 107 | `frontend/public/favicon.svg` | frontend service source | keep |
| 108 | `frontend/public/fig1_meter_hierarchy.png` | frontend service source | keep |
| 109 | `frontend/public/fig2_hvac_meters.png` | frontend service source | keep |
| 110 | `frontend/public/icons.svg` | frontend service source | keep |
| 111 | `frontend/readme.md` | frontend service source | keep |
| 112 | `frontend/src/api/client.js` | frontend service source | keep |
| 113 | `frontend/src/app.jsx` | frontend service source | keep |
| 114 | `frontend/src/assets/hero.png` | frontend service source | keep |
| 115 | `frontend/src/components/common/equipment_icon.jsx` | frontend service source | keep |
| 116 | `frontend/src/components/common/simulator_clock.jsx` | frontend service source | keep |
| 117 | `frontend/src/components/login_screen.jsx` | frontend service source | keep |
| 118 | `frontend/src/components/panels/anomaly_chart_panel.jsx` | frontend service source | keep |
| 119 | `frontend/src/components/panels/anomaly_panel.jsx` | frontend service source | keep |
| 120 | `frontend/src/components/panels/billing_panel.jsx` | frontend service source | keep |
| 121 | `frontend/src/components/panels/chat_history_panel.jsx` | frontend service source | keep |
| 122 | `frontend/src/components/panels/chat_panel.jsx` | frontend service source | keep |
| 123 | `frontend/src/components/panels/chat_workspace_panel.jsx` | frontend service source | keep |
| 124 | `frontend/src/components/panels/control_panel.jsx` | frontend service source | keep |
| 125 | `frontend/src/components/panels/daily_report_panel.jsx` | frontend service source | keep |
| 126 | `frontend/src/components/panels/dashboard_panel.jsx` | frontend service source | keep |
| 127 | `frontend/src/components/panels/equipment_panel.jsx` | frontend service source | keep |
| 128 | `frontend/src/components/panels/forecast_panel.jsx` | frontend service source | keep |
| 129 | `frontend/src/components/panels/maintenance_panel.jsx` | frontend service source | keep |
| 130 | `frontend/src/components/panels/report_panel.jsx` | frontend service source | keep |
| 131 | `frontend/src/components/panels/settings_panel.jsx` | frontend service source | keep |
| 132 | `frontend/src/components/panels/topology_panel.jsx` | frontend service source | keep |
| 133 | `frontend/src/components/panels/users_panel.jsx` | frontend service source | keep |
| 134 | `frontend/src/index.css` | frontend service source | keep |
| 135 | `frontend/src/main.jsx` | frontend service source | keep |
| 136 | `frontend/src/theme.js` | frontend service source | keep |
| 137 | `frontend/vite.config.js` | frontend service source | keep |
| 138 | `pyproject.toml` | project config | keep |
| 139 | `readme.md` | project config | keep |
| 140 | `requirements.txt` | project config | keep |
| 141 | `scripts/database/apply_schema.py` | service operations script | keep |
| 142 | `scripts/database/check_schema_drift.py` | service operations script | keep |
| 143 | `scripts/database/migrations/active_peak_view.sql` | service operations script | keep |
| 144 | `scripts/database/migrations/bucket_worker_grants.sql` | service operations script | keep |
| 145 | `scripts/database/migrations/gate28_model_serving_runtime_roles.sql` | service operations script | keep |
| 146 | `scripts/database/migrations/least_privilege_runtime_roles.sql` | service operations script | keep |
| 147 | `scripts/database/migrations/live_event_trigger.sql` | service operations script | keep |
| 148 | `scripts/database/migrations/model_serving_runtime_access.sql` | service operations script | keep |
| 149 | `scripts/database/migrations/model_serving_tables.sql` | service operations script | keep |
| 150 | `scripts/database/migrations/pmax_policy_2023.sql` | service operations script | keep |
| 151 | `scripts/database/schema_inventory.py` | service operations script | keep |
| 152 | `scripts/database/verify/gate28_model_serving_runtime_privilege_check.sql` | service operations script | keep |
| 153 | `scripts/deploy/deploy_pc1_phase1_runtime.sh` | service operations script | keep |
| 154 | `scripts/live/build_clocked_day_cache.py` | service operations script | keep |
| 155 | `scripts/live/dry_run_live_equalization.py` | service operations script | keep |
| 156 | `scripts/live/dry_run_live_stream.py` | service operations script | keep |
| 157 | `scripts/live/run_consumer_service.py` | service operations script | keep |
| 158 | `scripts/live/run_live_qa_latency_smoke.py` | service operations script | keep |
| 159 | `scripts/live/run_live_stream_injector.py` | service operations script | keep |
| 160 | `scripts/migrations/build_peak_feature_15min.py` | service operations script | keep |
| 161 | `scripts/migrations/generate_reference_migration.py` | service operations script | keep |
| 162 | `scripts/migrations/live_schema_draft.sql` | service operations script | keep |
| 163 | `scripts/migrations/ops_metric_draft.sql` | service operations script | keep |
| 164 | `scripts/ontology/generate_ontology.py` | service operations script | keep |
| 165 | `scripts/ontology/load_metadata_from_db.py` | service operations script | keep |
| 166 | `scripts/ontology/query_ontology.py` | service operations script | keep |
| 167 | `scripts/ontology/validate_ontology.py` | service operations script | keep |
| 168 | `scripts/scratch/local_scratch_db_integration.py` | service operations script | keep |
| 169 | `scripts/scratch/run_scratch_db_integration.py` | service operations script | keep |
| 170 | `scripts/serving/materialize_anomaly_features.py` | service operations script | keep |
| 171 | `scripts/serving/plan_pmax_materialization.py` | service operations script | keep |
| 172 | `scripts/serving/run_model_serving.py` | service operations script | keep |
| 173 | `scripts/serving/run_operational_scheduler.py` | service operations script | keep |
| 174 | `scripts/serving/validate_artifacts.py` | service operations script | keep |
| 175 | `scripts/stream/bucket_queue_worker.py` | service operations script | keep |
| 176 | `scripts/stream/canonical_promotion_worker.py` | service operations script | keep |
| 177 | `scripts/stream/consumer_service.py` | service operations script | keep |
| 178 | `scripts/stream/ingest_replay.py` | service operations script | keep |
| 179 | `scripts/verify/add_modeling_docx_diagrams.py` | service operations script | keep |
| 180 | `scripts/verify/apply_svg_text_backgrounds.py` | service operations script | keep |
| 181 | `scripts/verify/build_release_manifest.py` | service operations script | keep |
| 182 | `scripts/verify/create_mentoring_round_05_docx.py` | service operations script | keep |
| 183 | `scripts/verify/create_modeling_docx.py` | service operations script | keep |
| 184 | `scripts/verify/query_plan_eval_support.py` | service operations script | keep |
| 185 | `scripts/verify/render_diagrams.py` | service operations script | keep |
| 186 | `scripts/verify/run_query_plan_eval.py` | service operations script | keep |
| 187 | `scripts/verify/verify_migration_contracts.py` | service operations script | keep |
| 188 | `scripts/verify/verify_skeleton_contracts.py` | service operations script | keep |
| 189 | `src/cms/__init__.py` | service source | keep |
| 190 | `src/cms/contracts/__init__.py` | service source | keep |
| 191 | `src/cms/contracts/agent.py` | service source | keep |
| 192 | `src/cms/contracts/anomaly_detection_1h.py` | service source | keep |
| 193 | `src/cms/contracts/core.py` | service source | keep |
| 194 | `src/cms/contracts/evaluation.py` | service source | keep |
| 195 | `src/cms/contracts/ingestion.py` | service source | keep |
| 196 | `src/cms/contracts/job.py` | service source | keep |
| 197 | `src/cms/contracts/live_pipeline.py` | service source | keep |
| 198 | `src/cms/contracts/measurement.py` | service source | keep |
| 199 | `src/cms/contracts/model_input_1h.py` | service source | keep |
| 200 | `src/cms/contracts/observability.py` | service source | keep |
| 201 | `src/cms/contracts/pmax_forecast_15min.py` | service source | keep |
| 202 | `src/cms/contracts/qa.py` | service source | keep |
| 203 | `src/cms/contracts/retrieval.py` | service source | keep |
| 204 | `src/cms/contracts/timestamp_policy.py` | service source | keep |
| 205 | `src/cms/data/__init__.py` | service source | keep |
| 206 | `src/cms/data/anomaly_feature_materializer.py` | service source | keep |
| 207 | `src/cms/data/canonical_promotion_runner.py` | service source | keep |
| 208 | `src/cms/data/db_scratch_guard.py` | service source | keep |
| 209 | `src/cms/data/kafka_adapter.py` | service source | keep |
| 210 | `src/cms/data/live_bucket_queue_runner.py` | service source | keep |
| 211 | `src/cms/data/live_equalization_plan.py` | service source | keep |
| 212 | `src/cms/data/live_equalization_processor.py` | service source | keep |
| 213 | `src/cms/data/live_replay.py` | service source | keep |
| 214 | `src/cms/data/live_scratch_adapter.py` | service source | keep |
| 215 | `src/cms/data/live_workers.py` | service source | keep |
| 216 | `src/cms/data/model_serving_postgres.py` | service source | keep |
| 217 | `src/cms/data/model_serving_queries.py` | service source | keep |
| 218 | `src/cms/data/model_serving_sink.py` | service source | keep |
| 219 | `src/cms/data/peak_features.py` | service source | keep |
| 220 | `src/cms/data/pmax_materialization_plan.py` | service source | keep |
| 221 | `src/cms/data/postgres_event_writer.py` | service source | keep |
| 222 | `src/cms/data/raw_event_builder.py` | service source | keep |
| 223 | `src/cms/data/rebalance_idempotency_gate.py` | service source | keep |
| 224 | `src/cms/data/runtime_consumer_loop.py` | service source | keep |
| 225 | `src/cms/data/runtime_kafka.py` | service source | keep |
| 226 | `src/cms/data/runtime_postgres.py` | service source | keep |
| 227 | `src/cms/data/scratch_db_adapter.py` | service source | keep |
| 228 | `src/cms/data/scratch_ddl.py` | service source | keep |
| 229 | `src/cms/data/stream_consumer.py` | service source | keep |
| 230 | `src/cms/data/stream_consumer_runner.py` | service source | keep |
| 231 | `src/cms/data/timestamp_normalizer.py` | service source | keep |
| 232 | `src/cms/data/timestamp_policy_registry.py` | service source | keep |
| 233 | `src/cms/data/timestamp_qa.py` | service source | keep |
| 234 | `src/cms/knowledge/domain_knowledge.py` | service source | keep |
| 235 | `src/cms/knowledge/embedding.py` | service source | keep |
| 236 | `src/cms/knowledge/meter_metadata.json` | service source | keep |
| 237 | `src/cms/knowledge/rerank/__init__.py` | service source | keep |
| 238 | `src/cms/knowledge/rerank/backends.py` | service source | keep |
| 239 | `src/cms/knowledge/rerank/interfaces.py` | service source | keep |
| 240 | `src/cms/knowledge/rerank/reranker.py` | service source | keep |
| 241 | `src/cms/knowledge/retrieval/__init__.py` | service source | keep |
| 242 | `src/cms/knowledge/retrieval/backends.py` | service source | keep |
| 243 | `src/cms/knowledge/retrieval/interfaces.py` | service source | keep |
| 244 | `src/cms/knowledge/retrieval/keywords.py` | service source | keep |
| 245 | `src/cms/knowledge/retrieval/pipeline.py` | service source | keep |
| 246 | `src/cms/knowledge/retrieval/retriever.py` | service source | keep |
| 247 | `src/cms/knowledge/retrieval/router.py` | service source | keep |
| 248 | `src/cms/modeling/__init__.py` | service source | keep |
| 249 | `src/cms/modeling/anomaly/__init__.py` | service source | keep |
| 250 | `src/cms/modeling/anomaly/artifact_io.py` | service source | keep |
| 251 | `src/cms/modeling/anomaly/artifacts.py` | service source | keep |
| 252 | `src/cms/modeling/anomaly/bias.py` | service source | keep |
| 253 | `src/cms/modeling/anomaly/catboost_model.py` | service source | keep |
| 254 | `src/cms/modeling/anomaly/catboost_runtime.py` | service source | keep |
| 255 | `src/cms/modeling/anomaly/config.py` | service source | keep |
| 256 | `src/cms/modeling/anomaly/data.py` | service source | keep |
| 257 | `src/cms/modeling/anomaly/db.py` | service source | keep |
| 258 | `src/cms/modeling/anomaly/ensemble.py` | service source | keep |
| 259 | `src/cms/modeling/anomaly/features.py` | service source | keep |
| 260 | `src/cms/modeling/anomaly/lightgbm_model.py` | service source | keep |
| 261 | `src/cms/modeling/anomaly/lightgbm_runtime.py` | service source | keep |
| 262 | `src/cms/modeling/anomaly/lstm.py` | service source | keep |
| 263 | `src/cms/modeling/anomaly/mapping.py` | service source | keep |
| 264 | `src/cms/modeling/anomaly/model.py` | service source | keep |
| 265 | `src/cms/modeling/anomaly/naive.py` | service source | keep |
| 266 | `src/cms/modeling/anomaly/plots.py` | service source | keep |
| 267 | `src/cms/modeling/anomaly/predictor.py` | service source | keep |
| 268 | `src/cms/modeling/anomaly/preprocessing.py` | service source | keep |
| 269 | `src/cms/modeling/anomaly/readme.md` | service source | keep |
| 270 | `src/cms/modeling/anomaly/recurrent.py` | service source | keep |
| 271 | `src/cms/modeling/anomaly/resources/meter_tags.csv` | service source | keep |
| 272 | `src/cms/modeling/anomaly/ridge.py` | service source | keep |
| 273 | `src/cms/modeling/anomaly/ridge_runtime.py` | service source | keep |
| 274 | `src/cms/modeling/anomaly/router.py` | service source | keep |
| 275 | `src/cms/modeling/anomaly/selectors.py` | service source | keep |
| 276 | `src/cms/modeling/anomaly_artifact_loader.py` | service source | keep |
| 277 | `src/cms/modeling/anomaly_warning_adapter.py` | service source | keep |
| 278 | `src/cms/modeling/fake_champion_adapter.py` | service source | keep |
| 279 | `src/cms/modeling/pmax/__init__.py` | service source | keep |
| 280 | `src/cms/modeling/pmax/batch_inference.py` | service source | keep |
| 281 | `src/cms/modeling/pmax/csv_store.py` | service source | keep |
| 282 | `src/cms/modeling/pmax/inference.py` | service source | keep |
| 283 | `src/cms/modeling/pmax/operations.py` | service source | keep |
| 284 | `src/cms/modeling/pmax/promotion.py` | service source | keep |
| 285 | `src/cms/modeling/pmax/training.py` | service source | keep |
| 286 | `src/cms/modeling/pmax/validation.py` | service source | keep |
| 287 | `src/cms/modeling/pmax_artifact_loader.py` | service source | keep |
| 288 | `src/cms/modeling/pmax_feature_builder.py` | service source | keep |
| 289 | `src/cms/modeling/pmax_forecast_adapter.py` | service source | keep |
| 290 | `src/cms/ontology/__init__.py` | service source | keep |
| 291 | `src/cms/ontology/ontology.py` | service source | keep |
| 292 | `src/cms/service/__init__.py` | service source | keep |
| 293 | `src/cms/service/api.py` | service source | keep |
| 294 | `src/cms/service/config.py` | service source | keep |
| 295 | `src/cms/service/db.py` | service source | keep |
| 296 | `src/cms/service/equipment_config.json` | service source | keep |
| 297 | `src/cms/service/errors.py` | service source | keep |
| 298 | `src/cms/service/query_planner.py` | service source | keep |
| 299 | `src/cms/service/report_export.py` | service source | keep |
| 300 | `src/cms/service/routers/__init__.py` | service source | keep |
| 301 | `src/cms/service/routers/anomalies.py` | service source | keep |
| 302 | `src/cms/service/routers/auth.py` | service source | keep |
| 303 | `src/cms/service/routers/chat.py` | service source | keep |
| 304 | `src/cms/service/routers/cms.py` | service source | keep |
| 305 | `src/cms/service/routers/control.py` | service source | keep |
| 306 | `src/cms/service/routers/forecast.py` | service source | keep |
| 307 | `src/cms/service/routers/notifications.py` | service source | keep |
| 308 | `src/cms/service/routers/report.py` | service source | keep |
| 309 | `src/cms/service/routers/settings.py` | service source | keep |
| 310 | `src/cms/service/routers/simulator.py` | service source | keep |
| 311 | `src/cms/service/routers/users.py` | service source | keep |
| 312 | `src/cms/service/scheduler.py` | service source | keep |
| 313 | `src/cms/workflow/__init__.py` | service source | keep |
| 314 | `src/cms/workflow/airflow_runtime_policy.py` | service source | keep |
| 315 | `src/cms/workflow/airflow_skeleton.py` | service source | keep |
| 316 | `src/cms/workflow/anomaly_warning_tasks.py` | service source | keep |
| 317 | `src/cms/workflow/champion_airflow_skeleton.py` | service source | keep |
| 318 | `src/cms/workflow/champion_tasks.py` | service source | keep |
| 319 | `src/cms/workflow/daily_report_airflow.py` | service source | keep |
| 320 | `src/cms/workflow/langgraph_review.py` | service source | keep |
| 321 | `src/cms/workflow/langgraph_skeleton.py` | service source | keep |
| 322 | `src/cms/workflow/model_serving_airflow_skeleton.py` | service source | keep |
| 323 | `src/cms/workflow/model_serving_pipeline.py` | service source | keep |
| 324 | `src/cms/workflow/monthly_report_airflow.py` | service source | keep |
| 325 | `src/cms/workflow/nodes/__init__.py` | service source | keep |
| 326 | `src/cms/workflow/nodes/review_nodes.py` | service source | keep |
| 327 | `src/cms/workflow/pmax_forecast_tasks.py` | service source | keep |
| 328 | `src/cms/workflow/replay_clock.py` | service source | keep |
| 329 | `src/cms/workflow/report_freshness.py` | service source | keep |
| 330 | `src/cms/workflow/report_readiness_airflow.py` | service source | keep |
| 331 | `src/cms/workflow/review_jobs.py` | service source | keep |
| 332 | `src/cms/workflow/router/__init__.py` | service source | keep |
| 333 | `src/cms/workflow/router/anomaly_agent.py` | service source | keep |
| 334 | `src/cms/workflow/router/cms_agent.py` | service source | keep |
| 335 | `src/cms/workflow/router/forecast_agent.py` | service source | keep |
| 336 | `src/cms/workflow/router/graph.py` | service source | keep |
| 337 | `src/cms/workflow/router/llm_client.py` | service source | keep |
| 338 | `src/cms/workflow/router/rag_agent.py` | service source | keep |
| 339 | `src/cms/workflow/router/reporting_agent.py` | service source | keep |
| 340 | `src/cms/workflow/router/state.py` | service source | keep |
| 341 | `src/cms/workflow/state.py` | service source | keep |
| 342 | `src/cms/workflow/weekly_report_airflow.py` | service source | keep |
