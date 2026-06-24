# Live Quality / AWS Evidence Packet (Read-Only)

> Packet purpose: collect live-quality and AWS migration evidence without writes, DDL,
> grants, restarts, dotenv loading, or connection-string disclosure.

## 1. Run Metadata

- Approval ID: `<APPROVAL_ID>`
- Git commit: `<GIT_COMMIT_SHA>`
- Probe version: `live-quality-read-only-probe/v1`
- Probe artifact: `scripts/ops/live_quality_read_only_probe.py`
- Operator: `<OPERATOR>`
- Generated at (UTC): `<GENERATED_AT>`
- Environment label: `<ENVIRONMENT_LABEL>`

## 2. Approval Gate

- Evidence run approved by: `<APPROVER>`
- Approval scope: read-only SELECT evidence capture only
- Expiry / timebox: `<APPROVAL_EXPIRY>`
- Explicitly excluded: production writes, DDL, GRANT/REVOKE, service restart,
  AWS mutation, database mutation, secret capture, dotenv loading

## 3. Transaction Read-Only Proof

Paste the `transaction_read_only_proof` result from the probe output.

| field | observed value | expected value |
| --- | --- | --- |
| `transaction_read_only` | `<VALUE>` | `on` |
| `default_transaction_read_only` | `<VALUE>` | `on` |
| `statement_timeout` | `<VALUE>` | bounded timeout |
| `database_name` | `<VALUE>` | redacted if needed |
| `probe_user` | `<VALUE>` | least-privilege read-only role |

## 4. No-Write / No-Secret Attestation

- Probe did not load `.env` automatically: `<YES_NO>`
- Probe did not print connection strings or credentials: `<YES_NO>`
- Probe SQL was limited to read-only transaction controls and SELECT evidence: `<YES_NO>`
- No AWS or DB mutation was performed: `<YES_NO>`
- Output was reviewed for redaction before sharing: `<YES_NO>`
- Least-privilege role used: `<ROLE_DESCRIPTION>`

## 5. Skipped Optional Queries

List optional probes skipped by `to_regclass` relation guards.

| query name | guarded relation | skip reason |
| --- | --- | --- |
| `<QUERY_NAME>` | `<SCHEMA.TABLE>` | optional relation absent / not approved |

## 6. Schema Evidence

Paste or attach the `schema_inventory` and `column_inventory` sections.

- Schemas reviewed: `live`, `qa`, `ops`, `mart`
- Unexpected tables/columns: `<NONE_OR_DETAILS>`
- Redaction notes: `<NONE_OR_DETAILS>`

## 7. Index Evidence

Paste or attach the `index_inventory` section from `pg_indexes`.

- Missing expected indexes: `<NONE_OR_DETAILS>`
- Candidate index notes: `<NONE_OR_DETAILS>`
- Index-change approval required before any follow-up DDL: `<YES>`

## 8. Count / Freshness Evidence

Paste optional count outputs that were present and approved for read-only SELECT.

| relation | row_count | min timestamp | max timestamp | freshness note |
| --- | ---: | --- | --- | --- |
| `live.measurement_15min` | `<COUNT>` | `<MIN>` | `<MAX>` | `<NOTE>` |
| `live.measurement_1h` | `<COUNT>` | `<MIN>` | `<MAX>` | `<NOTE>` |

## 9. Reason / Bad-Row Evidence

Paste reason distributions, if the guarded optional tables were present.

| source | reason/status | row_count | latest observed | interpretation |
| --- | --- | ---: | --- | --- |
| `qa.bad_row` | `<REASON_CODE>` | `<COUNT>` | `<MAX_OBSERVED_AT>` | `<NOTE>` |
| `ops.quality_gate_result` | `<STATUS>/<REASON_CODE>` | `<COUNT>` | `<MAX_CHECKED_AT>` | `<NOTE>` |

## 10. AWS Evidence Linkage

- AWS account/region label, redacted: `<ACCOUNT_REGION_LABEL>`
- Evidence storage location, redacted: `<ARTIFACT_LOCATION>`
- IAM posture note: read-only evidence role / no mutation permission asserted
- Related deployment or migration ticket: `<TICKET_ID>`

## 11. Review Outcome

- Overall verdict: `<PASS_FAIL_BLOCKED>`
- Blockers: `<NONE_OR_DETAILS>`
- Follow-up requires separate approval: `<YES>`
- Reviewer sign-off: `<REVIEWER_AND_TIME>`
