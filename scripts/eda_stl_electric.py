from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "raw_eda" / "stl"
PNG_ROOT = OUTPUT_ROOT / "png" / "electric"
CSV_ROOT = OUTPUT_ROOT / "csv" / "electric"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.preprocess_h1z16 import fetch_joined_data


meter_config = {
    "H1.Z10": ["P", "PF", "I1"],
    "H1.Z13": ["P", "PF", "I1", "Ta"],
    "H1.Z16": ["P", "PF", "I1", "Ta"],
    "H1.Z20": ["P", "PF"],
    "H2.T.Z33": ["P", "PF", "I1", "Igm"],
    "H2.Z35": ["P", "PF", "I1", "Igm"],
    "H2.Z64": ["P", "PF", "I1", "f"],
    "H2.Z68": ["P", "PF", "I1", "Q"],
    "H2.ZE64": ["P", "PF", "I1"],
    "H4.Z50": ["P", "PF", "I1", "Ta"],
    "V.Z84": ["P", "PF", "Igm"],
}

FEATURE_UNITS = {
    "P": "W",
    "PF": "ratio",
    "I1": "A",
    "Ta": "degC",
    "Igm": "W/m2",
    "f": "Hz",
    "Q": "var",
    "Tdiff": "degC",
    "Tvl": "degC",
    "Trl": "degC",
    "qv": "m3/h",
}


def load_raw_meter_data(meter_urn: str) -> pd.DataFrame:
    df = fetch_joined_data(meter_urn).copy()
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    return df


def configure_matplotlib() -> None:
    plt.rcParams["font.family"] = "NanumGothic"
    plt.rcParams["axes.unicode_minus"] = False


def build_input_series(df: pd.DataFrame, col: str) -> pd.Series:
    if "ts" not in df.columns:
        raise ValueError("'ts' column missing")
    if col not in df.columns:
        raise ValueError(f"'{col}' column missing")

    working = df[["ts", col]].copy()
    working["ts"] = pd.to_datetime(working["ts"], utc=True, errors="coerce")
    working[col] = pd.to_numeric(working[col], errors="coerce")
    working = working.dropna(subset=["ts"]).sort_values("ts").drop_duplicates(subset=["ts"])
    return working.set_index("ts")[col]


def run_stl_eda(
    series: pd.Series,
    title: str,
    period: int = 24 * 7,
    seasonal: int = 13,
    sigma: int = 3,
):
    series = pd.to_numeric(series, errors="coerce").copy()
    series = series.interpolate(method="linear", limit=24)

    stl = STL(series.dropna(), period=period, seasonal=seasonal)
    result = stl.fit()

    residual = result.resid
    if len(residual) == 0:
        anomaly_mask = residual.astype(bool)
        print(f"  [{title}]")
        print("  이상 구간: 0건 (0.00%)")
        print("  잔차 최대: nan | 최소: nan")
        print(f"  +{sigma}σ: nan | -{sigma}σ: nan")
        print("  residual empty after STL, 빈 결과로 처리")
        print()
        return result, anomaly_mask

    mean = residual.mean()
    std = residual.std()
    upper = mean + sigma * std
    lower = mean - sigma * std
    anomaly_mask = (residual > upper) | (residual < lower)
    n_anomaly = int(anomaly_mask.sum())
    ratio = n_anomaly / len(residual) * 100

    print(f"  [{title}]")
    print(f"  이상 구간: {n_anomaly}건 ({ratio:.2f}%)")
    print(f"  잔차 최대: {residual.max():.2f} | 최소: {residual.min():.2f}")
    print(f"  +{sigma}σ: {upper:.2f} | -{sigma}σ: {lower:.2f}")
    print()

    return result, anomaly_mask


def summarize_stl_result(
    meter_urn: str,
    feature: str,
    result,
    anomaly_mask: pd.Series,
    null_ratio: float,
    sigma: int = 3,
) -> tuple[dict[str, object], pd.DataFrame]:
    residual = result.resid
    mean = residual.mean()
    std = residual.std()
    upper = mean + sigma * std
    lower = mean - sigma * std
    n_total = int(len(residual))
    n_anomaly = int(anomaly_mask.sum())
    ratio = float(n_anomaly / n_total * 100) if n_total else 0.0

    summary_row: dict[str, object] = {
        "meter_urn": meter_urn,
        "feature": feature,
        "n_total": n_total,
        "n_anomaly": n_anomaly,
        "ratio": ratio,
        "residual_max": float(residual.max()) if n_total else np.nan,
        "residual_min": float(residual.min()) if n_total else np.nan,
        "upper": float(upper) if pd.notna(upper) else np.nan,
        "lower": float(lower) if pd.notna(lower) else np.nan,
        "null_ratio": null_ratio,
    }
    detail_df = pd.DataFrame(
        {
            "meter_urn": meter_urn,
            "feature": feature,
            "ts": residual.index,
            "observed": result.observed.values,
            "trend": result.trend.values,
            "seasonal": result.seasonal.values,
            "residual": residual.values,
            "upper": upper,
            "lower": lower,
            "is_anomaly": anomaly_mask.values,
        }
    )
    return summary_row, detail_df


def save_stl_plot(
    result,
    anomaly_mask: pd.Series,
    title: str,
    output_path: Path,
    sigma: int = 3,
    unit: str | None = None,
) -> None:
    configure_matplotlib()
    observed = result.observed
    trend = result.trend
    seasonal_ = result.seasonal
    residual = result.resid
    if len(observed) == 0 or observed.index.empty:
        return

    mean = residual.mean()
    std = residual.std()
    upper = mean + sigma * std
    lower = mean - sigma * std

    fig, axes = plt.subplots(4, 1, figsize=(18, 10), sharex=True)
    display_title = title if not unit else f"{title} [{unit}]"
    fig.suptitle(display_title, fontsize=13, fontweight="bold")

    axes[0].plot(observed.index, observed, lw=0.8, color="steelblue")
    axes[0].set_ylabel("Observed")
    axes[0].set_title("Observed", loc="left", fontsize=11, fontweight="bold")

    axes[1].plot(trend.index, trend, lw=0.8, color="orange")
    axes[1].set_ylabel("Trend")
    axes[1].set_title("Trend", loc="left", fontsize=11, fontweight="bold")

    axes[2].plot(seasonal_.index, seasonal_, lw=0.8, color="green")
    axes[2].set_ylabel("Seasonal")
    axes[2].set_title("Seasonal", loc="left", fontsize=11, fontweight="bold")

    axes[3].plot(residual.index, residual, lw=0.8, color="gray")
    axes[3].axhline(upper, color="red", linestyle="--", lw=1, label=f"+{sigma}σ")
    axes[3].axhline(lower, color="red", linestyle="--", lw=1, label=f"-{sigma}σ")
    axes[3].axhline(mean, color="black", linestyle="--", lw=0.8)
    axes[3].scatter(
        residual[anomaly_mask].index,
        residual[anomaly_mask],
        color="red",
        s=5,
        zorder=5,
        label=f"이상 ({int(anomaly_mask.sum())}건)",
    )
    axes[3].set_ylabel("Residual")
    axes[3].set_title("Residual", loc="left", fontsize=11, fontweight="bold")
    axes[3].legend(loc="upper right", fontsize=8)

    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y", tz=observed.index.tz))
        ax.xaxis.set_major_locator(mdates.YearLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=30)

    x_min = observed.index.min()
    x_max = observed.index.max()
    if pd.notna(x_min) and pd.notna(x_max):
        axes[-1].set_xlim(x_min, x_max)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def main() -> None:
    configure_matplotlib()
    for meter_urn, columns in meter_config.items():
        print(f"=== {meter_urn} ===")
        try:
            df = load_raw_meter_data(meter_urn)
        except Exception as exc:
            print(f"  데이터 로드 실패: {exc}")
            print()
            continue

        meter_png_dir = PNG_ROOT / meter_urn
        meter_csv_dir = CSV_ROOT / meter_urn
        summary_rows: list[dict[str, object]] = []
        detail_frames: list[pd.DataFrame] = []

        for col in columns:
            if col not in df.columns:
                print(f"  {col} 컬럼 없음, skip")
                print()
                continue

            missing_ratio = float(df[col].isnull().mean() * 100)
            print(f"  {col} 결측률: {missing_ratio:.1f}%")
            if missing_ratio > 50:
                print("  → 결측률 높음, STL 결과 해석 주의")

            try:
                result, anomaly_mask = run_stl_eda(
                    build_input_series(df, col),
                    f"{meter_urn} - {col}",
                    sigma=3,
                )
                summary_row, detail_df = summarize_stl_result(
                    meter_urn,
                    col,
                    result,
                    anomaly_mask,
                    missing_ratio,
                    sigma=3,
                )
                summary_rows.append(summary_row)
                detail_frames.append(detail_df)

                output_path = meter_png_dir / f"{meter_urn}_{col}_stl.png"
                save_stl_plot(
                    result,
                    anomaly_mask,
                    f"{meter_urn} - {col} STL 분해 (±3σ)",
                    output_path,
                    sigma=3,
                    unit=FEATURE_UNITS.get(col),
                )
                print(f"  저장 경로: {output_path}")
                print()
            except Exception as exc:
                print(f"  [{meter_urn} - {col}] STL 실패: {exc}")
                print()

        if summary_rows:
            meter_csv_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(summary_rows).to_csv(
                meter_csv_dir / f"{meter_urn}_summary.csv",
                index=False,
            )
        if detail_frames:
            meter_csv_dir.mkdir(parents=True, exist_ok=True)
            pd.concat(detail_frames, ignore_index=True).to_csv(
                meter_csv_dir / f"{meter_urn}_detail.csv",
                index=False,
            )


if __name__ == "__main__":
    main()
