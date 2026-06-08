from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.forecasting.import_pmax.csv_store import upsert_forecast_csv


COLUMNS = [
    "logical_meter",
    "source_meter_urn",
    "base_ts",
    "input_end_ts",
    "target_ts",
    "horizon_minutes",
    "predicted_p_max",
    "created_at",
]


def forecast_frame(base_ts: str, value_offset: float = 0.0) -> pd.DataFrame:
    base = pd.Timestamp(base_ts)
    rows = []
    for meter_index, meter in enumerate(["V.Z81", "V.Z82", "H2.Z35x", "H2.Z36x"]):
        for step in range(1, 5):
            rows.append(
                {
                    "logical_meter": meter,
                    "source_meter_urn": meter,
                    "base_ts": base.isoformat(),
                    "input_end_ts": (base - pd.Timedelta(minutes=15)).isoformat(),
                    "target_ts": (base + pd.Timedelta(minutes=15 * step)).isoformat(),
                    "horizon_minutes": 15 * step,
                    "predicted_p_max": value_offset + meter_index * 100 + step,
                    "created_at": (base + pd.Timedelta(seconds=5)).isoformat(),
                }
            )
    return pd.DataFrame(rows, columns=COLUMNS)


class ForecastCsvUpsertTest(unittest.TestCase):
    def test_multiple_inference_runs_accumulate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "history.csv"

            first = upsert_forecast_csv(
                forecast_frame("2026-06-08T00:00:00Z"),
                output,
            )
            second = upsert_forecast_csv(
                forecast_frame("2026-06-08T03:00:00Z"),
                output,
            )

            self.assertEqual(first, {"inserted_rows": 16, "updated_rows": 0, "total_rows": 16})
            self.assertEqual(second, {"inserted_rows": 16, "updated_rows": 0, "total_rows": 32})
            self.assertEqual(len(pd.read_csv(output)), 32)

    def test_same_inference_key_updates_without_duplicate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "history.csv"
            base_ts = "2026-06-08T00:00:00Z"

            upsert_forecast_csv(forecast_frame(base_ts), output)
            result = upsert_forecast_csv(
                forecast_frame(base_ts, value_offset=1000.0),
                output,
            )
            stored = pd.read_csv(output)

            self.assertEqual(result, {"inserted_rows": 0, "updated_rows": 16, "total_rows": 16})
            self.assertEqual(len(stored), 16)
            self.assertGreater(stored["predicted_p_max"].min(), 1000.0)


if __name__ == "__main__":
    unittest.main()
