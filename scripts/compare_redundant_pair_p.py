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
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "redundant_pair_compare"
DEFAULT_METER = "H1.Z20"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config.meter_metadata import get_metadata, get_redundant_pair
from scripts.preprocess_h1z16 import preprocess_meter


def load_pair_p(main_meter_urn: str) -> tuple[str, pd.DataFrame]:
    redundant_pair = get_redundant_pair(main_meter_urn)
    if redundant_pair is None:
        raise ValueError(f"{main_meter_urn} does not have a redundant_pair in metadata")

    main_meta = get_metadata(main_meter_urn)
    pair_meta = get_metadata(redundant_pair)
    if main_meta is None or pair_meta is None:
        raise ValueError("Metadata not found for one of the redundant pair meters")

    main_df, _, _, _ = preprocess_meter(
        main_meter_urn,
        print_progress=False,
        print_issue_details=False,
    )
    pair_df, _, _, _ = preprocess_meter(
        redundant_pair,
        print_progress=False,
        print_issue_details=False,
    )

    if "P" not in main_df.columns or "P" not in pair_df.columns:
        raise ValueError("P column not found in one of the pair dataframes")

    compare_df = (
        main_df[["ts", "P"]]
        .rename(columns={"P": f"P_{main_meter_urn}"})
        .merge(
            pair_df[["ts", "P"]].rename(columns={"P": f"P_{redundant_pair}"}),
            on="ts",
            how="inner",
        )
        .sort_values("ts")
        .reset_index(drop=True)
    )

    compare_df["abs_diff"] = (
        compare_df[f"P_{main_meter_urn}"] - compare_df[f"P_{redundant_pair}"]
    ).abs()
    compare_df["signed_diff"] = (
        compare_df[f"P_{main_meter_urn}"] - compare_df[f"P_{redundant_pair}"]
    )
    denom = compare_df[[f"P_{main_meter_urn}", f"P_{redundant_pair}"]].abs().max(axis=1)
    compare_df["rel_diff_pct"] = np.where(
        denom > 0,
        compare_df["abs_diff"] / denom * 100,
        np.nan,
    )
    return redundant_pair, compare_df


def summarize_pair(main_meter_urn: str, pair_meter_urn: str, compare_df: pd.DataFrame) -> pd.Series:
    corr = compare_df[[f"P_{main_meter_urn}", f"P_{pair_meter_urn}"]].corr().iloc[0, 1]
    return pd.Series(
        {
            "main_meter_urn": main_meter_urn,
            "pair_meter_urn": pair_meter_urn,
            "common_rows": int(len(compare_df)),
            "main_mean_P": float(compare_df[f"P_{main_meter_urn}"].mean()),
            "pair_mean_P": float(compare_df[f"P_{pair_meter_urn}"].mean()),
            "mean_abs_diff": float(compare_df["abs_diff"].mean()),
            "median_abs_diff": float(compare_df["abs_diff"].median()),
            "max_abs_diff": float(compare_df["abs_diff"].max()),
            "mean_rel_diff_pct": float(compare_df["rel_diff_pct"].mean()),
            "p95_rel_diff_pct": float(compare_df["rel_diff_pct"].quantile(0.95)),
            "corr": float(corr),
        }
    )


def save_pair_plot(main_meter_urn: str, pair_meter_urn: str, compare_df: pd.DataFrame) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{main_meter_urn.replace('.', '_')}__{pair_meter_urn.replace('.', '_')}"
    output_path = OUTPUT_DIR / f"{safe_name}_P_compare.png"

    fig, axes = plt.subplots(3, 1, figsize=(16, 11), sharex=False)

    axes[0].plot(compare_df["ts"], compare_df[f"P_{main_meter_urn}"], label=main_meter_urn, linewidth=0.9)
    axes[0].plot(compare_df["ts"], compare_df[f"P_{pair_meter_urn}"], label=pair_meter_urn, linewidth=0.9)
    axes[0].set_title(f"{main_meter_urn} vs {pair_meter_urn} - P Timeseries")
    axes[0].set_ylabel("P")
    axes[0].legend()

    axes[1].plot(compare_df["ts"], compare_df["signed_diff"], color="tomato", linewidth=0.8)
    axes[1].axhline(0, color="black", linestyle="--", linewidth=0.8)
    axes[1].set_title("Signed Difference")
    axes[1].set_ylabel("P diff")

    axes[2].scatter(
        compare_df[f"P_{main_meter_urn}"],
        compare_df[f"P_{pair_meter_urn}"],
        s=8,
        alpha=0.35,
        color="slateblue",
    )
    min_val = float(
        min(
            compare_df[f"P_{main_meter_urn}"].min(),
            compare_df[f"P_{pair_meter_urn}"].min(),
        )
    )
    max_val = float(
        max(
            compare_df[f"P_{main_meter_urn}"].max(),
            compare_df[f"P_{pair_meter_urn}"].max(),
        )
    )
    axes[2].plot([min_val, max_val], [min_val, max_val], color="gray", linestyle="--", linewidth=1)
    axes[2].set_title("Scatter Comparison")
    axes[2].set_xlabel(main_meter_urn)
    axes[2].set_ylabel(pair_meter_urn)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def main() -> None:
    main_meter_urn = DEFAULT_METER
    pair_meter_urn, compare_df = load_pair_p(main_meter_urn)
    summary = summarize_pair(main_meter_urn, pair_meter_urn, compare_df)
    plot_path = save_pair_plot(main_meter_urn, pair_meter_urn, compare_df)

    print("redundant pair summary")
    print(summary.to_string())
    print()
    print("largest absolute differences top 10")
    print(
        compare_df.sort_values("abs_diff", ascending=False)[
            [
                "ts",
                f"P_{main_meter_urn}",
                f"P_{pair_meter_urn}",
                "signed_diff",
                "abs_diff",
                "rel_diff_pct",
            ]
        ]
        .head(10)
        .to_string(index=False)
    )
    print()
    print(f"plot saved: {plot_path}")


if __name__ == "__main__":
    main()
