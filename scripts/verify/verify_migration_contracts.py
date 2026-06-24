#!/usr/bin/env python3
"""Static verification for CMS reference/canonical migration package.

No DB connection, no .env reads, no production side effects.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.migrations.generate_reference_migration import (  # noqa: E402
    classify_inventory,
    inventory_sql,
    render_approval_packet,
    render_canonical_observed_contract_rollback_sql,
    render_canonical_observed_contract_sql,
    render_reconciliation_sql,
    render_reference_copy_sql,
    render_reference_rollback_sql,
)

PLAN_PATH = ROOT / ".hermes/plans/2026-06-01_124920-cms-reference-canonical-migration-plan.md"


def main() -> None:
    assert PLAN_PATH.exists(), PLAN_PATH
    plan = PLAN_PATH.read_text()
    for required in (
        "Track R: corrected/resampled data -> reference.corrected_resampled_* copy-first migration",
        "Track C: harmonized observed source -> canonical.measurement_* population or service switch",
        "Track R approval은 Track C approval을 포함하지 않는다",
        "Observed source가 검증되지 않으면 canonical service switch는 `BLOCK`",
    ):
        assert required in plan, required

    inventory = classify_inventory(
        [
            {
                "table_schema": "canonical",
                "table_name": "measurement_1h",
                "source_family": "corrected_resampled",
            },
            {
                "table_schema": "staging",
                "table_name": "measurement_1min",
                "source_family": "observed",
            },
        ]
    )
    reference_sql = render_reference_copy_sql(inventory, run_id="mig_verify")
    reference_rollback_sql = render_reference_rollback_sql(inventory, run_id="mig_verify")
    canonical_sql = render_canonical_observed_contract_sql()
    canonical_rollback_sql = render_canonical_observed_contract_rollback_sql()
    reconciliation_sql = render_reconciliation_sql(inventory)
    approval_packet = render_approval_packet(run_id="mig_verify", classified=inventory)
    inv_sql = inventory_sql()

    assert "reference.corrected_1h" in reference_sql
    assert "DROP TABLE" not in reference_sql.upper()
    assert "TRUNCATE" not in reference_sql.upper()
    assert "DELETE FROM CANONICAL" not in reference_sql.upper()
    assert "measurement_1min" in inv_sql
    assert "measurement_15min" in inv_sql
    assert "measurement_1h" in inv_sql
    assert "CREATE TABLE IF NOT EXISTS canonical.measurement_1min" in canonical_sql
    assert "coverage_formula_check" in canonical_sql
    assert "mask_code LIKE '%gap%'" in canonical_sql
    assert "DELETE FROM reference.corrected_1h" in reference_rollback_sql
    assert "performs no DDL/DML" in canonical_rollback_sql
    assert "value IS NULL" in canonical_sql
    assert "source_hash" in reconciliation_sql
    assert "target_hash" in reconciliation_sql
    assert "target_corrected_family_rows" in reconciliation_sql
    assert "source_sample" in reconciliation_sql
    assert "INSERT" not in reconciliation_sql.upper()
    assert "승인: cms reference corrected_resampled copy production 실행 허용" in approval_packet
    assert "승인: cms canonical observed population/switch production 실행 허용" in approval_packet
    print("cms migration contracts ok")


if __name__ == "__main__":
    main()
