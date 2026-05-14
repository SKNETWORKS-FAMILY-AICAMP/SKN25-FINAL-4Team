from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
OUTPUT_PATH = PROJECT_ROOT / "outputs" / "h1z16_if_anomaly.png"
RESULT_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "h1z16_if_results.csv"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from preprocess_h1z16 import preprocess_h1z16


FEATURE_CANDIDATES = [
    "P",
    "PF",
    "PF1",
    "PF2",
    "PF3",
    "I1",
    "I2",
    "I3",
    "P1",
    "P2",
    "P3",
    "Ta",
    "Igm",
    "hour_sin",
    "hour_cos",
    "month_sin",
    "month_cos",
    "is_weekend",
]


def load_if_input() -> pd.DataFrame:
    df, _, _, _ = preprocess_h1z16(
        print_progress=False,
        print_issue_details=False,
    )
    df = df.loc[df["is_valid"]].copy()
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def select_features(df: pd.DataFrame) -> list[str]:
    selected = []
    for column in FEATURE_CANDIDATES:
        if column not in df.columns:
            continue
        na_ratio = df[column].isna().mean()
        if na_ratio <= 0.5:
            selected.append(column)
    return selected


def prepare_feature_frame(df: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    return df.dropna(subset=feature_columns).copy()


def run_isolation_forest(
    df: pd.DataFrame,
    feature_columns: list[str],
) -> tuple[pd.DataFrame, float]:
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(df[feature_columns])

    model = IsolationForest(
        contamination=0.02,
        random_state=42,
        n_estimators=100,
    )
    model.fit(X_scaled)

    analyzed = df.copy()
    scores = model.score_samples(X_scaled)
    threshold = np.quantile(scores, 0.02)

    analyzed["anomaly_score"] = scores
    analyzed["anomaly_if"] = scores <= threshold

    return analyzed, threshold


def save_plot(df: pd.DataFrame, threshold: float, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    anomaly_df = df.loc[df["anomaly_if"]]

    fig, axes = plt.subplots(2, 1, figsize=(16, 8), sharex=True)

    axes[0].plot(df["ts"], df["P"], color="steelblue", linewidth=1, label="P")
    axes[0].scatter(
        anomaly_df["ts"],
        anomaly_df["P"],
        color="red",
        s=10,
        label="Anomaly",
        zorder=3,
    )
    axes[0].set_title("H1.Z16 P with Isolation Forest Anomalies")
    axes[0].set_ylabel("P")
    axes[0].legend()

    axes[1].plot(df["ts"], df["anomaly_score"], color="gray", linewidth=1, label="IF score")
    axes[1].axhline(threshold, color="red", linestyle="--", linewidth=1, label="Threshold")
    axes[1].set_title("Isolation Forest Score")
    axes[1].set_xlabel("ts")
    axes[1].set_ylabel("Anomaly Score")
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)


def save_results(df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)


def run_if(
    df: pd.DataFrame | None = None,
    feature_cols: list[str] | None = None,
    save_plot_file: bool = True,
) -> tuple[pd.DataFrame, list[str], float]:
    if df is None:
        df = load_if_input()
    else:
        df = df.loc[df["is_valid"]].copy() if "is_valid" in df.columns else df.copy()
        df = df.sort_values("ts").reset_index(drop=True)

    feature_columns = feature_cols if feature_cols is not None else select_features(df)
    filtered_df = prepare_feature_frame(df, feature_columns)
    analyzed, threshold = run_isolation_forest(filtered_df, feature_columns)

    if save_plot_file:
        save_plot(analyzed, threshold, OUTPUT_PATH)

    return analyzed, feature_columns, threshold


def main() -> None:
    analyzed, feature_columns, threshold = run_if(df=None, feature_cols=None, save_plot_file=True)
    save_results(
        analyzed[["ts", "P", "anomaly_score", "anomaly_if"]],
        RESULT_OUTPUT_PATH,
    )
    print(f"사용된 feature: {feature_columns}")
    print(f"결측 제거 후 행 수: {len(analyzed)}")

    anomaly_df = analyzed.loc[analyzed["anomaly_if"], ["ts", "P", "anomaly_score"]].copy()
    anomaly_ratio = len(anomaly_df) / len(analyzed) if len(analyzed) else 0.0

    print(f"전체 행 수: {len(analyzed)}")
    print(f"이상 탐지 개수: {len(anomaly_df)} ({anomaly_ratio:.2%})")
    print("이상 구간 상위 10개:")
    print(anomaly_df.head(10).to_string(index=False))
    print(f"플롯 저장: {OUTPUT_PATH}")
    print(f"결과 저장: {RESULT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
