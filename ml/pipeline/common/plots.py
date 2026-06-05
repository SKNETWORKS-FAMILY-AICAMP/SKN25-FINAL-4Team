"""계량기별 PNG 그래프 생성. 서비스화 시 이 파일만 제거하면 됨."""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd


def _actual_pred_cols(horizon: int):
    actual = [f"actual_t_plus_{s}" for s in range(1, horizon + 1)]
    pred   = [f"pred_t_plus_{s}"   for s in range(1, horizon + 1)]
    return actual, pred


def save_plots(
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
    horizon: int,
    meter_urn: str,
    output_dir: Path,
    threshold: float,
) -> None:
    """val + test 구간 actual vs predicted, anomaly score PNG 저장."""

    for split, df in [("val", val_df), ("test", test_df)]:
        _plot_actual_vs_pred(df, horizon, meter_urn, split, threshold, output_dir)
        _plot_anomaly_score(df, meter_urn, split, threshold, output_dir)


def _plot_actual_vs_pred(
    df: pd.DataFrame,
    horizon: int,
    meter_urn: str,
    split: str,
    threshold: float,
    output_dir: Path,
) -> None:
    actual_cols, pred_cols = _actual_pred_cols(horizon)

    # step 1 기준으로 그래프 (대표값)
    ts      = pd.to_datetime(df["target_end_ts"], utc=True)
    actual  = df[actual_cols[0]].to_numpy(dtype=float)
    pred    = df[pred_cols[0]].to_numpy(dtype=float)
    is_anom = df["is_anomaly"].to_numpy(dtype=bool)

    fig, axes = plt.subplots(2, 1, figsize=(16, 8), sharex=True)

    # 위: actual vs predicted
    ax = axes[0]
    ax.plot(ts, actual, label="Actual",    color="#2196F3", linewidth=0.8, alpha=0.9)
    ax.plot(ts, pred,   label="Predicted", color="#FF5722", linewidth=0.8, alpha=0.7)
    if is_anom.any():
        ax.scatter(ts[is_anom], actual[is_anom], color="red", s=8, zorder=5, label="Anomaly")
    ax.set_ylabel("P (kWh)")
    ax.set_title(f"{meter_urn} | {split} | horizon={horizon}h | Actual vs Predicted (t+1)")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(True, alpha=0.3)

    # 아래: residual
    ax2 = axes[1]
    residual = actual - pred
    ax2.bar(ts, residual, color=np.where(residual > 0, "#4CAF50", "#F44336"),
            width=0.04, alpha=0.6)
    ax2.axhline(0, color="black", linewidth=0.8)
    ax2.set_ylabel("Residual (kWh)")
    ax2.set_xlabel("Time (UTC)")
    ax2.grid(True, alpha=0.3)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.tight_layout()
    path = output_dir / f"actual_vs_predicted_{split}.png"
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def _plot_anomaly_score(
    df: pd.DataFrame,
    meter_urn: str,
    split: str,
    threshold: float,
    output_dir: Path,
) -> None:
    ts    = pd.to_datetime(df["target_end_ts"], utc=True)
    score = df["anomaly_score_mae"].to_numpy(dtype=float)
    is_anom = df["is_anomaly"].to_numpy(dtype=bool)

    fig, ax = plt.subplots(figsize=(16, 4))
    ax.plot(ts, score, color="#607D8B", linewidth=0.7, alpha=0.8, label="Anomaly Score (MAE)")
    ax.axhline(threshold, color="red", linewidth=1.2, linestyle="--",
               label=f"Threshold ({threshold:.1f})")
    if is_anom.any():
        ax.scatter(ts[is_anom], score[is_anom], color="red", s=10, zorder=5, label="Anomaly")
    ax.set_ylabel("MAE Score")
    ax.set_xlabel("Time (UTC)")
    ax.set_title(f"{meter_urn} | {split} | Anomaly Score")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.tight_layout()
    path = output_dir / f"anomaly_score_{split}.png"
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
