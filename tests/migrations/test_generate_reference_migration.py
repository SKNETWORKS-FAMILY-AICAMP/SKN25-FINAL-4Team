from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.migrations.generate_reference_migration import (
    assert_no_unapproved_destructive_sql,
    assert_select_only_reconciliation,
    classify_inventory,
    inventory_sql,
    render_approval_packet,
    render_canonical_observed_contract_rollback_sql,
    render_canonical_observed_contract_sql,
    render_reconciliation_sql,
    render_reference_copy_sql,
    render_reference_rollback_sql,
)


class CmsReferenceMigrationGeneratorTests(unittest.TestCase):
    def test_classifies_corrected_resampled_as_reference(self) -> None:
        classified = classify_inventory(
            [
                {
                    "table_schema": "canonical",
                    "table_name": "measurement_1h",
                    "source_family": "corrected_resampled",
                }
            ]
        )

        self.assertEqual(len(classified), 1)
        self.assertEqual(classified[0].classification, "reference")
        self.assertEqual(classified[0].resolution, "1h")

    def test_classifies_repaired_resampled_families_as_reference(self) -> None:
        classified = classify_inventory(
            [
                {"table_schema": "canonical", "table_name": "measurement_1min", "source_family": "gap_filled"},
                {"table_schema": "canonical", "table_name": "measurement_15min", "notes": "leap spike zero repaired resampled"},
                {"table_schema": "canonical", "table_name": "measurement_1h", "notes": "linear interpolation forward_fill"},
            ]
        )

        self.assertEqual([row.classification for row in classified], ["reference", "reference", "reference"])

    def test_reference_copy_sql_is_copy_first_and_non_destructive(self) -> None:
        classified = classify_inventory(
            [
                {
                    "table_schema": "canonical",
                    "table_name": "measurement_1h",
                    "source_family": "corrected_resampled",
                }
            ]
        )

        sql = render_reference_copy_sql(classified, run_id="mig_20260601")

        self.assertIn("reference.corrected_resampled_1h", sql)
        self.assertIn("FROM canonical.measurement_1h AS src", sql)
        self.assertIn("source_family", sql)
        self.assertNotIn("DROP TABLE", sql.upper())
        self.assertNotIn("TRUNCATE", sql.upper())
        self.assertNotIn("DELETE FROM canonical", sql)
        self.assertNotIn("ALTER TABLE canonical", sql)

    def test_expected_db_guard_can_target_legacy_fems_database_for_pre_cutover_drafts(self) -> None:
        classified = classify_inventory(
            [
                {
                    "table_schema": "canonical",
                    "table_name": "measurement_1h",
                    "source_family": "corrected_resampled",
                }
            ]
        )

        reference_sql = render_reference_copy_sql(classified, run_id="mig_20260601", expected_db="fems")
        canonical_sql = render_canonical_observed_contract_sql(expected_db="fems")
        packet = render_approval_packet(run_id="mig_20260601", classified=classified, expected_db="fems")

        self.assertIn("current_database() <> 'fems'", reference_sql)
        self.assertIn("current_database() <> 'fems'", canonical_sql)
        self.assertIn("**Target DB:** `fems`", packet)
        self.assertIn("대상 DB: fems", packet)

    def test_canonical_contract_includes_observed_gap_columns_and_checks(self) -> None:
        sql = render_canonical_observed_contract_sql()

        for table in ("measurement_1min", "measurement_15min", "measurement_1h"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS canonical.{table}", sql)
        for column in (
            "expected_points",
            "observed_points",
            "gap_points",
            "coverage_ratio",
            "mask_code",
            "quality_summary",
            "provenance",
            "source_event_ids",
            "promotion_id",
        ):
            self.assertIn(column, sql)
        self.assertIn("value IS NULL", sql)
        self.assertIn("coverage_ratio = 0.0", sql)
        self.assertIn("coverage_ratio >= 0.0", sql)
        self.assertIn("coverage_ratio <= 1.0", sql)
        self.assertIn("coverage_formula_check", sql)
        self.assertIn("mask_code IS NOT NULL", sql)
        self.assertIn("mask_code LIKE '%gap%'", sql)

    def test_reconciliation_sql_is_select_only(self) -> None:
        classified = classify_inventory(
            [
                {
                    "table_schema": "canonical",
                    "table_name": "measurement_15min",
                    "source_family": "corrected_resampled",
                }
            ]
        )

        sql = render_reconciliation_sql(classified)

        self.assertIn("SELECT current_database()", sql)
        self.assertIn("source_rows", sql)
        self.assertIn("target_rows", sql)
        self.assertIn("source_hash", sql)
        self.assertIn("target_hash", sql)
        self.assertIn("source_meter_urns", sql)
        self.assertIn("target_measurements", sql)
        self.assertIn("source_null_values", sql)
        self.assertIn("target_gap_rows", sql)
        self.assertIn("target_corrected_family_rows", sql)
        self.assertIn("source_sample", sql)
        self.assertIn("target_sample", sql)
        assert_select_only_reconciliation(sql)

    def test_inventory_sql_includes_1min_15min_1h_counts(self) -> None:
        sql = inventory_sql()

        self.assertIn("measurement_1min", sql)
        self.assertIn("measurement_15min", sql)
        self.assertIn("measurement_1h", sql)
        self.assertNotIn("INSERT", sql.upper())
        self.assertNotIn("UPDATE", sql.upper())
        self.assertNotIn("DELETE", sql.upper())

    def test_approval_packet_separates_track_r_and_track_c(self) -> None:
        classified = classify_inventory(
            [
                {
                    "table_schema": "canonical",
                    "table_name": "measurement_1h",
                    "source_family": "corrected_resampled",
                }
            ]
        )

        packet = render_approval_packet(run_id="mig_20260601", classified=classified)

        self.assertIn("Track R", packet)
        self.assertIn("Track C", packet)
        self.assertIn("Track R approval does not imply Track C approval", packet)
        self.assertIn("canonical.measurement_1h", packet)

    def test_rollback_drafts_are_generated_and_scoped(self) -> None:
        classified = classify_inventory(
            [
                {
                    "table_schema": "canonical",
                    "table_name": "measurement_1h",
                    "source_family": "corrected_resampled",
                }
            ]
        )

        reference_rollback = render_reference_rollback_sql(classified, run_id="mig_20260601")
        canonical_rollback = render_canonical_observed_contract_rollback_sql()

        self.assertIn("DELETE FROM reference.corrected_resampled_1h", reference_rollback)
        self.assertIn("migration_run_id = 'mig_20260601'", reference_rollback)
        self.assertNotIn("DELETE FROM canonical", reference_rollback)
        self.assertIn("performs no DDL/DML", canonical_rollback)

    def test_identifier_validation_rejects_unsafe_names(self) -> None:
        with self.assertRaises(ValueError):
            classify_inventory(
                [
                    {
                        "table_schema": "canonical;drop schema public",
                        "table_name": "measurement_1h",
                        "source_family": "corrected_resampled",
                    }
                ]
            )

    def test_destructive_sql_guard_flags_canonical_delete(self) -> None:
        with self.assertRaises(ValueError):
            assert_no_unapproved_destructive_sql("DELETE FROM canonical.measurement_1h")

    def test_cli_writes_expected_drafts_from_inventory_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inventory = tmp_path / "inventory.json"
            inventory.write_text(
                json.dumps(
                    [
                        {
                            "table_schema": "canonical",
                            "table_name": "measurement_1h",
                            "source_family": "corrected_resampled",
                        }
                    ]
                )
            )
            out_dir = tmp_path / "drafts"

            subprocess.run(
                [
                    sys.executable,
                    "scripts/migrations/generate_reference_migration.py",
                    "--inventory-json",
                    str(inventory),
                    "--run-id",
                    "mig_20260601",
                    "--out-dir",
                    str(out_dir),
                ],
                check=True,
            )

            self.assertTrue((out_dir / "20260601_cms_reference_copy.sql").exists())
            self.assertTrue((out_dir / "20260601_cms_reference_copy_rollback.sql").exists())
            self.assertTrue((out_dir / "20260601_cms_reference_reconciliation.sql").exists())
            self.assertTrue((out_dir / "20260601_cms_canonical_observed_contract.sql").exists())
            self.assertTrue((out_dir / "20260601_cms_canonical_observed_contract_rollback.sql").exists())
            self.assertTrue((out_dir / "approval_packet.md").exists())


if __name__ == "__main__":
    unittest.main()
