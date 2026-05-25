from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "raw_eda" / "sliding_window"
PNG_ROOT = OUTPUT_ROOT / "png" / "thermal"
CSV_ROOT = OUTPUT_ROOT / "csv" / "thermal"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.eda.stl.eda_stl_heat import heat_meter_config
from scripts.pipeline.fetch_h1z16_with_weather import (
    build_engine,
    fetch_meter_data,
    fetch_weather_data,
    validate_columns,
)


YEARS = [2018, 2019, 2020, 2021, 2022, 2023]
YEAR_COLORS = {
    2018: "tab:blue",
    2019: "tab:orange",
    2020: "tab:green",
    2021: "tab:red",
    2022: "tab:purple",
    2023: "tab:brown",
}
ANOMALY_NOTES = [
    "V.K21 qv:    2022 말~2023 초 rolling std 급감, 고착 의심",
    "H1.K12 P:    2023년 장기 0 구간, 미가동/이상 여부 확인 필요",
    "H1.K15 Tdiff: 2018 봄 장기 양수 구간, 부호/매핑 이상 의심",
    "H2.K21 Tdiff: 2021.11.15~12.10 게이트웨이 장애 구간 확인",
    "H1.W11 Tdiff: 2023년 운전 조건 변화(난방 현대화) 확인",
    "H1.W12 qv:   여름철 qv≈0 구간, CHP 미가동 정상 가능성 검토",
]
MONTH_TICKS = [pd.Timestamp(year=2020, month=month, day=1) for month in range(1, 13)]
MONTH_LABELS = [f"{month}월" for month in range(1, 13)]


def configure_matplotlib() -> None:
    plt.rcParams["font.family"] = "NanumGothic"
    plt.rcParams["axes.unicode_minus"] = False


def load_heat_meter_data(engine, weather_df: pd.DataFrame, meter_urn: str) -> pd.DataFrame:
    df_main = fetch_meter_data(engine, meter_urn).copy()
    validate_columns(df_main, meter_urn)
    df_main["ts"] = pd.to_datetime(df_main["ts"], utc=True, errors="coerce")

    merged = df_main.merge(weather_df, on="ts", how="left").sort_values("ts").reset_index(drop=True)
    for column in merged.columns:
        if column not in {"ts", "meter_urn"}:
            merged[column] = pd.to_numeric(merged[column], errors="coerce")
    return merged


def resolve_feature_columns(df: pd.DataFrame, configured_features: list[str]) -> list[str]:
    return [feature for feature in configured_features if feature in df.columns and not df[feature].isnull().all()]


def build_sliding_frame(df: pd.DataFrame, feature: str) -> pd.DataFrame:
    working = df[["ts", feature]].copy()
    working["ts"] = pd.to_datetime(working["ts"], utc=True, errors="coerce")
    working[feature] = pd.to_numeric(working[feature], errors="coerce")
    working = working.dropna(subset=["ts"]).sort_values("ts").drop_duplicates(subset=["ts"])
    working["year"] = working["ts"].dt.year
    working = working.loc[working["year"].isin(YEARS)].copy()

    working["sliding_ts"] = pd.to_datetime(
        {
            "year": 2020,
            "month": working["ts"].dt.month,
            "day": working["ts"].dt.day,
            "hour": working["ts"].dt.hour,
            "minute": working["ts"].dt.minute,
            "second": working["ts"].dt.second,
        },
        errors="coerce",
    )
    return working.dropna(subset=["sliding_ts"])


def build_monthly_overlay_frame(sliding_df: pd.DataFrame, feature: str) -> pd.DataFrame:
    working = sliding_df[["year", "sliding_ts", feature]].copy()
    working["month_start"] = pd.to_datetime(
        {
            "year": 2020,
            "month": working["sliding_ts"].dt.month,
            "day": 1,
        }
    )
    aggregated = (
        working.groupby(["year", "month_start"], as_index=False)[feature]
        .median()
        .rename(columns={feature: "observed"})
    )
    return aggregated


def render_sliding_plot(meter_urn: str, feature: str, sliding_df: pd.DataFrame):
    configure_matplotlib()
    overlay_df = build_monthly_overlay_frame(sliding_df, feature)
    fig, ax = plt.subplots(figsize=(18, 6))

    ax.set_title(f"{meter_urn} — {feature} | 연도별 월별 median overlay", fontsize=15, fontweight="bold")

    for year in YEARS:
        year_df = overlay_df.loc[overlay_df["year"] == year, ["month_start", "observed"]].copy()
        if year_df.empty:
            continue

        ax.plot(
            year_df["month_start"],
            year_df["observed"],
            label=str(year),
            color=YEAR_COLORS[year],
            alpha=0.9,
            lw=1.8,
            marker="o",
            markersize=4,
        )

    ax.set_xlabel("월")
    ax.set_ylabel(f"{feature} (monthly median)")
    ax.set_xticks(MONTH_TICKS)
    ax.set_xticklabels(MONTH_LABELS)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.set_xlim(pd.Timestamp("2020-01-01"), pd.Timestamp("2020-12-31 23:59:59"))
    ax.grid(True, alpha=0.25)
    ax.legend(title="연도", ncol=6, loc="upper center", bbox_to_anchor=(0.5, 1.18), frameon=False)

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    return fig


def figure_to_png_bytes(fig) -> bytes:
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=150, bbox_inches="tight")
    buffer.seek(0)
    return buffer.getvalue()


def summarize_by_year(df: pd.DataFrame, meter_urn: str, feature: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    working = df[["ts", feature]].copy()
    working["ts"] = pd.to_datetime(working["ts"], utc=True, errors="coerce")
    working[feature] = pd.to_numeric(working[feature], errors="coerce")
    working = working.dropna(subset=["ts"]).copy()
    working["year"] = working["ts"].dt.year

    for year in YEARS:
        year_df = working.loc[working["year"] == year].copy()
        if year_df.empty:
            continue

        series = year_df[feature]
        rows.append(
            {
                "meter_urn": meter_urn,
                "feature": feature,
                "year": year,
                "mean": float(series.mean()) if series.notna().any() else None,
                "std": float(series.std()) if series.notna().any() else None,
                "min": float(series.min()) if series.notna().any() else None,
                "max": float(series.max()) if series.notna().any() else None,
                "null_ratio": float(series.isnull().mean() * 100),
            }
        )

    return pd.DataFrame(rows)


def build_detail_frame(df: pd.DataFrame, meter_urn: str, feature: str) -> pd.DataFrame:
    working = build_sliding_frame(df, feature).copy()
    if working.empty:
        return pd.DataFrame()

    working = working.rename(columns={feature: "observed"})
    working["meter_urn"] = meter_urn
    working["feature"] = feature
    working["month"] = working["ts"].dt.month
    working["day"] = working["ts"].dt.day
    working["hour"] = working["ts"].dt.hour
    working["minute"] = working["ts"].dt.minute
    working["second"] = working["ts"].dt.second
    working["dayofyear"] = working["sliding_ts"].dt.dayofyear

    return working[
        [
            "meter_urn",
            "feature",
            "ts",
            "year",
            "sliding_ts",
            "month",
            "day",
            "hour",
            "minute",
            "second",
            "dayofyear",
            "observed",
        ]
    ].reset_index(drop=True)


def summarize_sliding_by_year(sliding_df: pd.DataFrame, meter_urn: str, feature: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    for year in YEARS:
        year_df = sliding_df.loc[sliding_df["year"] == year].copy()
        if year_df.empty:
            continue

        series = pd.to_numeric(year_df[feature], errors="coerce")
        rows.append(
            {
                "meter_urn": meter_urn,
                "feature": feature,
                "year": year,
                "mean": float(series.mean()) if series.notna().any() else None,
                "std": float(series.std()) if series.notna().any() else None,
                "min": float(series.min()) if series.notna().any() else None,
                "max": float(series.max()) if series.notna().any() else None,
                "null_ratio": float(series.isnull().mean() * 100),
            }
        )

    return pd.DataFrame(rows)


def print_anomaly_notes() -> None:
    print("[확인 필요 구간]")
    for note in ANOMALY_NOTES:
        print(note)


def main() -> None:
    engine = build_engine()
    weather_df = fetch_weather_data(engine).copy()
    weather_df["ts"] = pd.to_datetime(weather_df["ts"], utc=True, errors="coerce")
    for column in ["Ta", "Igm"]:
        if column in weather_df.columns:
            weather_df[column] = pd.to_numeric(weather_df[column], errors="coerce")

    for meter_urn, configured_features in heat_meter_config.items():
        print(f"=== {meter_urn} ===")
        try:
            df = load_heat_meter_data(engine, weather_df, meter_urn)
        except Exception as exc:
            print(f"  데이터 로드 실패: {exc}")
            print()
            continue

        resolved_features = resolve_feature_columns(df, configured_features)
        if not resolved_features:
            print("  유효 컬럼 없음, skip")
            print()
            continue

        meter_png_dir = PNG_ROOT / meter_urn
        meter_png_dir.mkdir(parents=True, exist_ok=True)
        meter_csv_dir = CSV_ROOT / meter_urn
        meter_csv_dir.mkdir(parents=True, exist_ok=True)
        meter_stats_frames: list[pd.DataFrame] = []
        meter_detail_frames: list[pd.DataFrame] = []

        for feature in resolved_features:
            base_sliding_df = build_sliding_frame(df, feature)
            if base_sliding_df.empty or base_sliding_df[feature].dropna().empty:
                print(f"  {feature}: 유효 데이터 없음, skip")
                continue

            sliding_df = base_sliding_df.copy()
            fig = render_sliding_plot(meter_urn, feature, sliding_df)
            output_path = meter_png_dir / f"{meter_urn}_{feature}_sliding.png"
            fig.savefig(output_path, dpi=150)
            plt.close(fig)
            print(f"  저장: {output_path}")

            stats_df = summarize_sliding_by_year(sliding_df, meter_urn, feature)
            if not stats_df.empty:
                meter_stats_frames.append(stats_df)

            detail_df = build_detail_frame(sliding_df, meter_urn, feature)
            if not detail_df.empty:
                meter_detail_frames.append(detail_df)

        if meter_stats_frames:
            pd.concat(meter_stats_frames, ignore_index=True).to_csv(
                meter_csv_dir / f"{meter_urn}_sliding_stats.csv",
                index=False,
                encoding="utf-8-sig",
            )
            print(f"  통계 저장: {meter_csv_dir / f'{meter_urn}_sliding_stats.csv'}")

        if meter_detail_frames:
            pd.concat(meter_detail_frames, ignore_index=True).to_csv(
                meter_csv_dir / f"{meter_urn}_sliding_detail.csv",
                index=False,
                encoding="utf-8-sig",
            )
            print(f"  detail 저장: {meter_csv_dir / f'{meter_urn}_sliding_detail.csv'}")

        print()

    print_anomaly_notes()


if __name__ == "__main__":
    main()
