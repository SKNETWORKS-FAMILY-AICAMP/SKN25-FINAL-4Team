from __future__ import annotations

import logging
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from sqlalchemy import text

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fetch_h1z16_with_weather import (
    END_TS,
    START_TS,
    WEATHER_METER_URN,
    build_engine,
)


OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "raw_eda"
SIX_YEAR_DIR = OUTPUT_ROOT / "6year"
YEARLY_DIR = OUTPUT_ROOT / "yearly"
SEASONAL_DIR = OUTPUT_ROOT / "seasonal"

MIN_PERIOD_ROWS = 24
MAX_HEATMAP_COLUMNS = 20
SEASON_ORDER = ["Winter", "Spring", "Summer", "Autumn"]
REPRESENTATIVE_METERS = [
    "H1.Z10",
    "H1.Z16",
    "H1.Z13",
    "H2.Z64",
    "H4.Z50",
    "H2.Z68",
    "V.Z84",
    "H1.Z20",
    "H2.T.Z33",
    "H2.Z35",
    "H2.ZE64",
]

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
    return [str(row[0]) for row in rows]


def build_pivot_sql(measurements: list[str]) -> str:
    pivot_lines = [
        f"""MAX(CASE WHEN measurement = '{measurement}' THEN value END) AS "{measurement}" """
        for measurement in measurements
    ]
    return f"""
SELECT
    ts,
    meter_urn,
    {",\n    ".join(pivot_lines)}
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


def add_period_columns(df: pd.DataFrame) -> pd.DataFrame:
    enriched = df.copy()
    enriched["year"] = enriched["ts"].dt.year.astype(int)
    season_map = {
        12: "Winter",
        1: "Winter",
        2: "Winter",
        3: "Spring",
        4: "Spring",
        5: "Spring",
        6: "Summer",
        7: "Summer",
        8: "Summer",
        9: "Autumn",
        10: "Autumn",
        11: "Autumn",
    }
    enriched["season"] = enriched["ts"].dt.month.map(season_map)
    return enriched


def select_usable_columns(df: pd.DataFrame, candidate_columns: list[str]) -> list[str]:
    usable = []
    for column in candidate_columns:
        if column not in df.columns:
            continue
        non_null_count = int(df[column].notna().sum())
        unique_count = int(df[column].nunique(dropna=True))
        if non_null_count >= 2 and unique_count >= 2:
            usable.append(column)
    return usable


def build_corr_matrix(df: pd.DataFrame, usable_columns: list[str]) -> pd.DataFrame:
    if len(usable_columns) < 2:
        raise ValueError("usable columns 부족")
    corr_df = df[usable_columns].corr()
    if corr_df.empty or corr_df.shape[0] < 2:
        raise ValueError("correlation matrix 생성 실패")
    return corr_df


def build_long_rows(
    meter_urn: str,
    period_type: str,
    period_label: str,
    corr_df: pd.DataFrame,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    rank = 1
    for idx, column_a in enumerate(corr_df.columns):
        for column_b in corr_df.columns[idx + 1 :]:
            corr_value = corr_df.loc[column_a, column_b]
            if pd.isna(corr_value):
                continue
            corr_float = float(corr_value)
            rows.append(
                {
                    "meter_urn": meter_urn,
                    "period_type": period_type,
                    "period_label": period_label,
                    "column_a": str(column_a),
                    "column_b": str(column_b),
                    "corr": round(corr_float, 6),
                    "abs_corr": round(abs(corr_float), 6),
                    "rank": rank,
                }
            )
            rank += 1
    rows.sort(key=lambda row: row["abs_corr"], reverse=True)
    for new_rank, row in enumerate(rows, start=1):
        row["rank"] = new_rank
    return rows


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


def pick_heatmap_columns(corr_df: pd.DataFrame) -> list[str]:
    if corr_df.shape[0] <= MAX_HEATMAP_COLUMNS:
        return list(corr_df.columns)

    score_map = {}
    for column in corr_df.columns:
        max_abs_corr = corr_df[column].drop(labels=[column], errors="ignore").abs().max()
        score_map[str(column)] = float(max_abs_corr) if pd.notna(max_abs_corr) else -1.0

    return sorted(score_map, key=lambda column: score_map[column], reverse=True)[:MAX_HEATMAP_COLUMNS]


def save_heatmap(corr_df: pd.DataFrame, meter_urn: str, title: str, output_path: Path) -> None:
    plot_columns = pick_heatmap_columns(corr_df)
    plot_df = corr_df.loc[plot_columns, plot_columns]

    fig_width = max(8, plot_df.shape[1] * 0.65)
    fig_height = max(7, plot_df.shape[0] * 0.6)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    image = ax.imshow(plot_df.values, cmap="coolwarm", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(plot_df.shape[1]))
    ax.set_yticks(range(plot_df.shape[0]))
    ax.set_xticklabels(plot_df.columns, rotation=90)
    ax.set_yticklabels(plot_df.index)
    ax.set_title(title)

    for row_idx in range(plot_df.shape[0]):
        for col_idx in range(plot_df.shape[1]):
            value = plot_df.iat[row_idx, col_idx]
            if pd.isna(value):
                continue
            text_color = "white" if abs(float(value)) >= 0.6 else "black"
            ax.text(col_idx, row_idx, f"{float(value):.2f}", ha="center", va="center", color=text_color, fontsize=7)

    fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)


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
    save_heatmap(corr_df, meter_urn, f"{meter_urn} 6-Year Raw Correlation Matrix", SIX_YEAR_DIR / f"{meter_urn}_corr.png")
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
            title=f"{meter_urn} {year} Raw Correlation Matrix",
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
            title=f"{meter_urn} {season} Raw Correlation Matrix",
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

    engine = build_engine()
    weather_df, weather_columns = fetch_weather_df(engine)

    all_six_year_summary: list[dict[str, object]] = []
    all_six_year_long: list[dict[str, object]] = []
    all_yearly_summary: list[dict[str, object]] = []
    all_yearly_long: list[dict[str, object]] = []
    all_seasonal_summary: list[dict[str, object]] = []
    all_seasonal_long: list[dict[str, object]] = []

    logger.info("대표 전기 계량기 %s개 raw correlation matrix 생성 시작", len(REPRESENTATIVE_METERS))

    for meter_urn in REPRESENTATIVE_METERS:
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

    save_outputs(SIX_YEAR_DIR, all_six_year_summary, all_six_year_long)
    save_outputs(YEARLY_DIR, all_yearly_summary, all_yearly_long)
    save_outputs(SEASONAL_DIR, all_seasonal_summary, all_seasonal_long)

    logger.info("6year 저장: %s", SIX_YEAR_DIR)
    logger.info("yearly 저장: %s", YEARLY_DIR)
    logger.info("seasonal 저장: %s", SEASONAL_DIR)


if __name__ == "__main__":
    main()
