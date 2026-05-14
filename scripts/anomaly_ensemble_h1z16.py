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
OUTPUT_PATH = PROJECT_ROOT / "outputs" / "h1z16_ensemble_anomaly.png"
RESULT_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "h1z16_ensemble_results.csv"
STL_RESULT_PATH = PROJECT_ROOT / "outputs" / "h1z16_stl_results.csv"
IF_RESULT_PATH = PROJECT_ROOT / "outputs" / "h1z16_if_results.csv"
LSTM_RESULT_PATH = PROJECT_ROOT / "outputs" / "h1z16_lstm_results.csv"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

def load_model_results() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    missing_paths = [path for path in [STL_RESULT_PATH, IF_RESULT_PATH, LSTM_RESULT_PATH] if not path.exists()]
    if missing_paths:
        missing_text = ", ".join(str(path) for path in missing_paths)
        raise FileNotFoundError(f"모델 결과 파일이 없습니다: {missing_text}")

    stl_df = pd.read_csv(STL_RESULT_PATH, parse_dates=["ts"])
    if_df = pd.read_csv(IF_RESULT_PATH, parse_dates=["ts"])
    lstm_df = pd.read_csv(LSTM_RESULT_PATH, parse_dates=["ts"])
    return stl_df, if_df, lstm_df


def run_ensemble(
    stl_df: pd.DataFrame,
    if_df: pd.DataFrame,
    lstm_df: pd.DataFrame,
) -> pd.DataFrame:
    stl_df = stl_df[["ts", "P", "anomaly_stl"]].copy()
    if_df = if_df[["ts", "anomaly_if"]].copy()
    lstm_df = lstm_df[["ts", "anomaly_lstm"]].copy()

    df = stl_df.merge(if_df, on="ts", how="outer")
    df = df.merge(lstm_df, on="ts", how="outer")
    df = df.sort_values("ts").reset_index(drop=True)

    for column in ["anomaly_stl", "anomaly_if", "anomaly_lstm"]:
        df[column] = df[column].fillna(False).astype(bool)

    df["anomaly_count"] = (
        df["anomaly_stl"].astype(int)
        + df["anomaly_if"].astype(int)
        + df["anomaly_lstm"].astype(int)
    )
    df["ensemble_level"] = np.select(
        [df["anomaly_count"] == 3, df["anomaly_count"] == 2],
        ["DANGER", "WARNING"],
        default="NORMAL",
    )
    df["status"] = df["ensemble_level"]
    return df


def build_ensemble_df() -> pd.DataFrame:
    stl_df, if_df, lstm_df = load_model_results()
    return run_ensemble(stl_df, if_df, lstm_df)


def save_plot(df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    warning_df = df.loc[df["ensemble_level"] == "WARNING"]
    danger_df = df.loc[df["ensemble_level"] == "DANGER"]

    count_color = {0: "gray", 1: "#f1c40f", 2: "#e67e22", 3: "#e74c3c"}
    bar_colors = [count_color[count] for count in df["anomaly_count"]]

    fig, axes = plt.subplots(2, 1, figsize=(16, 8), sharex=True)

    axes[0].plot(df["ts"], df["P"], color="steelblue", linewidth=1, label="NORMAL")
    axes[0].scatter(warning_df["ts"], warning_df["P"], color="#e67e22", s=10, label="WARNING", zorder=3)
    axes[0].scatter(danger_df["ts"], danger_df["P"], color="#e74c3c", s=12, label="DANGER", zorder=4)
    axes[0].set_title("H1.Z16 Ensemble Anomaly Detection")
    axes[0].set_ylabel("P")
    axes[0].legend()

    axes[1].bar(df["ts"], df["anomaly_count"], color=bar_colors, width=0.03)
    axes[1].set_title("Ensemble Anomaly Count")
    axes[1].set_xlabel("ts")
    axes[1].set_ylabel("Count")
    axes[1].set_ylim(-0.1, 3.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)


def save_results(df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)


def main() -> None:
    df = build_ensemble_df()
    save_results(
        df[["ts", "P", "anomaly_stl", "anomaly_if", "anomaly_lstm", "anomaly_count", "ensemble_level"]],
        RESULT_OUTPUT_PATH,
    )
    total_count = len(df)
    danger_df = df.loc[df["ensemble_level"] == "DANGER", ["ts", "P", "anomaly_count"]].copy()
    warning_df = df.loc[df["ensemble_level"] == "WARNING", ["ts", "P", "anomaly_count"]].copy()
    normal_df = df.loc[df["ensemble_level"] == "NORMAL"]

    print(f"전체 행 수: {total_count}")
    print(f"DANGER 개수: {len(danger_df)} ({len(danger_df) / total_count:.2%})")
    print(f"WARNING 개수: {len(warning_df)} ({len(warning_df) / total_count:.2%})")
    print(f"NORMAL 개수: {len(normal_df)} ({len(normal_df) / total_count:.2%})")
    print("DANGER 구간 상위 10개:")
    print(danger_df.head(10).to_string(index=False))
    print("WARNING 구간 상위 10개:")
    print(warning_df.head(10).to_string(index=False))

    save_plot(df, OUTPUT_PATH)
    print(f"플롯 저장: {OUTPUT_PATH}")
    print(f"결과 저장: {RESULT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
