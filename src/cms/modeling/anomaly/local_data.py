"""Local file-backed training data readers for anomaly RunPod jobs."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from cms.modeling.anomaly.config import MeterSpec


def _candidate_paths(root: Path, meter_urn: str) -> list[Path]:
    safe_urn = meter_urn.replace("/", "_")
    return [
        root / f"{meter_urn}.parquet",
        root / f"{safe_urn}.parquet",
        root / meter_urn / "data.parquet",
        root / safe_urn / "data.parquet",
    ]


def fetch_meter_frame_from_dir(input_data_dir: str | Path, spec: MeterSpec) -> pd.DataFrame:
    """Read one meter's raw training frame from parquet files.

    Expected schema matches the DB-backed fetcher: ``ts`` plus all columns in
    ``spec.features``. ``meter_urn`` is optional and is added when absent.
    """
    root = Path(input_data_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"input_data_dir not found: {root}")

    candidates = _candidate_paths(root, spec.meter_urn)
    source_path = next((path for path in candidates if path.is_file()), None)
    if source_path is None:
        expected = ", ".join(str(path) for path in candidates)
        raise FileNotFoundError(f"no parquet file for {spec.meter_urn}; expected one of: {expected}")

    frame = pd.read_parquet(source_path)
    required = ["ts", *spec.features]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{spec.meter_urn}: local parquet missing columns {missing}")

    if "meter_urn" not in frame.columns:
        frame = frame.copy()
        frame["meter_urn"] = spec.meter_urn

    return frame[["ts", "meter_urn", *spec.features]].sort_values("ts").reset_index(drop=True)
