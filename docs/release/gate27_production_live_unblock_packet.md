# Gate 27 Production/Live Unblock Packet

- scope: read-only inventory and approval packet only
- destructive_cleanup: not performed
- canonical_or_prod_write: not performed
- ddl_or_grant_change: not performed
- generated_for: SKN25/CMS model-serving production/live readiness

## Current verdict

- nonprod reference_backfill PC3 model-serving runtime: PASS
- production/live model-serving readiness: BLOCK
- release/dev overwrite readiness: BLOCK

## Read-only DB inventory

### Relation presence

The following relations exist in AWS PostgreSQL:

- `mart.anomaly_feature_1h`
- `mart.peak_feature_15min`
- `mart.pmax_forecast_15min`
- `mart.anomaly_warning_1h`
- `ops.pmax_forecast_inference_log`
- `ops.anomaly_warning_inference_log`
- `qa.model_serving_evidence_packet`
- `reference.corrected_resampled_1h`
- `reference.corrected_resampled_15min`

### Cheap table stats

- `mart.anomaly_feature_1h`: approximately `0` rows, `24 kB`
- `mart.anomaly_warning_1h`: approximately `0` rows, `32 kB`
- `mart.peak_feature_15min`: approximately `56,344,897` rows, `20 GB`
- `mart.pmax_forecast_15min`: approximately `16` rows, `48 kB`
- `ops.anomaly_warning_inference_log`: approximately `0` rows, `16 kB`
- `ops.pmax_forecast_inference_log`: approximately `1` row, `32 kB`
- `qa.model_serving_evidence_packet`: approximately `1` row, `48 kB`
- `reference.corrected_resampled_1h`: approximately `68,468,728` rows, `11 GB`
- `reference.corrected_resampled_15min`: approximately `272,668,691` rows, `46 GB`

## P-Max strict readiness blocker

Checked bounded strict window:

- window: `2023-12-31 08:00 KST` to `2024-01-01 07:45 KST`
- meters: `V.Z81`, `V.Z82`, `H2.Z351`, `H2.Z361`
- measurements: `P`, `U1`, `PF`

Each meter/measurement showed:

- expected_windows: `192`
- `source_mode='live_observed'` rows: `31`
- `source_mode IS NULL` rows: `192`
- total rows: `223`

Interpretation:

- P-Max compatibility runs pass because null-lineage legacy rows are allowed.
- P-Max strict production readiness remains blocked until sufficient `live_observed` rows exist or a bounded provenance materialization approval is granted.
- Do not silently relax strict query contracts to reference or null-lineage rows.

## Anomaly production readiness blocker

`mart.anomaly_feature_1h` exists but contains approximately `0` rows.

Columns observed:

- `bucket_ts`
- `meter_urn`
- `feature_set`
- `p_value`
- `u1_value`
- `pf_value`
- `qv_value`
- `tdiff_value`
- `derived_features`
- `input_quality`
- `source_refs`
- `created_at`

Indexes observed:

- primary key on `(bucket_ts, meter_urn)`
- index on `(meter_urn, bucket_ts)`

Interpretation:

- Approved anomaly DB-serving path is blocked because the feature table has no production rows.
- Reference/backfill direct-read route remains nonprod evidence only.

## Runtime privilege blocker

Current runtime DB user from environment resolves to `cms`.

Read-only privilege check found:

- `canonical` schema: `USAGE=true`, `CREATE=true`
- canonical table write privilege summary: insert/update/delete privileges exist on `3` canonical tables
- model-serving mart/ops/qa target tables: SELECT/INSERT/UPDATE/DELETE privileges are present

Interpretation:

- Code-level write gates are working, but DB role privileges are too broad for production model-serving runtime.
- Production serving should use a narrower dedicated role or a verified grant boundary before any write gate is opened.
- No grant or revoke was executed.

## Required approval gates

### Gate A: Dedicated model-serving DB role or privilege boundary

Approval required before changing DB roles/grants.

Target properties:

- read-only access to serving input tables:
  - `mart.peak_feature_15min`
  - `mart.anomaly_feature_1h`
  - optionally `reference.corrected_resampled_*` only for nonprod reference jobs
- write access only to approved model-serving output tables when production write gate opens:
  - `mart.pmax_forecast_15min`
  - `mart.anomaly_warning_1h`
  - `ops.pmax_forecast_inference_log`
  - `ops.anomaly_warning_inference_log`
  - `qa.model_serving_evidence_packet`
- no canonical write privileges
- no broad schema CREATE privileges in `canonical`

### Gate B: P-Max strict unblock

Options:

1. Wait for live stream/rollup to produce complete `source_mode='live_observed'` coverage.
2. Prepare a bounded, approval-gated provenance materialization packet for known null-lineage historical rows.

Any materialization must be bounded by:

- meter list
- measurement list
- exact time window
- target row count estimate
- no canonical write
- post-materialization strict no-write serving acceptance check

### Gate C: Anomaly feature materialization

Required before production anomaly serving:

- define source mapping into `mart.anomaly_feature_1h`
- populate only with approved live/production provenance, not reference masquerading as live
- preserve `source_refs` and `input_quality`
- run no-write anomaly serving from `mart.anomaly_feature_1h`
- keep reference/backfill route explicitly nonprod

### Gate D: Clean release candidate

Before dev overwrite or release:

- use clean temporary checkout, not current index
- copy only file-level allowlist plus approved dependency closure
- exclude `artifacts/`, runtime logs, and secret-bearing env files from source release
- rerun targeted tests in the clean release tree
- get fern release hygiene PASS

## Acceptance checks after approval

Production model-serving may be considered only after all of the following pass:

1. `mart.anomaly_feature_1h` has bounded approved rows for target meters/window.
2. P-Max strict `live_observed` run produces expected predictions without compatibility flag.
3. Anomaly DB-serving run produces predictions from `mart.anomaly_feature_1h`, not reference direct-read.
4. `write_attempted=false` no-write run passes for both models.
5. DB role check confirms no canonical write privileges for serving runtime.
6. Production write gate is opened only in a separate explicitly approved run.

## Reviewer addendum: required changes before approval

Independent reviews returned:

- data QA review: `REQUEST_CHANGES` for production/live approval packet
- DB safety review: `BLOCK` for production/live write gate

This packet remains valid as a read-only blocker inventory and stop packet. It is not sufficient as production/live write approval.

### P-Max strict full-scope requirement

The bounded inventory above used `192` windows as a sample window. Production strict readiness must use the contract scope:

- required_history_windows: `288`
- source meters: `4`
  - `V.Z81`
  - `V.Z82`
  - `H2.Z351`
  - `H2.Z361`
- measurements: `3`
  - `P`
  - `U1`
  - `PF`
- expected strict input keys: `288 × 4 × 3 = 3,456`

Before any P-Max production unblock, run a read-only full-scope packet with:

| item | required value |
|---|---:|
| required history windows | 288 |
| source meters | 4 |
| measurements | 3 |
| expected strict keys | 3,456 |
| current `live_observed` distinct keys | requery required |
| current null-lineage candidate keys | requery required |
| remaining missing keys | requery required |
| approved write target | `mart.peak_feature_15min` only |
| canonical write | false |
| cleanup/reconcile key | required before any materialization |

The observed `31` live rows per meter/measurement in the bounded sample is not enough for strict production readiness. The `192` null-lineage rows per meter/measurement may be candidates for a separate bounded provenance materialization packet, but they must not be silently treated as `live_observed`.

### Anomaly production full-scope requirement

For one full anomaly production forecast-origin, the expected serving input scope is:

- history hours: `343`
- model meters: `63`
- expected feature rows: `343 × 63 = 21,609`

Before any anomaly production unblock, define and verify:

1. exact forecast origin or exact input window
2. target meter list, or explicitly all `63` model meters
3. expected feature row count, with scoped-down count if not full 63-meter mode
4. `source_refs` non-null and valid
5. `input_quality` pass/warn/block semantics
6. electric/heat feature-set mapping completeness
7. no reference direct-read masquerading as production input
8. no-write DB-serving run from `mart.anomaly_feature_1h`

Reference direct-read evidence remains nonprod only.

### DB role requirement

Current `cms` DB role is too broad for production model-serving runtime.

Production serving must not use the current broad `cms` role. It requires a dedicated runtime role such as `cms_model_serving_runtime` or equivalent, with these properties:

- no canonical schema `CREATE`
- no canonical table `INSERT`, `UPDATE`, or `DELETE`
- no broad `CREATE` on production schemas
- no ordinary `DELETE` on model-serving output tables
- read-only access to approved production input tables:
  - `mart.peak_feature_15min`
  - `mart.anomaly_feature_1h`
- production output access only to approved output tables, and only after explicit write-gate approval:
  - `mart.pmax_forecast_15min`
  - `mart.anomaly_warning_1h`
  - `ops.pmax_forecast_inference_log`
  - `ops.anomaly_warning_inference_log`
  - `qa.model_serving_evidence_packet`
- `reference.corrected_resampled_*` access belongs to a separate nonprod reference/backfill role unless explicitly approved otherwise
- default privileges must be checked so broad future-table grants do not reappear

### Production/live BLOCK conditions still active

Production/live model-serving remains blocked until all of the following are true:

1. P-Max strict 288-window `live_observed` coverage is complete, or a bounded provenance materialization packet is separately approved and verified.
2. `mart.anomaly_feature_1h` contains approved production/live feature rows for the required forecast-origin scope.
3. No-write P-Max run succeeds without compatibility flag.
4. No-write anomaly run succeeds from `mart.anomaly_feature_1h`, not reference direct-read.
5. Dedicated runtime role privilege checks pass.
6. Release candidate hygiene receives fern PASS from a clean temporary checkout.
7. Production write gate is opened only in a separate explicit approval run.

## Current stop point

This packet is read-only. No production write, canonical write, grant, revoke, DDL, or destructive cleanup has been executed.
