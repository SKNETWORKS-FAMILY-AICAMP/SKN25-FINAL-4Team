# A-to-Z DB contract and migration boundary (current AWS aligned)

Audit note: this document is the repo-local DB/access plan only. It must not be treated as permission to apply DDL or grants. Catalog facts below are point-in-time audit evidence and must be rechecked before AWS execution.

## Current AWS catalog facts used by this plan

- `live.measurement_event`: present, `109,590` rows.
- `canonical.measurement_1min`, `canonical.measurement_15min`, `canonical.measurement_1h`: present but `0` rows in the audited fact set; no default A-to-Z pipeline writes target canonical.
- `reference.corrected_resampled_15min`, `reference.corrected_resampled_1h`: populated reference/audit sources, not default serving truth.
- `mart.peak_feature_15min`: present/populated and is the direct P-Max serving feature source.
- `mart.peak_input_15min`: present legacy compatibility view/table. Do not add new `*_input_*` DB tables.
- `mart.peak_training_frame_15min`: target standard alias over the legacy peak frame, to be created only by approved migration.
- Missing target-only live lane objects: `live.measurement_policy`, `live.measurement_1min`, `live.bucket_queue`, `live.measurement_15min`, `live.measurement_1h`, `live.promotion_check`, `qa.live_measurement_issue`.
- Missing target-only anomaly/evidence objects: `mart.anomaly_feature_1h`, `mart.anomaly_warning_1h`, `ops.anomaly_warning_inference_log`, `qa.anomaly_warning_evaluation`, `qa.model_serving_evidence_packet`.

## Naming boundary

- Standard anomaly feature object: `mart.anomaly_feature_1h`.
- Forbidden new DB table name: `mart.anomaly_input_1h` and any new table matching `*._input_*`.
- Standard peak training frame alias: `mart.peak_training_frame_15min`.
- Legacy compatibility only: `mart.peak_input_15min`; keep read-only while callers migrate.
- P-Max direct runtime feature source remains `mart.peak_feature_15min` until the alias is approved and present.

## Role separation

| Role | Purpose | Default write scope | Canonical scope |
|---|---|---|---|
| `cms_ingest` | live event ingestion and operational evidence | `live.measurement_event`, target live lane objects after migration, `ops.pipeline_metric`, QA issue/tag tables | none |
| `cms_model_serving` | model-serving reads and non-canonical output writes | `mart.pmax_forecast_15min`, `ops.pmax_forecast_inference_log`, `qa.pmax_forecast_evaluation`, approved anomaly/evidence outputs | none |
| `cms_migration_admin` | approved DDL/grant migration boundary | DDL/grants only under explicit admin gate | separate approval path only |

The access draft `scripts/database/migrations/model_serving_runtime_access.sql` has an execution guard using `SET LOCAL cms.allow_access_ddl = '1'` and intentionally contains no `GRANT ... ON ... canonical.*` statements.

## Migration boundary

- Table/view DDL draft: `scripts/database/migrations/model_serving_tables.sql`.
- Access/grant draft: `scripts/database/migrations/model_serving_runtime_access.sql`.
- DDL helper: `scripts/migrations/apply_model_serving_tables.py` defaults to plan-only. Execution requires `--execute`, `ALLOW_MODEL_SERVING_DDL=1`, and explicit `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER` environment variables. It does not read `.env` files.
- This lane did **not** apply DDL or grants.

## Canonical promotion policy

Canonical writes are disabled by default. Any future canonical promotion is outside the model-serving runtime role and requires separate approval, explicit row-count evidence, rollback, and admin execution. Model-serving forecasts/warnings are serving outputs and QA evidence, not observed measurement facts.
