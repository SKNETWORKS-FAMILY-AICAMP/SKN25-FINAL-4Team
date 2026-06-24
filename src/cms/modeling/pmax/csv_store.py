from __future__ import annotations

from pathlib import Path

import pandas as pd


FORECAST_KEY_COLUMNS = ["logical_meter", "base_ts", "target_ts"]


def upsert_forecast_csv(
    incoming: pd.DataFrame,
    history_csv: Path,
) -> dict[str, int]:
    history_csv.parent.mkdir(parents=True, exist_ok=True)
    expected_columns = incoming.columns.tolist()
    if history_csv.exists():
        existing = pd.read_csv(history_csv)
        if existing.columns.tolist() != expected_columns:
            raise ValueError(
                f"History CSV schema mismatch: expected {expected_columns}, "
                f"got {existing.columns.tolist()}"
            )
    else:
        existing = pd.DataFrame(columns=expected_columns)

    missing_keys = [
        column for column in FORECAST_KEY_COLUMNS if column not in expected_columns
    ]
    if missing_keys:
        raise ValueError(f"Forecast CSV is missing key columns: {missing_keys}")
    if incoming.duplicated(FORECAST_KEY_COLUMNS).any():
        raise ValueError("Incoming forecast batch contains duplicate forecast keys")

    existing_keys = set(
        existing[FORECAST_KEY_COLUMNS].astype(str).itertuples(index=False, name=None)
    )
    incoming_keys = set(
        incoming[FORECAST_KEY_COLUMNS].astype(str).itertuples(index=False, name=None)
    )
    updated_rows = len(existing_keys & incoming_keys)
    inserted_rows = len(incoming_keys - existing_keys)

    combined = pd.concat([existing, incoming], ignore_index=True)
    combined = combined.drop_duplicates(FORECAST_KEY_COLUMNS, keep="last")
    combined = combined.sort_values(
        ["base_ts", "logical_meter", "target_ts"],
        kind="stable",
    ).reset_index(drop=True)

    temporary_path = history_csv.with_suffix(history_csv.suffix + ".tmp")
    combined.to_csv(temporary_path, index=False)
    temporary_path.replace(history_csv)
    return {
        "inserted_rows": inserted_rows,
        "updated_rows": updated_rows,
        "total_rows": len(combined),
    }
