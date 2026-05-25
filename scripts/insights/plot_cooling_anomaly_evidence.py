"""Create visual evidence figures for Cooling anomaly candidates.

Figures:
1. Nature-described cooling load scaling issue window around 2023-09-20.
2. A separate over-electricity efficiency-review candidate around 2023-08-21.
3. Cooling thermal/electric relation scatter with highlighted candidate windows.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "insights"))

from run_cooling_chp_anomaly_quantification_1h import (  # noqa: E402
    SEVERE_Z,
    fit_relation_model,
    positive_quantile,
)
from run_equipment_relation_strength_1h import fetch_reduced_1h  # noqa: E402

OUT = ROOT / "outputs" / "figures" / "equipment_anomaly_validation"
TABLE_OUT = ROOT / "outputs" / "tables" / "equipment_anomaly_validation"


def prepare_cooling_frame() -> tuple[pd.DataFrame, float]:
    data = fetch_reduced_1h()
    model = fit_relation_model(
        data,
        "Cooling efficiency",
        "cooling_elec_P",
        ["cooling_thermal_P", "Ta", "hour_sin", "hour_cos", "month_sin", "month_cos"],
    )
    frame = model.frame.copy()
    frame["cooling_elec_pred"] = frame["pred"]
    frame["cooling_residual"] = frame["residual"]
    frame["cooling_abs_robust_z"] = frame["abs_robust_z"]
    frame["cooling_thermal_kW"] = frame["cooling_thermal_P"] / 1000.0
    frame["cooling_elec_kW"] = frame["cooling_elec_P"] / 1000.0
    frame["cooling_elec_pred_kW"] = frame["cooling_elec_pred"] / 1000.0
    frame["cooling_residual_kW"] = frame["cooling_residual"] / 1000.0
    active_threshold = positive_quantile(frame["cooling_thermal_P"], 0.50)
    return frame, active_threshold


def window(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    start_ts = pd.Timestamp(start, tz="Europe/Berlin")
    end_ts = pd.Timestamp(end, tz="Europe/Berlin")
    return frame[(frame["local_ts"] >= start_ts) & (frame["local_ts"] <= end_ts)].copy()


def plot_window(frame: pd.DataFrame, title: str, start: str, end: str, highlight_start: str, highlight_end: str, path: Path) -> pd.DataFrame:
    w = window(frame, start, end)
    h_start = pd.Timestamp(highlight_start, tz="Europe/Berlin")
    h_end = pd.Timestamp(highlight_end, tz="Europe/Berlin")
    highlight = w[(w["local_ts"] >= h_start) & (w["local_ts"] <= h_end)].copy()

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(4, 1, figsize=(15, 11), sharex=True)
    fig.suptitle(title, fontsize=15, fontweight="bold")

    axes[0].plot(w["local_ts"], w["cooling_thermal_kW"], color="#0066cc", linewidth=2, label="Cooling thermal P")
    axes[0].scatter(highlight["local_ts"], highlight["cooling_thermal_kW"], color="#d62728", s=35, label="highlighted candidate")
    axes[0].set_ylabel("Thermal P (kW)")
    axes[0].legend(loc="upper left")

    axes[1].plot(w["local_ts"], w["cooling_elec_kW"], color="#2ca02c", linewidth=2, label="Actual cooling electric P")
    axes[1].plot(w["local_ts"], w["cooling_elec_pred_kW"], color="#ff7f0e", linewidth=2, linestyle="--", label="Expected electric P from relation model")
    axes[1].scatter(highlight["local_ts"], highlight["cooling_elec_kW"], color="#d62728", s=35)
    axes[1].set_ylabel("Electric P (kW)")
    axes[1].legend(loc="upper left")

    axes[2].bar(w["local_ts"], w["cooling_residual_kW"], width=0.035, color=np.where(w["cooling_residual_kW"] >= 0, "#9467bd", "#8c564b"))
    axes[2].axhline(0, color="black", linewidth=0.8)
    axes[2].set_ylabel("Actual - expected (kW)")

    axes[3].plot(w["local_ts"], w["cooling_abs_robust_z"], color="#d62728", linewidth=2, label="abs robust z")
    axes[3].axhline(SEVERE_Z, color="black", linestyle="--", linewidth=1, label=f"severe threshold = {SEVERE_Z}")
    axes[3].plot(w["local_ts"], w["Ta"], color="#17becf", linewidth=1.5, alpha=0.8, label="Outside air temp (°C)")
    axes[3].set_ylabel("z / °C")
    axes[3].legend(loc="upper left")

    for ax in axes:
        ax.axvspan(h_start, h_end, color="#d62728", alpha=0.12)
        ax.grid(True, alpha=0.3)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M", tz=w["local_ts"].dt.tz))
    axes[-1].set_xlabel("Local time (Europe/Berlin)")
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return highlight


def plot_scatter(frame: pd.DataFrame, path: Path) -> None:
    base = frame.dropna(subset=["cooling_thermal_kW", "cooling_elec_kW", "cooling_abs_robust_z"]).copy()
    normal = base[base["cooling_abs_robust_z"] < 4]
    issue = window(base, "2023-09-19 18:00", "2023-09-20 08:00")
    over = window(base, "2023-08-21 06:00", "2023-08-22 18:00")

    rng = np.random.default_rng(42)
    if len(normal) > 12000:
        normal = normal.sample(12000, random_state=42)

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(11, 8))
    ax.scatter(normal["cooling_thermal_kW"], normal["cooling_elec_kW"], s=8, alpha=0.12, color="#4c78a8", label="normal-ish hours (abs z < 4 sample)")
    ax.scatter(issue["cooling_thermal_kW"], issue["cooling_elec_kW"], s=55, color="#d62728", label="2023-09-19/20 scaling-like candidate")
    ax.scatter(over["cooling_thermal_kW"], over["cooling_elec_kW"], s=55, color="#ff7f0e", label="2023-08-21 over-electricity candidate")
    ax.set_xlabel("Cooling thermal P (kW)")
    ax.set_ylabel("Cooling electric P (kW)")
    ax.set_title("Cooling relation: thermal load vs electric power")
    ax.legend(loc="upper left", frameon=True)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def summarize_window(name: str, h: pd.DataFrame) -> dict:
    return {
        "window": name,
        "start_local_ts": h["local_ts"].min(),
        "end_local_ts": h["local_ts"].max(),
        "hours": int(len(h)),
        "thermal_kW_min": float(h["cooling_thermal_kW"].min()),
        "thermal_kW_max": float(h["cooling_thermal_kW"].max()),
        "electric_actual_kW_min": float(h["cooling_elec_kW"].min()),
        "electric_actual_kW_max": float(h["cooling_elec_kW"].max()),
        "electric_expected_kW_min": float(h["cooling_elec_pred_kW"].min()),
        "electric_expected_kW_max": float(h["cooling_elec_pred_kW"].max()),
        "residual_kW_min": float(h["cooling_residual_kW"].min()),
        "residual_kW_max": float(h["cooling_residual_kW"].max()),
        "abs_robust_z_max": float(h["cooling_abs_robust_z"].max()),
        "Ta_min": float(h["Ta"].min()),
        "Ta_max": float(h["Ta"].max()),
    }


def main() -> None:
    frame, active_threshold = prepare_cooling_frame()
    issue_path = OUT / "cooling_scaling_issue_2023_09_19_20.png"
    over_path = OUT / "cooling_over_electricity_2023_08_21.png"
    scatter_path = OUT / "cooling_relation_scatter_highlighted_candidates.png"

    h_issue = plot_window(
        frame,
        "Cooling candidate: scaling-like thermal relation break around 2023-09-19/20",
        "2023-09-18 00:00",
        "2023-09-21 12:00",
        "2023-09-19 20:00",
        "2023-09-20 07:00",
        issue_path,
    )
    h_over = plot_window(
        frame,
        "Cooling candidate: electricity above expected around 2023-08-21",
        "2023-08-20 00:00",
        "2023-08-23 12:00",
        "2023-08-21 08:00",
        "2023-08-21 23:00",
        over_path,
    )
    plot_scatter(frame, scatter_path)

    summary = pd.DataFrame(
        [
            summarize_window("scaling_like_2023_09_19_20", h_issue),
            summarize_window("over_electricity_2023_08_21", h_over),
        ]
    )
    TABLE_OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(TABLE_OUT / "07_cooling_visual_window_summary.csv", index=False)
    print(
        {
            "figures": [str(issue_path), str(over_path), str(scatter_path)],
            "summary_csv": str(TABLE_OUT / "07_cooling_visual_window_summary.csv"),
            "active_threshold_thermal_P": active_threshold,
        }
    )


if __name__ == "__main__":
    main()
