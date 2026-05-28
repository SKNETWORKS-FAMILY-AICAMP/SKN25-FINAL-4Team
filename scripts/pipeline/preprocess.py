from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DEFAULT_METER_URN = "H1.Z16"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config.meter_metadata import get_metadata
from scripts.pipeline.fetch_h1z16_with_weather import (
    build_engine,
    fetch_meter_data,
    fetch_weather_data,
    get_measurement_columns,
    validate_columns,
)


WEATHER_COLUMNS = ["Ta", "Igm"]


def get_numeric_columns(meter_urn: str) -> list[str]:
    return [*get_measurement_columns(meter_urn), *WEATHER_COLUMNS]


def fetch_joined_data(meter_urn: str) -> pd.DataFrame:
    engine = build_engine()
    df_main = fetch_meter_data(engine, meter_urn).copy()
    validate_columns(df_main, meter_urn)
    df_weather = fetch_weather_data(engine).copy()

    df = df_main.merge(df_weather, on="ts", how="left").sort_values("ts").reset_index(drop=True)

    for column in get_numeric_columns(meter_urn):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    return df


def apply_confirmed_physical_nan_rules(df: pd.DataFrame, metadata: dict[str, Any]) -> pd.DataFrame:
    cleaned = df.copy()

    for column in ["PF", "PF1", "PF2", "PF3"]:
        if column in cleaned.columns:
            cleaned.loc[cleaned[column].abs() > 1, column] = pd.NA

    for column in ["Ta", "Tvl", "Trl"]:
        if column in cleaned.columns:
            cleaned.loc[cleaned[column] < -273.15, column] = pd.NA

    for column in ["U1", "U2", "U3"]:
        if column in cleaned.columns:
            cleaned.loc[cleaned[column] <= 0, column] = pd.NA

    if "f" in cleaned.columns:
        cleaned.loc[cleaned["f"] <= 0, "f"] = pd.NA

    if "Igm" in cleaned.columns:
        cleaned.loc[cleaned["Igm"] < 0, "Igm"] = pd.NA

    if metadata.get("meter_type") == "thermal":
        for column in ["qv", "V"]:
            if column in cleaned.columns:
                cleaned.loc[cleaned[column] < 0, column] = pd.NA

    return cleaned


def preprocess_meter(
    meter_urn: str,
    print_progress: bool = True,
    print_issue_details: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    del print_issue_details

    metadata = get_metadata(meter_urn)
    if metadata is None:
        raise ValueError(f"Metadata not found for {meter_urn}")

    if print_progress:
        print(f"[1/2] 데이터 로드 중... ({meter_urn})")
    df = fetch_joined_data(meter_urn)
    df_before = df.copy()

    if print_progress:
        print(f"[2/2] 확정 물리 규칙 기반 NaN 처리 중... ({meter_urn})")
    df = apply_confirmed_physical_nan_rules(df, metadata)

    issues: list[dict[str, Any]] = []
    invalid_segments: list[dict[str, Any]] = []
    return df, df_before, issues, invalid_segments


def preprocess_default_meter(
    print_progress: bool = True,
    print_issue_details: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    return preprocess_meter(
        DEFAULT_METER_URN,
        print_progress=print_progress,
        print_issue_details=print_issue_details,
    )


def summarize_changed_nan_counts(df_before: pd.DataFrame, df_after: pd.DataFrame, meter_urn: str) -> None:
    print(f"meter_urn={meter_urn}")
    for column in get_numeric_columns(meter_urn):
        if column not in df_before.columns or column not in df_after.columns:
            continue
        before = int(df_before[column].isna().sum())
        after = int(df_after[column].isna().sum())
        if after != before:
            print(f"{column}: NaN before={before}, after={after}, added={after - before}")


def main() -> None:
    df, df_before, _, _ = preprocess_default_meter(
        print_progress=True,
        print_issue_details=True,
    )
    summarize_changed_nan_counts(df_before, df, DEFAULT_METER_URN)
    print("df.shape")
    print(df.shape)
    print()
    print("df.head()")
    print(df.head())


if __name__ == "__main__":
    main()
