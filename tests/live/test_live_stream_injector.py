from __future__ import annotations

import csv
import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from scripts.live.run_live_stream_injector import (
    build_payload,
    compute_replay_delay,
    discover_harmonized_files,
    merged_rows,
    parse_meter_measurement,
    select_source_files,
)


class LiveStreamInjectorTests(unittest.TestCase):
    def test_merge_rows_preserves_cross_meter_event_time_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "meter_a" / "meter_a.P_harmonized.csv.gz"
            b = root / "meter_b" / "meter_b.P_harmonized.csv.gz"
            write_gzip_csv(
                a,
                header="meter_a.P",
                rows=(
                    ("2024-01-01T00:00:00+00:00", "1.0"),
                    ("2024-01-01T00:02:00+00:00", "3.0"),
                ),
            )
            write_gzip_csv(
                b,
                header="meter_b.P",
                rows=(
                    ("2024-01-01T00:01:00+00:00", "2.0"),
                    ("2024-01-01T00:03:00+00:00", "4.0"),
                ),
            )

            rows = list(merged_rows([a, b]))

            self.assertEqual([row.event_ts for row in rows], [
                "2024-01-01T00:00:00+00:00",
                "2024-01-01T00:01:00+00:00",
                "2024-01-01T00:02:00+00:00",
                "2024-01-01T00:03:00+00:00",
            ])
            self.assertEqual([row.meter_urn for row in rows], ["meter_a", "meter_b", "meter_a", "meter_b"])

    def test_meter_balanced_selection_covers_meters_before_second_series(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files: list[Path] = []
            for meter in ("meter_a", "meter_b", "meter_c"):
                for measurement in ("P", "W"):
                    path = root / meter / f"{meter}.{measurement}_harmonized.csv.gz"
                    write_gzip_csv(path, header=f"{meter}.{measurement}", rows=(("2024-01-01T00:00:00+00:00", "1.0"),))
                    files.append(path)

            selected = select_source_files(sorted(files), max_files=3, selection_mode="meter-balanced")

            self.assertEqual({path.parent.name for path in selected}, {"meter_a", "meter_b", "meter_c"})
            self.assertEqual(len(selected), 3)

    def test_event_time_replay_delay_preserves_source_gaps(self) -> None:
        times = [
            datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
            datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC),
            datetime(2024, 1, 1, 0, 0, 3, tzinfo=UTC),
            datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        ]

        delays = [compute_replay_delay(previous, current, time_scale=1.0) for previous, current in zip(times, times[1:])]
        scaled = [compute_replay_delay(times[2], times[3], time_scale=10.0)]

        self.assertEqual(delays, [1.0, 1.0, 7.0])
        self.assertEqual(scaled, [0.7])

    def test_required_meter_gate_fails_when_selection_is_too_narrow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_gzip_csv(root / "meter_a" / "meter_a.P_harmonized.csv.gz", header="meter_a.P", rows=(("2024-01-01T00:00:00+00:00", "1.0"),))
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/live/run_live_stream_injector.py",
                    "--source-root",
                    str(root),
                    "--selection-mode",
                    "meter-balanced",
                    "--required-meters",
                    "2",
                    "--max-files",
                    "1",
                    "--max-events",
                    "1",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("selected_meter_count 1 is below --required-meters 2", completed.stderr)

    def test_duration_minutes_stops_at_source_event_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_gzip_csv(
                root / "meter_a" / "meter_a.P_harmonized.csv.gz",
                header="meter_a.P",
                rows=(
                    ("2024-01-01T00:00:00+00:00", "1.0"),
                    ("2024-01-01T00:20:00+00:00", "2.0"),
                    ("2024-01-01T00:40:00+00:00", "3.0"),
                ),
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/live/run_live_stream_injector.py",
                    "--source-root",
                    str(root),
                    "--max-files",
                    "1",
                    "--max-events",
                    "10",
                    "--duration-minutes",
                    "30",
                    "--replay-clock",
                    "event-time",
                    "--time-scale",
                    "10",
                ],
                cwd=REPO_ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

            payload = json.loads(completed.stdout)
            self.assertEqual(payload["emitted_count"], 2)
            self.assertEqual(payload["first_event_ts"], "2024-01-01T00:00:00+00:00")
            self.assertEqual(payload["last_event_ts"], "2024-01-01T00:20:00+00:00")
            self.assertEqual(payload["duration_minutes"], 30.0)

    def test_payload_uses_ingestion_contract_fields_and_no_db_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "H1.K11" / "H1.K11.P_harmonized.csv.gz"
            write_gzip_csv(path, header="H1.K11.P", rows=(("2024-01-01T00:00:00+00:00", "25300.0"),))
            row = next(merged_rows([path]))

            payload = build_payload(row, source_system="cms_live_source_archive", received_at=datetime(2026, 1, 1, tzinfo=UTC))

            self.assertEqual(payload["schema_version"], "measurement_raw_v1")
            self.assertEqual(payload["source_system"], "cms_live_source_archive")
            self.assertEqual(payload["meter_urn"], "H1.K11")
            self.assertEqual(payload["measurement"], "P")
            self.assertEqual(payload["value_numeric"], 25300.0)
            self.assertGreaterEqual(len(payload["raw_payload_hash"]), 16)
            self.assertIn("H1.K11.P_harmonized.csv.gz:2", payload["source_event_id"])

    def test_command_defaults_to_dry_run_without_network_or_db_clients(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_gzip_csv(
                root / "H1.K11" / "H1.K11.P_harmonized.csv.gz",
                header="H1.K11.P",
                rows=(("2024-01-01T00:00:00+00:00", "1.0"),),
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/live/run_live_stream_injector.py",
                    "--source-root",
                    str(root),
                    "--max-files",
                    "1",
                    "--max-events",
                    "1",
                ],
                cwd=REPO_ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

            payload = json.loads(completed.stdout)
            self.assertEqual(payload["mode"], "dry_run")
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["emitted_count"], 1)
            self.assertEqual(payload["accepted_count"], 0)
            self.assertFalse(payload["postgres_write_attempted"])
            self.assertFalse(payload["kafka_client_imported"])
            self.assertFalse(payload["db_client_imported"])

    def test_discovery_uses_live_source_archive_harmonized_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            included = root / "H1.K11" / "H1.K11.P_harmonized.csv.gz"
            backup = root / "H1.K11" / "backup" / "H1.K11.P_harmonized.csv.gz"
            excluded = root / "H1.K11" / "H1.K11.P_corrected_resampled_15min.csv.gz"
            write_gzip_csv(included, header="H1.K11.P", rows=(("2024-01-01T00:00:00+00:00", "1.0"),))
            write_gzip_csv(backup, header="H1.K11.P", rows=(("2024-01-01T00:00:00+00:00", "1.0"),))
            write_gzip_csv(excluded, header="H1.K11.P", rows=(("2024-01-01T00:00:00+00:00", "1.0"),))

            files = discover_harmonized_files(root)
            all_files = discover_harmonized_files(root, include_backup=True)

            self.assertEqual(files, [included])
            self.assertEqual(all_files, [included, backup])

    def test_parse_meter_measurement_splits_on_last_dot(self) -> None:
        self.assertEqual(parse_meter_measurement(["datetime_utc", "H2.Z351.WQ_in"]), ("H2.Z351", "WQ_in"))


REPO_ROOT = Path(__file__).resolve().parents[2]


def write_gzip_csv(path: Path, *, header: str, rows: tuple[tuple[str, str], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["datetime_utc", header])
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
