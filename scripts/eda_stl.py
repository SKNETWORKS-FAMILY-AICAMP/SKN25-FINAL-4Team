from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import pandas as pd
from statsmodels.tsa.seasonal import STL

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "raw_eda" / "stl" / "png" / "electric"
METER_LIST = [
    "H1.Z10",
    "H1.Z13",
    "H1.Z16",
    "H1.Z20",
    "H2.T.Z33",
    "H2.Z35",
    "H2.Z64",
    "H2.Z68",
    "H2.ZE64",
    "H4.Z50",
    "V.Z84",
]
STL_PERIOD = 24 * 7
STL_SEASONAL = 13
INTERPOLATE_LIMIT = 24

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.meter_metadata import get_metadata
from scripts.preprocess_h1z16 import preprocess_meter


def prepare_series(df: pd.DataFrame, meter_urn: str) -> pd.Series:
    if "ts" not in df.columns:
        raise ValueError("'ts' column is missing")

    metadata = get_metadata(meter_urn)
    if metadata is None:
        raise ValueError(f"Metadata not found for {meter_urn}")

    target_col = metadata.get("anomaly_target")
    if not target_col:
        raise ValueError(f"anomaly_target is not configured for {meter_urn}")
    if target_col not in df.columns:
        raise ValueError(f"Target column '{target_col}' is missing for {meter_urn}")

    working = df[["ts", target_col]].copy()
    working["ts"] = pd.to_datetime(working["ts"], utc=True, errors="coerce")
    working["anomaly_target"] = pd.to_numeric(working[target_col], errors="coerce")
    working = working.dropna(subset=["ts"]).sort_values("ts").drop_duplicates(subset=["ts"])
    working = working.set_index("ts")[["anomaly_target"]]

    series = working["anomaly_target"].interpolate(
        method="linear",
        limit=INTERPOLATE_LIMIT,
        limit_direction="both",
    )
    series = series.dropna()
    if series.empty:
        raise ValueError("No usable anomaly_target data after interpolation")
    if len(series) < STL_PERIOD * 2:
        raise ValueError(
            f"Not enough data for STL: {len(series)} rows, need at least {STL_PERIOD * 2}"
        )

    return series


def plot_stl(
    meter_urn: str, series: pd.Series, output_path: Path
) -> tuple[int, float, float, float, pd.DataFrame]:
    result = STL(series, period=STL_PERIOD, seasonal=STL_SEASONAL).fit()
    residual = result.resid

    residual_mean = residual.mean()
    residual_std = residual.std()
    upper = residual_mean + 3 * residual_std
    lower = residual_mean - 3 * residual_std
    anomaly_mask = (residual > upper) | (residual < lower)
    anomalies = residual[anomaly_mask]
    detail_df = pd.DataFrame(
        {
            "meter_urn": meter_urn,
            "ts": series.index,
            "observed": series.values,
            "trend": result.trend.values,
            "seasonal": result.seasonal.values,
            "residual": residual.values,
            "upper": upper,
            "lower": lower,
            "is_anomaly": anomaly_mask.values,
        }
    )

    fig, axes = plt.subplots(4, 1, figsize=(18, 14), sharex=True)

    axes[0].plot(series.index, series.values, color="black", linewidth=0.8)
    axes[0].set_title(f"{meter_urn} Observed")
    axes[0].set_ylabel("Observed")

    axes[1].plot(result.trend.index, result.trend.values, color="tab:blue", linewidth=0.8)
    axes[1].set_title("Trend")
    axes[1].set_ylabel("Trend")

    axes[2].plot(
        result.seasonal.index,
        result.seasonal.values,
        color="tab:green",
        linewidth=0.8,
    )
    axes[2].set_title("Seasonal")
    axes[2].set_ylabel("Seasonal")

    axes[3].plot(residual.index, residual.values, color="tab:gray", linewidth=0.8)
    axes[3].axhline(upper, color="red", linestyle="--", linewidth=1.0)
    axes[3].axhline(lower, color="red", linestyle="--", linewidth=1.0)
    if not anomalies.empty:
        axes[3].scatter(
            anomalies.index,
            anomalies.values,
            color="red",
            s=10,
            alpha=0.8,
        )
    axes[3].set_title("Residual")
    axes[3].set_ylabel("Residual")
    axes[3].set_xlabel("ts")

    fig.suptitle(f"{meter_urn} STL Decomposition", fontsize=14)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)

    n_anomaly = int(anomaly_mask.sum())
    ratio = float(n_anomaly / len(residual) * 100)
    return (
        n_anomaly,
        ratio,
        float(residual.max()),
        float(residual.min()),
        detail_df,
    )


def process_meter(meter_urn: str) -> tuple[dict[str, object], pd.DataFrame] | None:
    try:
        df, _, _, _ = preprocess_meter(
            meter_urn,
            print_progress=False,
            print_issue_details=False,
        )
        series = prepare_series(df, meter_urn)
        output_path = OUTPUT_DIR / f"{meter_urn}_stl.png"
        n_anomaly, ratio, residual_max, residual_min, detail_df = plot_stl(
            meter_urn, series, output_path
        )
    except Exception as exc:
        print(f"{meter_urn} STL 실패: {exc}")
        return None

    print(f"{meter_urn} STL 완료")
    print(f"  잔차 이상 구간 수: {n_anomaly}건 ({ratio:.2f}%)")
    print(f"  잔차 최대값: {residual_max:.2f}")
    print(f"  잔차 최소값: {residual_min:.2f}")
    summary_row: dict[str, object] = {
        "meter_urn": meter_urn,
        "n_total": int(len(detail_df)),
        "n_anomaly": n_anomaly,
        "ratio": ratio,
        "residual_max": residual_max,
        "residual_min": residual_min,
    }
    return summary_row, detail_df


def save_csv_outputs(
    summary_rows: list[dict[str, object]], detail_frames: list[pd.DataFrame]
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summary_df = pd.DataFrame(summary_rows)
    if not summary_df.empty:
        summary_df.to_csv(OUTPUT_DIR / "stl_summary.csv", index=False)

    if detail_frames:
        detail_df = pd.concat(detail_frames, ignore_index=True)
        detail_df.to_csv(OUTPUT_DIR / "stl_detail.csv", index=False)


def print_output_summary() -> None:
    png_files = sorted(OUTPUT_DIR.glob("*_stl.png"))
    existing_names = {path.name for path in png_files}
    expected_names = [f"{meter_urn}_stl.png" for meter_urn in METER_LIST]
    found_expected = [name for name in expected_names if name in existing_names]

    print(f"생성 확인: {len(found_expected)}/{len(expected_names)}개")
    for name in found_expected:
        print(name)


def main() -> None:
    summary_rows: list[dict[str, object]] = []
    detail_frames: list[pd.DataFrame] = []

    for meter_urn in METER_LIST:
        result = process_meter(meter_urn)
        if result is None:
            continue
        summary_row, detail_df = result
        summary_rows.append(summary_row)
        detail_frames.append(detail_df)

    save_csv_outputs(summary_rows, detail_frames)
    print_output_summary()


if __name__ == "__main__":
    main()
