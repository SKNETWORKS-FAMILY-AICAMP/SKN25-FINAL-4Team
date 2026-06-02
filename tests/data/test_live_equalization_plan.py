from __future__ import annotations

import csv
import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cms.data.live_equalization_plan import (
    SeriesCadenceCountPolicy,
    build_cadence_equalization_count_plan,
    build_equalization_count_plan,
    build_tc0_inventory,
)


class LiveEqualizationPlanTests(unittest.TestCase):
    def test_tc0_inventory_maps_harmonized_series_to_available_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_series(root, "V.Z84", "W", refs=("1min", "15min", "1h"))
            write_series(root, "V.Z84", "P", refs=("1min", "15min", "1h"))
            write_series(root, "WeatherStation.Weather", "Ta", refs=("1min", "15min", "1h"))
            write_series(root, "V.Z84", "W_in", refs=("1min",))
            write_series(root / "V.Z84" / "backup", "V.Z84", "ignored", refs=("1min",))

            inventory = build_tc0_inventory(root)

        self.assertEqual(inventory.source_identifier_count, 2)
        self.assertEqual(inventory.measurement_series_count, 4)
        self.assertEqual(inventory.corrected_5min_ref_count, 0)
        self.assertEqual(inventory.missing_reference_counts, {"corrected_1min_ref": 0, "corrected_15min_ref": 1, "corrected_1h_ref": 1})
        self.assertFalse(inventory.side_effects_executed)
        self.assertFalse(inventory.writes_allowed)

        entry = inventory.by_meter_measurement[("V.Z84", "W")]
        self.assertTrue(entry.harmonized_file.endswith("V.Z84.W_harmonized.csv.gz"))
        self.assertTrue(entry.corrected_1min_ref.endswith("V.Z84.W_corrected_resampled_1min.csv.gz"))
        self.assertEqual(entry.eq_1min_reference_kind, "corrected_resampled_1min")
        self.assertEqual(entry.eq_5min_reference_kind, "derived_from_corrected_resampled_1min")
        self.assertEqual(entry.eq_5min_reference_source, entry.corrected_1min_ref)

    def test_tc0_inventory_fails_on_ambiguous_harmonized_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_series(root / "a", "V.Z84", "W", refs=("1min",))
            write_series(root / "b", "V.Z84", "W", refs=("1min",))

            with self.assertRaisesRegex(ValueError, "ambiguous"):
                build_tc0_inventory(root)

    def test_count_plan_matches_contract_tc1_tc2_expected_rows(self) -> None:
        tc1 = build_equalization_count_plan(measurement_series_count=1603, window_minutes=60)
        self.assertEqual(tc1.rows_out_1min, 96_180)
        self.assertEqual(tc1.rows_out_5min, 19_236)
        self.assertEqual(tc1.rows_out_15min, 6_412)
        self.assertEqual(tc1.rows_out_1h, 1_603)
        self.assertEqual(tc1.eq_1min_reference, "corrected_resampled_1min")
        self.assertEqual(tc1.eq_5min_reference, "derived_from_corrected_resampled_1min")

        tc2 = build_equalization_count_plan(measurement_series_count=1603, window_minutes=24 * 60)
        self.assertEqual(tc2.rows_out_1min, 2_308_320)
        self.assertEqual(tc2.rows_out_5min, 461_664)
        self.assertEqual(tc2.rows_out_15min, 153_888)
        self.assertEqual(tc2.rows_out_1h, 38_472)

    def test_cadence_count_plan_uses_per_series_target_grid(self) -> None:
        plan = build_cadence_equalization_count_plan(
            cadence_policies=(
                SeriesCadenceCountPolicy(native_interval_seconds=1),
                SeriesCadenceCountPolicy(native_interval_seconds=60),
                SeriesCadenceCountPolicy(native_interval_seconds=300),
                SeriesCadenceCountPolicy(native_interval_seconds=900),
                SeriesCadenceCountPolicy(native_interval_seconds=3600),
            ),
            window_minutes=60,
        )

        self.assertEqual(plan.measurement_series_count, 5)
        self.assertEqual(plan.rows_out_1min, 120)
        self.assertEqual(plan.rows_out_5min, 24)
        self.assertEqual(plan.rows_out_15min, 16)
        self.assertEqual(plan.rows_out_1h, 5)

    def test_dry_run_cli_prints_tc0_and_count_plan_without_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_series(root, "V.Z84", "W", refs=("1min", "15min", "1h"))
            command = [
                sys.executable,
                "scripts/live/dry_run_live_equalization.py",
                "--data-root",
                str(root),
                "--window-minutes",
                "60",
            ]
            completed = subprocess.run(command, cwd=REPO_ROOT, check=True, text=True, capture_output=True)

        payload = json.loads(completed.stdout)
        self.assertFalse(payload["side_effects_executed"])
        self.assertFalse(payload["writes_allowed"])
        self.assertFalse(payload["local_artifacts_written"])
        self.assertEqual(payload["tc0_inventory"]["measurement_series_count"], 1)
        self.assertEqual(payload["count_plan"]["rows_out_5min"], 12)


REPO_ROOT = Path(__file__).resolve().parents[2]


def write_series(root: Path, meter_urn: str, measurement: str, *, refs: tuple[str, ...]) -> None:
    folder = root / meter_urn
    folder.mkdir(parents=True, exist_ok=True)
    key = f"{meter_urn}.{measurement}"
    write_gzip_csv(folder / f"{key}_harmonized.csv.gz", ["datetime_utc", key])
    for ref in refs:
        write_gzip_csv(folder / f"{key}_corrected_resampled_{ref}.csv.gz", ["datetime_utc", key])


def write_gzip_csv(path: Path, header: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerow(["2024-01-01T00:00:00+00:00", "1.0"])
