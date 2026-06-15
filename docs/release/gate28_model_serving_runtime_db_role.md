# Gate 28 Model-Serving Runtime DB Role Proposal

- scope: repo-local SQL proposal and read-only verification gate
- destructive_cleanup: not performed
- canonical_or_prod_write: not performed
- ddl_or_grant_change: not executed
- secrets: none

## Assumptions and trade-offs

- The dedicated runtime boundary should be a group role, not a password-bearing login role, so credential binding stays in AWS/RDS/admin tooling.
- Production model-serving should not read `reference.corrected_resampled_*` by default. Reference/backfill or explicitly approved hybrid reads are isolated in a separate read-only role.
- Output-table `SELECT` is included with `INSERT, UPDATE` for idempotency/evidence reads, but only on approved non-canonical mart/ops/qa serving outputs.
- No default privileges are granted. Future objects must be reviewed explicitly instead of inheriting broad access.

## Proposed roles

| Role | Login | Purpose |
|---|---:|---|
| `cms_model_serving_runtime` | `NOLOGIN` | Production/hybrid model-serving boundary for approved mart inputs and write-gated non-canonical outputs. |
| `cms_model_serving_reference_read` | `NOLOGIN` | Separate nonprod/reference-backfill read role for `reference.corrected_resampled_15min/1h`. |

## Runtime read boundary

`cms_model_serving_runtime` receives `SELECT` on approved live-serving feature inputs only:

- `mart.peak_feature_15min`
- `mart.anomaly_feature_1h`

It intentionally receives no `live.*`, `reference.*`, or `canonical.*` grants.

## Runtime write-gated output boundary

After an explicit production write gate, `cms_model_serving_runtime` may have `SELECT, INSERT, UPDATE` only on:

- `mart.pmax_forecast_15min`
- `mart.anomaly_warning_1h`
- `ops.pmax_forecast_inference_log`
- `ops.anomaly_warning_inference_log`
- `qa.pmax_forecast_evaluation`
- `qa.anomaly_warning_evaluation`
- `qa.model_serving_evidence_packet`

No `DELETE`, `TRUNCATE`, `REFERENCES`, `TRIGGER`, broad `CREATE`, default privileges, or canonical privileges are proposed.

## SQL files

- Proposal, gated DDL/GRANT, not run by default:
  - `scripts/database/migrations/gate28_model_serving_runtime_roles.sql`
- Verification gate, read-only catalog checks:
  - `scripts/database/verify/gate28_model_serving_runtime_privilege_check.sql`

## Verification gate

Run the read-only gate after any approved application of the role proposal:

```bash
psql "$DATABASE_URL" -f scripts/database/verify/gate28_model_serving_runtime_privilege_check.sql
```

Expected final row:

- `check_name = gate28_model_serving_runtime_privilege_boundary`
- `status = PASS`

The verification query fails if it finds missing expected privileges, unexpected table privileges, canonical privileges, ordinary `DELETE`, broad schema `CREATE` on any non-system schema/extra managed-schema usage, admin/login bits on the group roles, or managed-schema default privileges to either serving role/PUBLIC.

## Next execution gate

Do not apply the proposal until an authorized DB admin approves Gate 28 and runs the migration inside:

```sql
BEGIN;
SET LOCAL cms.allow_gate28_model_serving_runtime_role_plan = '1';
\i scripts/database/migrations/gate28_model_serving_runtime_roles.sql
COMMIT;
```

After apply, run the read-only verification gate above. Only if it returns `PASS` should a separate approval decide whether to bind the group role to a runtime login and open the model-serving write gate.
