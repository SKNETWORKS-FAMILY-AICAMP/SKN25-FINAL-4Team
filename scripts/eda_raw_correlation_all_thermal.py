from __future__ import annotations

import logging
import sys
from pathlib import Path

import matplotlib
import pandas as pd
from sqlalchemy import text

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.meter_metadata import get_metadata, get_meters_by_type
from scripts.eda_raw_correlation_representative_electric import (
    MIN_PERIOD_ROWS,
    SEASON_ORDER,
    add_period_columns,
    build_corr_matrix,
    build_long_rows,
    build_pivot_sql,
    fetch_weather_df,
    get_measurements,
    save_heatmap,
    select_usable_columns,
)
from scripts.fetch_h1z16_with_weather import END_TS, START_TS, build_engine


OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "raw_eda" / "correlation" / "static"
SIX_YEAR_DIR = OUTPUT_ROOT / "png" / "thermal" / "6year"
YEARLY_DIR = OUTPUT_ROOT / "png" / "thermal" / "yearly"
SEASONAL_DIR = OUTPUT_ROOT / "png" / "thermal" / "seasonal"
SIX_YEAR_CSV_DIR = OUTPUT_ROOT / "csv" / "thermal" / "6year"
YEARLY_CSV_DIR = OUTPUT_ROOT / "csv" / "thermal" / "yearly"
SEASONAL_CSV_DIR = OUTPUT_ROOT / "csv" / "thermal" / "seasonal"

THERMAL_METERS = get_meters_by_type("thermal")

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")
logger = logging.getLogger(__name__)


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


def summarize_period(
    meter_urn: str,
    df: pd.DataFrame,
    measurements: list[str],
    weather_columns: list[str],
    usable_columns: list[str],
    period_type: str,
    period_label: str,
    long_rows: list[dict[str, object]],
    error: str | None = None,
) -> dict[str, object]:
    metadata = get_metadata(meter_urn) or {}
    top_pair = ""
    top_corr = None
    top_abs_corr = None
    if long_rows:
        top_pair = f"{long_rows[0]['column_a']}~{long_rows[0]['column_b']}"
        top_corr = long_rows[0]["corr"]
        top_abs_corr = long_rows[0]["abs_corr"]

    mean_null_ratio = (
        round(float(df[usable_columns].isna().mean().mean()), 6)
        if usable_columns
        else None
    )
    return {
        "meter_urn": meter_urn,
        "thermal_mode": metadata.get("thermal_mode"),
        "group_name": metadata.get("group_name"),
        "description": metadata.get("description"),
        "period_type": period_type,
        "period_label": period_label,
        "row_count": int(len(df)),
        "measurement_count": int(len(measurements)),
        "measurement_columns": ",".join(measurements),
        "joined_weather_columns": ",".join(weather_columns),
        "usable_column_count": int(len(usable_columns)),
        "usable_columns": ",".join(usable_columns),
        "start_ts": df["ts"].min(),
        "end_ts": df["ts"].max(),
        "mean_null_ratio_usable": mean_null_ratio,
        "pair_count": int(len(long_rows)),
        "top_pair": top_pair,
        "top_corr": top_corr,
        "top_abs_corr": top_abs_corr,
        "error": error,
    }


def process_period(
    meter_urn: str,
    period_type: str,
    period_label: str,
    period_df: pd.DataFrame,
    measurements: list[str],
    weather_columns: list[str],
    output_dir: Path,
    title: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    candidate_columns = measurements + weather_columns
    usable_columns = select_usable_columns(period_df, candidate_columns)

    if len(period_df) < MIN_PERIOD_ROWS or len(usable_columns) < 2:
        summary = summarize_period(
            meter_urn=meter_urn,
            df=period_df,
            measurements=measurements,
            weather_columns=weather_columns,
            usable_columns=usable_columns,
            period_type=period_type,
            period_label=period_label,
            long_rows=[],
            error="row 수 또는 usable columns 부족",
        )
        return summary, []

    corr_df = build_corr_matrix(period_df, usable_columns)
    long_rows = build_long_rows(meter_urn, period_type, period_label, corr_df)
    summary = summarize_period(
        meter_urn=meter_urn,
        df=period_df,
        measurements=measurements,
        weather_columns=weather_columns,
        usable_columns=usable_columns,
        period_type=period_type,
        period_label=period_label,
        long_rows=long_rows,
    )
    save_heatmap(corr_df, meter_urn, title, output_dir / f"{meter_urn}_{period_label}_corr.png")
    return summary, long_rows


def process_6year(
    meter_urn: str,
    merged_df: pd.DataFrame,
    measurements: list[str],
    weather_columns: list[str],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    candidate_columns = measurements + weather_columns
    usable_columns = select_usable_columns(merged_df, candidate_columns)

    if len(usable_columns) < 2:
        summary = summarize_period(
            meter_urn=meter_urn,
            df=merged_df,
            measurements=measurements,
            weather_columns=weather_columns,
            usable_columns=usable_columns,
            period_type="6year",
            period_label="all",
            long_rows=[],
            error="usable columns 부족",
        )
        return summary, []

    corr_df = build_corr_matrix(merged_df, usable_columns)
    long_rows = build_long_rows(meter_urn, "6year", "all", corr_df)
    summary = summarize_period(
        meter_urn=meter_urn,
        df=merged_df,
        measurements=measurements,
        weather_columns=weather_columns,
        usable_columns=usable_columns,
        period_type="6year",
        period_label="all",
        long_rows=long_rows,
    )
    save_heatmap(
        corr_df,
        meter_urn,
        f"{meter_urn} 6-Year Raw Thermal Correlation Matrix",
        SIX_YEAR_DIR / f"{meter_urn}_corr.png",
    )
    return summary, long_rows


def process_yearly(
    meter_urn: str,
    merged_df: pd.DataFrame,
    measurements: list[str],
    weather_columns: list[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    summary_rows: list[dict[str, object]] = []
    long_rows: list[dict[str, object]] = []
    years = sorted(int(year) for year in merged_df["year"].dropna().unique())

    for year in years:
        period_df = merged_df.loc[merged_df["year"] == year].copy()
        summary, period_long_rows = process_period(
            meter_urn=meter_urn,
            period_type="yearly",
            period_label=str(year),
            period_df=period_df,
            measurements=measurements,
            weather_columns=weather_columns,
            output_dir=YEARLY_DIR,
            title=f"{meter_urn} {year} Raw Thermal Correlation Matrix",
        )
        summary_rows.append(summary)
        long_rows.extend(period_long_rows)

    return summary_rows, long_rows


def process_seasonal(
    meter_urn: str,
    merged_df: pd.DataFrame,
    measurements: list[str],
    weather_columns: list[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    summary_rows: list[dict[str, object]] = []
    long_rows: list[dict[str, object]] = []

    for season in SEASON_ORDER:
        period_df = merged_df.loc[merged_df["season"] == season].copy()
        summary, period_long_rows = process_period(
            meter_urn=meter_urn,
            period_type="seasonal",
            period_label=season,
            period_df=period_df,
            measurements=measurements,
            weather_columns=weather_columns,
            output_dir=SEASONAL_DIR,
            title=f"{meter_urn} {season} Raw Thermal Correlation Matrix",
        )
        summary_rows.append(summary)
        long_rows.extend(period_long_rows)

    return summary_rows, long_rows


def save_outputs(output_dir: Path, summary_rows: list[dict[str, object]], long_rows: list[dict[str, object]]) -> None:
    pd.DataFrame(summary_rows).to_csv(output_dir / "summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(long_rows).to_csv(output_dir / "long.csv", index=False, encoding="utf-8-sig")


def main() -> None:
    SIX_YEAR_DIR.mkdir(parents=True, exist_ok=True)
    YEARLY_DIR.mkdir(parents=True, exist_ok=True)
    SEASONAL_DIR.mkdir(parents=True, exist_ok=True)
    SIX_YEAR_CSV_DIR.mkdir(parents=True, exist_ok=True)
    YEARLY_CSV_DIR.mkdir(parents=True, exist_ok=True)
    SEASONAL_CSV_DIR.mkdir(parents=True, exist_ok=True)

    engine = build_engine()
    weather_df, weather_columns = fetch_weather_df(engine)

    all_six_year_summary: list[dict[str, object]] = []
    all_six_year_long: list[dict[str, object]] = []
    all_yearly_summary: list[dict[str, object]] = []
    all_yearly_long: list[dict[str, object]] = []
    all_seasonal_summary: list[dict[str, object]] = []
    all_seasonal_long: list[dict[str, object]] = []

    logger.info("열 계량기 %s개 raw correlation matrix 생성 시작", len(THERMAL_METERS))

    for meter_urn in THERMAL_METERS:
        logger.info("%s 처리 시작", meter_urn)
        try:
            raw_df, measurements = fetch_meter_raw_df(engine, meter_urn)
            merged_df = add_period_columns(raw_df.merge(weather_df, on="ts", how="left"))

            six_year_summary, six_year_long = process_6year(
                meter_urn=meter_urn,
                merged_df=merged_df,
                measurements=measurements,
                weather_columns=weather_columns,
            )
            yearly_summary, yearly_long = process_yearly(
                meter_urn=meter_urn,
                merged_df=merged_df,
                measurements=measurements,
                weather_columns=weather_columns,
            )
            seasonal_summary, seasonal_long = process_seasonal(
                meter_urn=meter_urn,
                merged_df=merged_df,
                measurements=measurements,
                weather_columns=weather_columns,
            )

            all_six_year_summary.append(six_year_summary)
            all_six_year_long.extend(six_year_long)
            all_yearly_summary.extend(yearly_summary)
            all_yearly_long.extend(yearly_long)
            all_seasonal_summary.extend(seasonal_summary)
            all_seasonal_long.extend(seasonal_long)
            logger.info("%s 완료", meter_urn)
        except Exception as exc:
            logger.exception("%s 실패: %s", meter_urn, exc)
            empty_df = pd.DataFrame({"ts": pd.to_datetime([])})
            all_six_year_summary.append(
                summarize_period(
                    meter_urn=meter_urn,
                    df=empty_df,
                    measurements=[],
                    weather_columns=weather_columns,
                    usable_columns=[],
                    period_type="6year",
                    period_label="all",
                    long_rows=[],
                    error=str(exc),
                )
            )

    save_outputs(SIX_YEAR_CSV_DIR, all_six_year_summary, all_six_year_long)
    save_outputs(YEARLY_CSV_DIR, all_yearly_summary, all_yearly_long)
    save_outputs(SEASONAL_CSV_DIR, all_seasonal_summary, all_seasonal_long)

    logger.info("6year PNG 저장: %s", SIX_YEAR_DIR)
    logger.info("yearly PNG 저장: %s", YEARLY_DIR)
    logger.info("seasonal PNG 저장: %s", SEASONAL_DIR)
    logger.info("6year CSV 저장: %s", SIX_YEAR_CSV_DIR)
    logger.info("yearly CSV 저장: %s", YEARLY_CSV_DIR)
    logger.info("seasonal CSV 저장: %s", SEASONAL_CSV_DIR)


if __name__ == "__main__":
    main()
