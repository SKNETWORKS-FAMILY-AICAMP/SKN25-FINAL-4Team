from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
OUTPUT_PATH = PROJECT_ROOT / "outputs" / "h1z16_stl_anomaly.png"
DECOMPOSITION_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "h1z16_stl_decomposition.png"
RESULT_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "h1z16_stl_results.csv"
YEAR_VIEW_START = "2023-01-01 00:00:00+00:00"
YEAR_VIEW_END = "2023-12-31 23:59:59+00:00"
YEAR_ANOMALY_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "h1z16_stl_anomaly_2023.png"
YEAR_DECOMPOSITION_OUTPUT_PATH = (
    PROJECT_ROOT / "outputs" / "h1z16_stl_decomposition_2023.png"
)
STL_ROLLING_WINDOW = 24 * 365
STL_MIN_PERIODS = 24 * 30

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from preprocess_h1z16 import preprocess_h1z16


def load_stl_input() -> pd.DataFrame:
    df, _, _, _ = preprocess_h1z16(print_progress=False, print_issue_details=False)
    df = df.loc[df["is_valid"]].copy()
    df = df.loc[df["P"].notna()].copy()
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def run_stl_anomaly_detection(
    df: pd.DataFrame,
    target_col: str,
) -> tuple[pd.DataFrame, int, int]:
    stl = STL(df[target_col], period=24, seasonal=7, robust=True)
    result = stl.fit()

    analyzed = df.copy()
    analyzed["trend"] = result.trend
    analyzed["seasonal"] = result.seasonal
    analyzed["residual"] = result.resid

    residual = analyzed["residual"]
    mean_before = residual.mean()
    std_before = residual.std()
    anomaly_before = (
        (residual < mean_before - 2 * std_before)
        | (residual > mean_before + 2 * std_before)
    )

    analyzed["residual_rolling_mean"] = residual.rolling(
        window=STL_ROLLING_WINDOW,
        min_periods=STL_MIN_PERIODS,
        center=True,
    ).mean()
    analyzed["residual_rolling_std"] = residual.rolling(
        window=STL_ROLLING_WINDOW,
        min_periods=STL_MIN_PERIODS,
        center=True,
    ).std()
    analyzed["residual_lower_2sigma"] = (
        analyzed["residual_rolling_mean"] - 2 * analyzed["residual_rolling_std"]
    )
    analyzed["residual_upper_2sigma"] = (
        analyzed["residual_rolling_mean"] + 2 * analyzed["residual_rolling_std"]
    )

    analyzed["anomaly_stl"] = (
        (residual < analyzed["residual_lower_2sigma"])
        | (residual > analyzed["residual_upper_2sigma"])
    )
    analyzed["anomaly_stl"] = analyzed["anomaly_stl"].fillna(False)

    n_before = int(anomaly_before.sum())
    n_after = int(analyzed["anomaly_stl"].sum())
    print(f"전체 기간 σ 기준 이상 건수: {n_before}건")
    print(f"롤링 365일 σ 기준 이상 건수: {n_after}건")
    print(f"변화: {n_after - n_before:+d}건")

    return analyzed, n_before, n_after


def save_decomposition_plot(df: pd.DataFrame, target_col: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(4, 1, figsize=(16, 10), sharex=True)

    axes[0].plot(df["ts"], df[target_col], color="steelblue", linewidth=1)
    axes[0].set_title(f"Observed {target_col}")
    axes[0].set_ylabel(target_col)

    axes[1].plot(df["ts"], df["trend"], color="darkorange", linewidth=1)
    axes[1].set_title("Trend")
    axes[1].set_ylabel("Trend")

    axes[2].plot(df["ts"], df["seasonal"], color="seagreen", linewidth=1)
    axes[2].set_title("Seasonal")
    axes[2].set_ylabel("Seasonal")

    axes[3].plot(df["ts"], df["residual"], color="gray", linewidth=1)
    axes[3].axhline(0.0, color="black", linestyle="--", linewidth=1)
    axes[3].set_title("Residual")
    axes[3].set_ylabel("Residual")
    axes[3].set_xlabel("ts")

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)


def save_plot(df: pd.DataFrame, target_col: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    anomaly_df = df.loc[df["anomaly_stl"]]

    fig, axes = plt.subplots(2, 1, figsize=(16, 8), sharex=True)

    axes[0].plot(df["ts"], df[target_col], color="steelblue", linewidth=1, label=target_col)
    axes[0].scatter(
        anomaly_df["ts"],
        anomaly_df[target_col],
        color="red",
        s=10,
        label="Anomaly",
        zorder=3,
    )
    axes[0].set_title(f"H1.Z16 {target_col} with STL Anomalies")
    axes[0].set_ylabel(target_col)
    axes[0].legend()

    axes[1].plot(df["ts"], df["residual"], color="gray", linewidth=1, label="Residual")
    axes[1].plot(
        df["ts"],
        df["residual_rolling_mean"],
        color="black",
        linestyle="--",
        linewidth=1,
        label="Rolling Mean",
    )
    axes[1].plot(
        df["ts"],
        df["residual_lower_2sigma"],
        color="red",
        linewidth=1,
        label="-2σ",
    )
    axes[1].plot(
        df["ts"],
        df["residual_upper_2sigma"],
        color="red",
        linewidth=1,
        label="+2σ",
    )
    axes[1].set_title("STL Residual with Rolling ±2σ Bounds")
    axes[1].set_xlabel("ts")
    axes[1].set_ylabel("Residual")
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)


def save_results(df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)


def filter_year_view(df: pd.DataFrame) -> pd.DataFrame:
    start = pd.Timestamp(YEAR_VIEW_START)
    end = pd.Timestamp(YEAR_VIEW_END)
    return df.loc[df["ts"].between(start, end)].copy()


def run_stl(
    df: pd.DataFrame | None = None,
    target_col: str = "P",
    save_plot_file: bool = True,
) -> pd.DataFrame:
    if df is None:
        df = load_stl_input()
    else:
        df = df.loc[df["is_valid"]].copy() if "is_valid" in df.columns else df.copy()
        df = df.loc[df[target_col].notna()].copy()
        df = df.sort_values("ts").reset_index(drop=True)

    analyzed, _, _ = run_stl_anomaly_detection(df, target_col)

    if save_plot_file:
        save_decomposition_plot(analyzed, target_col, DECOMPOSITION_OUTPUT_PATH)
        save_plot(analyzed, target_col, OUTPUT_PATH)
        year_df = filter_year_view(analyzed)
        if not year_df.empty:
            save_decomposition_plot(year_df, target_col, YEAR_DECOMPOSITION_OUTPUT_PATH)
            save_plot(year_df, target_col, YEAR_ANOMALY_OUTPUT_PATH)

    return analyzed


def main() -> None:
    analyzed = run_stl(df=None, target_col="P", save_plot_file=True)
    save_results(
        analyzed[["ts", "P", "trend", "seasonal", "residual", "anomaly_stl"]],
        RESULT_OUTPUT_PATH,
    )

    anomaly_df = analyzed.loc[analyzed["anomaly_stl"], ["ts", "P", "residual"]].copy()
    anomaly_ratio = len(anomaly_df) / len(analyzed) if len(analyzed) else 0.0

    print(f"전체 행 수: {len(analyzed)}")
    print(f"이상 탐지 개수: {len(anomaly_df)} ({anomaly_ratio:.2%})")
    print("이상 구간 상위 10개:")
    print(anomaly_df.head(10).to_string(index=False))

    print(f"분해 플롯 저장: {DECOMPOSITION_OUTPUT_PATH}")
    print(f"이상탐지 플롯 저장: {OUTPUT_PATH}")
    print(f"2023 분해 플롯 저장: {YEAR_DECOMPOSITION_OUTPUT_PATH}")
    print(f"2023 이상탐지 플롯 저장: {YEAR_ANOMALY_OUTPUT_PATH}")
    print(f"결과 저장: {RESULT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
