from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "eda"
REPRESENTATIVE_METERS = [
    "H1.Z16",  # electric cooling
    "H1.Z20",  # electric production
    "V.K21",   # thermal cooling
    "H2.Z61",  # server load
]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config.meter_metadata import get_metadata
from scripts.preprocess_h1z16 import preprocess_meter


def get_plot_columns(df: pd.DataFrame, metadata: dict[str, object]) -> list[str]:
    meter_type = metadata.get("meter_type")
    if meter_type == "thermal":
        candidates = ["Tdiff", "qv", "Ta", "Igm"]
    else:
        candidates = ["P", "W", "PF", "Ta", "Igm"]
    return [column for column in candidates if column in df.columns]


def build_summary_row(
    meter_urn: str,
    df: pd.DataFrame,
    df_before: pd.DataFrame,
    invalid_segments: list[dict[str, object]],
    metadata: dict[str, object],
) -> dict[str, object]:
    target_col = metadata.get("anomaly_target")
    target_nonnull_ratio = None
    target_mean = None
    target_std = None
    target_nan_before = None
    target_nan_after = None

    if isinstance(target_col, str) and target_col in df.columns:
        target_nonnull_ratio = float(df[target_col].notna().mean())
        target_mean = float(df[target_col].mean())
        target_std = float(df[target_col].std())
        if target_col in df_before.columns:
            target_nan_before = int(df_before[target_col].isna().sum())
        target_nan_after = int(df[target_col].isna().sum())

    return {
        "meter_urn": meter_urn,
        "meter_type": metadata.get("meter_type"),
        "group_name": metadata.get("group_name"),
        "anomaly_target": target_col,
        "rows": int(len(df)),
        "valid_rows": int(df["is_valid"].sum()),
        "valid_ratio": float(df["is_valid"].mean()),
        "start_ts": df["ts"].min(),
        "end_ts": df["ts"].max(),
        "issue_count": None,
        "invalid_segment_count": len(invalid_segments),
        "target_nonnull_ratio": target_nonnull_ratio,
        "target_mean": target_mean,
        "target_std": target_std,
        "target_nan_before": target_nan_before,
        "target_nan_after": target_nan_after,
    }


def save_timeseries_plot(
    meter_urn: str,
    df: pd.DataFrame,
    issues: list[dict[str, object]],
    metadata: dict[str, object],
    output_path: Path,
) -> None:
    columns = get_plot_columns(df, metadata)
    if not columns:
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(len(columns), 1, figsize=(16, 3.5 * len(columns)), sharex=True)
    if len(columns) == 1:
        axes = [axes]

    for ax, column in zip(axes, columns):
        ax.plot(df["ts"], df[column], linewidth=0.8, color="steelblue")
        ax.set_ylabel(column)
        ax.set_title(f"{meter_urn} {column} Timeseries")

        for issue in issues:
            start = pd.to_datetime(issue["time_start"], unit="s", utc=True)
            end = pd.to_datetime(issue["time_end"], unit="s", utc=True)
            ax.axvspan(start, end, color="tomato", alpha=0.08)

    axes[-1].set_xlabel("ts")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)


def save_hourly_profile_plot(
    meter_urn: str,
    df: pd.DataFrame,
    metadata: dict[str, object],
    output_path: Path,
) -> None:
    target_col = metadata.get("anomaly_target")
    if not isinstance(target_col, str) or target_col not in df.columns:
        return

    profile_df = (
        df.groupby("hour", dropna=False)[target_col]
        .mean()
        .reset_index()
        .sort_values("hour")
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(10, 5))
    plt.plot(profile_df["hour"], profile_df[target_col], marker="o", color="seagreen")
    plt.title(f"{meter_urn} Hourly Mean Profile ({target_col})")
    plt.xlabel("hour")
    plt.ylabel(target_col)
    plt.xticks(range(0, 24, 2))
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def save_monthly_profile_plot(
    meter_urn: str,
    df: pd.DataFrame,
    metadata: dict[str, object],
    output_path: Path,
) -> None:
    target_col = metadata.get("anomaly_target")
    if not isinstance(target_col, str) or target_col not in df.columns:
        return

    profile_df = (
        df.groupby("month", dropna=False)[target_col]
        .mean()
        .reset_index()
        .sort_values("month")
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(10, 5))
    plt.bar(profile_df["month"], profile_df[target_col], color="slateblue")
    plt.title(f"{meter_urn} Monthly Mean Profile ({target_col})")
    plt.xlabel("month")
    plt.ylabel(target_col)
    plt.xticks(range(1, 13))
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def save_correlation_heatmap(
    meter_urn: str,
    df: pd.DataFrame,
    metadata: dict[str, object],
    output_path: Path,
) -> None:
    if metadata.get("meter_type") == "thermal":
        candidates = ["Tdiff", "qv", "Tvl", "Trl", "Ta", "Igm"]
    else:
        candidates = ["P", "PF", "I1", "I2", "I3", "P1", "P2", "P3", "Ta", "Igm"]
    columns = [column for column in candidates if column in df.columns and df[column].notna().any()]
    if len(columns) < 2:
        return

    corr = df[columns].corr(numeric_only=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 6))
    im = ax.imshow(corr.values, cmap="coolwarm", vmin=-1, vmax=1)
    ax.set_xticks(range(len(columns)))
    ax.set_yticks(range(len(columns)))
    ax.set_xticklabels(columns, rotation=45, ha="right")
    ax.set_yticklabels(columns)
    ax.set_title(f"{meter_urn} Correlation Heatmap")
    fig.colorbar(im, ax=ax, shrink=0.8)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)


def run_meter_profile(meter_urn: str) -> dict[str, object]:
    metadata = get_metadata(meter_urn)
    if metadata is None:
        raise ValueError(f"Metadata not found for {meter_urn}")

    df, df_before, issues, invalid_segments = preprocess_meter(
        meter_urn,
        print_progress=False,
        print_issue_details=False,
    )
    summary = build_summary_row(meter_urn, df, df_before, invalid_segments, metadata)
    summary["issue_count"] = len(issues)

    safe_name = meter_urn.replace(".", "_")
    meter_dir = OUTPUT_DIR / safe_name
    meter_dir.mkdir(parents=True, exist_ok=True)

    save_timeseries_plot(meter_urn, df, issues, metadata, meter_dir / f"{safe_name}_timeseries.png")
    save_hourly_profile_plot(meter_urn, df, metadata, meter_dir / f"{safe_name}_hourly_profile.png")
    save_monthly_profile_plot(meter_urn, df, metadata, meter_dir / f"{safe_name}_monthly_profile.png")
    save_correlation_heatmap(meter_urn, df, metadata, meter_dir / f"{safe_name}_corr.png")

    describe_columns = [column for column in get_plot_columns(df, metadata) if column in df.columns]
    if describe_columns:
        df[describe_columns].describe().to_csv(meter_dir / f"{safe_name}_describe.csv", encoding="utf-8-sig")
    pd.DataFrame([summary]).to_csv(meter_dir / f"{safe_name}_summary.csv", index=False, encoding="utf-8-sig")

    return summary


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, object]] = []

    print("EDA 대상 대표 계량기:")
    for meter_urn in REPRESENTATIVE_METERS:
        print(f"- {meter_urn}")

    for meter_urn in REPRESENTATIVE_METERS:
        print(f"[EDA] {meter_urn} 처리 중...")
        summary = run_meter_profile(meter_urn)
        summaries.append(summary)
        print(
            f"  rows={summary['rows']}, valid_rows={summary['valid_rows']}, "
            f"issue_count={summary['issue_count']}, invalid_segments={summary['invalid_segment_count']}"
        )

    overview_df = pd.DataFrame(summaries)
    overview_path = OUTPUT_DIR / "representative_meter_overview.csv"
    overview_df.to_csv(overview_path, index=False, encoding="utf-8-sig")

    print()
    print("대표 계량기 EDA 요약:")
    print(
        overview_df[
            [
                "meter_urn",
                "meter_type",
                "group_name",
                "anomaly_target",
                "rows",
                "valid_rows",
                "issue_count",
                "invalid_segment_count",
                "target_nonnull_ratio",
            ]
        ].to_string(index=False)
    )
    print()
    print(f"저장 경로: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
