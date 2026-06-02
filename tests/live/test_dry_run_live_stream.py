from __future__ import annotations

import csv
import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DryRunLiveStreamTests(unittest.TestCase):
    def test_write_artifacts_uses_expected_target_table_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            output_root = Path(tmp) / "outputs"
            write_gzip_csv(root / "H2.Z64" / "H2.Z64.W_harmonized_15min.csv.gz")
            command = [
                sys.executable,
                "scripts/live/dry_run_live_stream.py",
                "--data-root",
                str(root),
                "--output-root",
                str(output_root),
                "--test-run-id",
                "dryrun_test",
                "--max-files",
                "1",
                "--sample-rows",
                "3",
                "--write-artifacts",
            ]
            completed = subprocess.run(command, cwd=REPO_ROOT, check=True, text=True, capture_output=True)

            payload = json.loads(completed.stdout)
            artifact_dir = output_root / "dryrun_test"
            self.assertTrue(payload["local_artifacts_written"])
            self.assertEqual(payload["samples"][0]["expected_target_table"], "canonical.measurement_15min")
            self.assertIn("canonical.measurement_15min", (artifact_dir / "summary.md").read_text(encoding="utf-8"))

            with (artifact_dir / "samples.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["expected_target_table"], "canonical.measurement_15min")


REPO_ROOT = Path(__file__).resolve().parents[2]


def write_gzip_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["datetime_utc", "H2.Z64.W"])
        writer.writerow(["2024-01-01T00:00:00+00:00", "1.0"])
        writer.writerow(["2024-01-01T00:15:00+00:00", "2.0"])
        writer.writerow(["2024-01-01T00:30:00+00:00", "3.0"])


if __name__ == "__main__":
    unittest.main()
