from __future__ import annotations

import logging
from pathlib import Path

import matplotlib
import pandas as pd
from sqlalchemy import text

from config.meter_metadata import get_meters_by_type
from scripts.fetch_h1z16_with_weather import (
    END_TS,
    START_TS,
    WEATHER_METER_URN,
    build_engine,
)

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "raw_eda" / "electric"
SUMMARY_CSV_PATH = OUTPUT_DIR / "electric_meter_correlation_summary.csv"
LONG_CSV_PATH = OUTPUT_DIR / "electric_meter_correlation_long.csv"
TARGET_COLUMN = "P"
MAX_PLOT_COLUMNS = 18

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")
logger = logging.getLogger(__name__)


MEASUREMENT_SQL = text(
    """
SELECT DISTINCT measurement
FROM ems.cr_measurement_1h
WHERE meter_urn = :meter_urn
ORDER BY measurement
"""
)

def get_measurements(engine, meter_urn: str) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(MEASUREMENT_SQL, {"meter_urn": meter_urn}).fetchall()
    return [row[0] for row in rows]


def build_pivot_sql(measurements: list[str]) -> str:
    pivot_lines = [
        f"""MAX(CASE WHEN measurement = '{measurement}' THEN value END) AS "{measurement}" """
        for measurement in measurements
    ]
    pivot_sql = ",\n    ".join(pivot_lines)
    return f"""
SELECT
    ts,
    meter_urn,
    {pivot_sql}
FROM ems.cr_measurement_1h
WHERE meter_urn = :meter_urn
  AND ts BETWEEN :start_ts AND :end_ts
GROUP BY ts, meter_urn
ORDER BY ts
"""


def fetch_meter_raw_df(engine, meter_urn: str) -> tuple[pd.DataFrame, list[str]]:
    measurements = get_measurements(engine, meter_urn)
    if not measurements:
        raise ValueError(f"{meter_urn} has no measurements")

    pivot_sql = text(build_pivot_sql(measurements))
    df = pd.read_sql(
        pivot_sql,
        con=engine,
        params={
            "meter_urn": meter_urn,
            "start_ts": START_TS,
            "end_ts": END_TS,
        },
        parse_dates=["ts"],
    )
    for column in measurements:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df, measurements


def fetch_weather_df(engine) -> tuple[pd.DataFrame, list[str]]:
    weather_measurements = get_measurements(engine, WEATHER_METER_URN)
    if not weather_measurements:
        raise ValueError("Weather station has no measurements")

    pivot_lines = [
        f"""MAX(CASE WHEN measurement = '{measurement}' THEN value END) AS "{measurement}" """
        for measurement in weather_measurements
    ]
    pivot_sql = text(
        f"""
SELECT
    ts,
    {",\n    ".join(pivot_lines)}
FROM ems.cr_measurement_1h
WHERE meter_urn = :meter_urn
  AND ts BETWEEN :start_ts AND :end_ts
GROUP BY ts
ORDER BY ts
"""
    )
    df = pd.read_sql(
        pivot_sql,
        con=engine,
        params={
            "meter_urn": WEATHER_METER_URN,
            "start_ts": START_TS,
            "end_ts": END_TS,
        },
        parse_dates=["ts"],
    )
    for column in weather_measurements:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df, weather_measurements


def select_usable_columns(df: pd.DataFrame, candidate_columns: list[str]) -> list[str]:
    usable = []
    for column in candidate_columns:
        non_null_count = int(df[column].notna().sum())
        unique_count = int(df[column].nunique(dropna=True))
        if non_null_count >= 2 and unique_count >= 2:
            usable.append(column)
    return usable


def build_target_correlation(df: pd.DataFrame, usable_columns: list[str]) -> pd.Series:
    if TARGET_COLUMN not in usable_columns:
        raise ValueError(f"{TARGET_COLUMN} is not available as a usable column")

    corr_series = df[usable_columns].corr()[TARGET_COLUMN].dropna()
    corr_series = corr_series.drop(labels=[TARGET_COLUMN], errors="ignore")
    if corr_series.empty:
        raise ValueError(f"{TARGET_COLUMN} has no comparable columns")

    return corr_series.sort_values(key=lambda series: series.abs(), ascending=False)


def save_heatmap(corr_series: pd.Series, meter_urn: str) -> Path:
    output_path = OUTPUT_DIR / f"{meter_urn}_corr.png"
    plot_series = corr_series.head(MAX_PLOT_COLUMNS)
    labels = list(plot_series.index)
    heatmap_df = pd.DataFrame([plot_series.values], index=[TARGET_COLUMN], columns=labels)

    fig_width = max(10, len(labels) * 0.7)
    fig_height = 3.8
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    image = ax.imshow(heatmap_df, cmap="coolwarm", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(labels)))
    ax.set_yticks([0])
    ax.set_xticklabels(labels, rotation=90)
    ax.set_yticklabels([TARGET_COLUMN])
    ax.set_title(
        f"Raw DB Correlation vs {TARGET_COLUMN}: {meter_urn} "
        f"(top {len(labels)} by |corr|)"
    )

    for idx, value in enumerate(plot_series.values):
        text_color = "white" if abs(float(value)) >= 0.6 else "black"
        ax.text(
            idx,
            0,
            f"{float(value):.2f}",
            ha="center",
            va="center",
            fontsize=9,
            color=text_color,
            fontweight="bold",
        )

    fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def summarize_meter(
    meter_urn: str,
    df: pd.DataFrame,
    measurements: list[str],
    usable_columns: list[str],
    corr_series: pd.Series,
) -> dict[str, object]:
    top_column = str(corr_series.index[0]) if not corr_series.empty else ""
    top_corr = round(float(corr_series.iloc[0]), 4) if not corr_series.empty else None
    top_abs_corr = round(float(corr_series.abs().iloc[0]), 4) if not corr_series.empty else None
    null_ratio_mean = round(float(df[usable_columns].isna().mean().mean()), 4) if usable_columns else None
    return {
        "meter_urn": meter_urn,
        "row_count": int(len(df)),
        "measurement_count": int(len(measurements)),
        "measurement_columns": ",".join(measurements),
        "joined_weather_columns": "",
        "usable_column_count": int(len(usable_columns)),
        "usable_columns": ",".join(usable_columns),
        "start_ts": df["ts"].min(),
        "end_ts": df["ts"].max(),
        "mean_null_ratio_usable": null_ratio_mean,
        "target_column": TARGET_COLUMN,
        "top_corr_with_target_column": top_column,
        "top_corr_with_target_value": top_corr,
        "top_abs_corr_with_target_value": top_abs_corr,
    }


def build_long_rows(meter_urn: str, corr_series: pd.Series) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for rank, (column, corr_value) in enumerate(corr_series.items(), start=1):
        corr_float = float(corr_value)
        rows.append(
            {
                "meter_urn": meter_urn,
                "target_column": TARGET_COLUMN,
                "rank": rank,
                "column": str(column),
                "corr": round(corr_float, 6),
                "abs_corr": round(abs(corr_float), 6),
            }
        )
    return rows


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    engine = build_engine()
    weather_df, weather_columns = fetch_weather_df(engine)
    electric_meters = get_meters_by_type("electric")
    summary_rows: list[dict[str, object]] = []
    long_rows: list[dict[str, object]] = []

    logger.info("전기 계량기 %s개 raw correlation 생성 시작", len(electric_meters))

    for meter_urn in electric_meters:
        logger.info("%s 처리 시작", meter_urn)
        try:
            raw_df, measurements = fetch_meter_raw_df(engine, meter_urn)
            merged_df = raw_df.merge(weather_df, on="ts", how="left")
            candidate_columns = measurements + weather_columns
            usable_columns = select_usable_columns(merged_df, candidate_columns)

            if len(usable_columns) < 2:
                logger.warning("%s usable columns 부족, PNG 생략", meter_urn)
                summary_rows.append(
                    {
                        "meter_urn": meter_urn,
                        "row_count": int(len(merged_df)),
                        "measurement_count": int(len(measurements)),
                        "measurement_columns": ",".join(measurements),
                        "joined_weather_columns": ",".join(weather_columns),
                        "usable_column_count": int(len(usable_columns)),
                        "usable_columns": ",".join(usable_columns),
                        "start_ts": merged_df["ts"].min(),
                        "end_ts": merged_df["ts"].max(),
                        "mean_null_ratio_usable": None,
                        "target_column": TARGET_COLUMN,
                        "top_corr_with_target_column": "",
                        "top_corr_with_target_value": None,
                        "top_abs_corr_with_target_value": None,
                    }
                )
                continue

            corr_series = build_target_correlation(merged_df, usable_columns)
            save_heatmap(corr_series, meter_urn)
            summary_rows.append(
                summarize_meter(
                    meter_urn=meter_urn,
                    df=merged_df,
                    measurements=measurements,
                    usable_columns=usable_columns,
                    corr_series=corr_series,
                )
            )
            long_rows.extend(build_long_rows(meter_urn, corr_series))
            summary_rows[-1]["joined_weather_columns"] = ",".join(weather_columns)
            logger.info("%s 완료", meter_urn)
        except Exception as exc:
            logger.exception("%s 실패: %s", meter_urn, exc)
            summary_rows.append(
                {
                    "meter_urn": meter_urn,
                    "row_count": None,
                    "measurement_count": None,
                    "measurement_columns": "",
                    "joined_weather_columns": ",".join(weather_columns),
                    "usable_column_count": None,
                    "usable_columns": "",
                    "start_ts": None,
                    "end_ts": None,
                    "mean_null_ratio_usable": None,
                    "target_column": TARGET_COLUMN,
                    "top_corr_with_target_column": "",
                    "top_corr_with_target_value": None,
                    "top_abs_corr_with_target_value": None,
                    "error": str(exc),
                }
            )

    summary_df = pd.DataFrame(summary_rows)
    long_df = pd.DataFrame(long_rows)
    summary_df.to_csv(SUMMARY_CSV_PATH, index=False, encoding="utf-8-sig")
    long_df.to_csv(LONG_CSV_PATH, index=False, encoding="utf-8-sig")
    logger.info("요약 저장: %s", SUMMARY_CSV_PATH)
    logger.info("상관 long 저장: %s", LONG_CSV_PATH)


if __name__ == "__main__":
    main()
