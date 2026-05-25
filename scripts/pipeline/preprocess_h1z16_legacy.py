from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
ISSUES_ZIP_PATH = Path("/home/playdata2/final_pj/issues.zip")
TARGET_METER_URN = "H1.Z16"
LONG_GAP_THRESHOLD = 24

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


CORE_NUMERIC_COLUMNS = [
    "P",
    "W",
    "PF",
    "PF1",
    "PF2",
    "PF3",
    "P1",
    "P2",
    "P3",
    "I1",
    "I2",
    "I3",
    "U1",
    "U2",
    "U3",
    "Q",
    "f",
    "W_in",
    "W_out",
    "Ta",
    "Igm",
]

THERMAL_NUMERIC_COLUMNS = [
    "P",
    "W",
    "Tvl",
    "Trl",
    "Tdiff",
    "qv",
    "V",
    "Ta",
    "Igm",
]


def maybe_import_issues_parser():
    parser_path = SCRIPTS_DIR / "utils" / "issues_parser.py"
    if not parser_path.exists():
        return None

    spec = importlib.util.spec_from_file_location("issues_parser", parser_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fallback_load_issues(zip_path: Path, meter_urn: str) -> list[dict[str, Any]]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to parse issues.zip") from exc

    with ZipFile(zip_path) as zip_file:
        matched_names = [name for name in zip_file.namelist() if meter_urn in name]
        if not matched_names:
            return []

        raw = zip_file.read(matched_names[0]).decode("utf-8")
        payload = yaml.safe_load(raw) or {}

    issues: list[dict[str, Any]] = []
    for issue_key, issue_info in payload.items():
        issues.append(
            {
                "issue_key": issue_key,
                "reference": issue_info.get("reference"),
                "reason": issue_info.get("reason"),
                "correction": issue_info.get("correction"),
                "time_start": issue_info.get("time_start"),
                "time_end": issue_info.get("time_end"),
                "comment": issue_info.get("comment"),
            }
        )
    return issues


def load_meter_issues(zip_path: Path, meter_urn: str) -> list[dict[str, Any]]:
    parser_module = maybe_import_issues_parser()
    if parser_module is not None:
        if hasattr(parser_module, "load_issues"):
            issues = parser_module.load_issues(zip_path)
        elif hasattr(parser_module, "parse_issues"):
            issues = parser_module.parse_issues(zip_path)
        else:
            issues = fallback_load_issues(zip_path, meter_urn)
    else:
        issues = fallback_load_issues(zip_path, meter_urn)

    filtered = []
    for issue in issues:
        reference = issue.get("reference")
        issue_key = issue.get("issue_key", "")
        if reference == meter_urn or meter_urn in str(issue_key):
            filtered.append(issue)
    return filtered


def print_issue_summary(issues: list[dict[str, Any]]) -> None:
    print(f"이슈 총 개수: {len(issues)}개")
    for issue in issues:
        start = pd.to_datetime(issue["time_start"], unit="s", utc=True)
        end = pd.to_datetime(issue["time_end"], unit="s", utc=True)
        print(
            f"- 타입: {issue.get('reason')}, 기간: {start} ~ {end}, "
            f"보정방법: {issue.get('correction')}"
        )


def get_numeric_columns(meter_urn: str) -> list[str]:
    metadata = get_metadata(meter_urn)
    if metadata is None:
        raise ValueError(f"Metadata not found for {meter_urn}")
    if metadata.get("meter_type") == "thermal":
        return THERMAL_NUMERIC_COLUMNS
    return CORE_NUMERIC_COLUMNS


def fetch_joined_data(meter_urn: str) -> pd.DataFrame:
    engine = build_engine()
    df_main = fetch_meter_data(engine, meter_urn)
    validate_columns(df_main, meter_urn)
    df_weather = fetch_weather_data(engine)

    df = df_main.merge(df_weather, on="ts", how="left")
    df = df.sort_values("ts").reset_index(drop=True)

    for column in get_numeric_columns(meter_urn):
        df[column] = pd.to_numeric(df[column], errors="coerce")

    return df


def build_mask_intervals(issues: list[dict[str, Any]]) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    intervals = []
    for issue in issues:
        correction = issue.get("correction")
        if not isinstance(correction, str):
            continue
        if not (correction.startswith("delete") or correction.startswith("substitute")):
            continue
        start = pd.to_datetime(issue["time_start"], unit="s", utc=True)
        end = pd.to_datetime(issue["time_end"], unit="s", utc=True)
        intervals.append((start, end))
    return intervals


def apply_issue_masks(df: pd.DataFrame, intervals: list[tuple[pd.Timestamp, pd.Timestamp]]) -> pd.DataFrame:
    masked = df.copy()
    value_columns = [column for column in masked.columns if column != "ts"]

    for start, end in intervals:
        interval_mask = masked["ts"].between(start, end)
        masked.loc[interval_mask, value_columns] = np.nan

    return masked


def apply_sign_rules(df: pd.DataFrame, metadata: dict[str, Any], issue_intervals: list[tuple[pd.Timestamp, pd.Timestamp]]) -> pd.DataFrame:
    cleaned = df.copy()
    meter_type = metadata.get("meter_type")
    energy_type = metadata.get("energy_type")

    if meter_type == "electric" and energy_type == "consumption":
        for column in ["P", "P1", "P2", "P3"]:
            if column in cleaned.columns:
                cleaned.loc[cleaned[column] < 0, column] = np.nan
    if "W" in cleaned.columns:
        cleaned.loc[cleaned["W"] < 0, "W"] = np.nan
    if "PF" in cleaned.columns:
        cleaned.loc[cleaned["PF"].abs() > 1, "PF"] = np.nan

    return cleaned


def apply_weather_rules(df: pd.DataFrame) -> pd.DataFrame:
    cleaned = df.copy()
    hours = cleaned["ts"].dt.hour

    cleaned.loc[(cleaned["Ta"] < -20) | (cleaned["Ta"] > 45), "Ta"] = np.nan
    cleaned.loc[cleaned["Igm"] < 0, "Igm"] = np.nan
    cleaned.loc[((hours >= 20) | (hours <= 6)) & (cleaned["Igm"] > 10), "Igm"] = np.nan

    return cleaned


def mark_long_gaps(
    df: pd.DataFrame,
    target_col: str,
    threshold: int,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    working = df.copy()
    working["is_valid"] = True
    invalid_segments: list[dict[str, Any]] = []

    if not target_col or target_col not in working.columns:
        return working, invalid_segments

    na_mask = working[target_col].isna()
    if not na_mask.any():
        return working, invalid_segments

    segment_ids = na_mask.ne(na_mask.shift(fill_value=False)).cumsum()
    for _, segment in working.loc[na_mask].groupby(segment_ids[na_mask]):
        if len(segment) < threshold:
            continue
        start_ts = segment["ts"].iloc[0]
        end_ts = segment["ts"].iloc[-1]
        working.loc[segment.index, "is_valid"] = False
        invalid_segments.append(
            {
                "column": target_col,
                "start": start_ts,
                "end": end_ts,
                "length": len(segment),
            }
        )

    return working, invalid_segments


def interpolate_short_gaps(df: pd.DataFrame, columns: list[str], threshold: int) -> pd.DataFrame:
    interpolated = df.copy()

    for column in columns:
        series = interpolated[column]
        interpolated[column] = series.interpolate(
            method="linear",
            limit=threshold - 1,
            limit_area="inside",
        )

    return interpolated


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    enriched = df.copy()

    enriched["hour"] = enriched["ts"].dt.hour
    enriched["month"] = enriched["ts"].dt.month
    enriched["hour_sin"] = np.sin(2 * math.pi * enriched["hour"] / 24)
    enriched["hour_cos"] = np.cos(2 * math.pi * enriched["hour"] / 24)
    enriched["month_sin"] = np.sin(2 * math.pi * enriched["month"] / 12)
    enriched["month_cos"] = np.cos(2 * math.pi * enriched["month"] / 12)
    enriched["is_weekend"] = enriched["ts"].dt.dayofweek.isin([5, 6]).astype(int)

    return enriched


def summarize_invalid_segments(invalid_segments: list[dict[str, Any]]) -> None:
    print(f"is_valid=False 구간 개수: {len(invalid_segments)}")
    for segment in invalid_segments:
        print(
            f"- {segment['column']}: {segment['start']} ~ {segment['end']} "
            f"({segment['length']} rows)"
        )


def plot_preprocessed_p(df_before: pd.DataFrame, df_after: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(16, 6))
    plt.plot(df_before["ts"], df_before["P"], color="red", linewidth=1, label="Before")
    plt.plot(df_after["ts"], df_after["P"], color="blue", linewidth=1, label="After")
    plt.title("H1.Z16 P Before/After Preprocessing")
    plt.xlabel("ts")
    plt.ylabel("P")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def preprocess_meter(
    meter_urn: str,
    print_progress: bool = True,
    print_issue_details: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    if print_progress:
        print(f"[1/5] 데이터 로드 중... ({meter_urn})")
    metadata = get_metadata(meter_urn)
    if metadata is None:
        raise ValueError(f"Metadata not found for {meter_urn}")

    df = fetch_joined_data(meter_urn)
    df_before = df.copy()

    issues: list[dict[str, Any]] = []

    if print_progress:
        print(f"[2/5] 음수 및 전력 규칙 처리 중... ({meter_urn})")
    df = apply_sign_rules(df, metadata, [])

    if print_progress:
        print(f"[3/5] 기상 이상치 처리 중... ({meter_urn})")
    df = apply_weather_rules(df)

    if print_progress:
        print(f"[4/5] 유효성 마킹 중... ({meter_urn})")
    target_col = metadata.get("anomaly_target")
    df, invalid_segments = mark_long_gaps(df, target_col, LONG_GAP_THRESHOLD)

    if print_progress:
        print(f"[5/5] 파생변수 생성 중... ({meter_urn})")
    df = add_derived_features(df)

    return df, df_before, issues, invalid_segments


def preprocess_h1z16(
    print_progress: bool = True,
    print_issue_details: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    return preprocess_meter(
        TARGET_METER_URN,
        print_progress=print_progress,
        print_issue_details=print_issue_details,
    )


def main() -> None:
    df, df_before, _, invalid_segments = preprocess_h1z16(
        print_progress=True,
        print_issue_details=True,
    )

    print("결과 저장 및 출력 중...")
    p_nan_before = df_before["P"].isna().sum()
    p_nan_after = df["P"].isna().sum()
    print(f"P NaN before: {p_nan_before}")
    print(f"P NaN after: {p_nan_after}")
    summarize_invalid_segments(invalid_segments)
    print("df.shape")
    print(df.shape)
    print()
    print("df.head()")
    print(df.head())

    plot_preprocessed_p(
        df_before=df_before,
        df_after=df,
        output_path=OUTPUT_DIR / "h1z16_preprocessing.png",
    )
    print(f"Plot saved to: {OUTPUT_DIR / 'h1z16_preprocessing.png'}")


if __name__ == "__main__":
    main()
