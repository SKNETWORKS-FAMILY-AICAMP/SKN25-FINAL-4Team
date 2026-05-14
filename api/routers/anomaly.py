from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException


router = APIRouter()
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUMMARY_RESULTS_PATH = PROJECT_ROOT / "outputs" / "anomaly_results_summary.csv"
DETAIL_RESULTS_PATH = PROJECT_ROOT / "outputs" / "anomaly_results_detail.csv"
SUMMARY_DF = pd.read_csv(SUMMARY_RESULTS_PATH)
DETAIL_DF = pd.read_csv(DETAIL_RESULTS_PATH)
DETAIL_DF["ts"] = pd.to_datetime(DETAIL_DF["ts"])

VALID_LEVELS = {"DANGER", "WARNING", "NORMAL"}


def _clean_records(df: pd.DataFrame) -> list[dict]:
    cleaned_df = df.astype(object).where(pd.notna(df), None)
    return cleaned_df.to_dict(orient="records")


@router.get("/summary")
def get_anomaly_summary() -> list[dict]:
    summary_df = SUMMARY_DF.copy()
    summary_df["danger_count"] = pd.to_numeric(summary_df["danger"], errors="coerce").fillna(0).astype(int)
    summary_df["warning_count"] = pd.to_numeric(summary_df["warning"], errors="coerce").fillna(0).astype(int)
    summary_df = summary_df[["meter_urn", "danger_count", "warning_count"]]
    summary_df = summary_df.sort_values(["danger_count", "warning_count"], ascending=[False, False]).reset_index(drop=True)
    return summary_df.to_dict(orient="records")


def _validate_level(level: str | None) -> None:
    if level is not None and level not in VALID_LEVELS:
        raise HTTPException(status_code=400, detail="level must be one of DANGER, WARNING, NORMAL")


def _parse_datetime(value: str | None, field_name: str) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        return pd.to_datetime(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid {field_name} datetime") from exc


@router.get("/{meter_urn}/timeline")
def get_anomaly_timeline(
    meter_urn: str,
    level: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    logger.info(
        "anomaly request meter_urn=%s level=%s start=%s end=%s",
        meter_urn,
        level,
        start,
        end,
    )

    _validate_level(level)
    start_ts = _parse_datetime(start, "start")
    end_ts = _parse_datetime(end, "end")

    if meter_urn not in set(DETAIL_DF["meter_urn"].unique()):
        raise HTTPException(status_code=404, detail=f"meter_urn {meter_urn} not found")

    meter_df = DETAIL_DF.loc[DETAIL_DF["meter_urn"] == meter_urn].copy()
    if meter_df.empty:
        return []

    if level is not None:
        meter_df = meter_df.loc[meter_df["ensemble_level"] == level]

    if start_ts is not None:
        meter_df = meter_df.loc[meter_df["ts"] >= start_ts]
    if end_ts is not None:
        meter_df = meter_df.loc[meter_df["ts"] <= end_ts]

    timeline_df = meter_df[
        ["ts", "anomaly_stl", "anomaly_if", "anomaly_lstm", "ensemble_level"]
    ].copy()
    timeline_df["ts"] = timeline_df["ts"].astype(str)
    return _clean_records(timeline_df)


@router.get("/{meter_urn}/stats")
def get_anomaly_stats(meter_urn: str) -> dict:
    logger.info("anomaly stats request meter_urn=%s", meter_urn)

    meter_df = DETAIL_DF.loc[DETAIL_DF["meter_urn"] == meter_urn].copy()
    if meter_df.empty:
        raise HTTPException(status_code=404, detail=f"meter_urn {meter_urn} not found")

    total = int(len(meter_df))
    danger = int((meter_df["ensemble_level"] == "DANGER").sum())
    warning = int((meter_df["ensemble_level"] == "WARNING").sum())
    normal = int((meter_df["ensemble_level"] == "NORMAL").sum())

    stl = meter_df["anomaly_stl"].astype(bool)
    if_flag = meter_df["anomaly_if"].astype(bool)
    lstm = meter_df["anomaly_lstm"].astype(bool)

    return {
        "meter_urn": meter_urn,
        "total": total,
        "danger": danger,
        "warning": warning,
        "normal": normal,
        "danger_pct": round(danger / total * 100, 2) if total else 0.0,
        "warning_pct": round(warning / total * 100, 2) if total else 0.0,
        "stl_only_count": int((stl & ~if_flag & ~lstm).sum()),
        "if_only_count": int((~stl & if_flag & ~lstm).sum()),
        "lstm_only_count": int((~stl & ~if_flag & lstm).sum()),
        "stl_if_count": int((stl & if_flag & ~lstm).sum()),
        "stl_lstm_count": int((stl & ~if_flag & lstm).sum()),
        "if_lstm_count": int((~stl & if_flag & lstm).sum()),
        "all_three_count": int((stl & if_flag & lstm).sum()),
    }


@router.get("/{meter_urn}")
def get_anomaly_by_meter(
    meter_urn: str,
    level: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    logger.info(
        "anomaly summary-row request meter_urn=%s level=%s start=%s end=%s",
        meter_urn,
        level,
        start,
        end,
    )

    _validate_level(level)

    meter_df = SUMMARY_DF.loc[SUMMARY_DF["meter_urn"] == meter_urn].copy()
    if meter_df.empty:
        raise HTTPException(status_code=404, detail=f"meter_urn {meter_urn} not found")

    if level == "DANGER":
        meter_df = meter_df.loc[pd.to_numeric(meter_df["danger"], errors="coerce").fillna(0) > 0]
    elif level == "WARNING":
        meter_df = meter_df.loc[pd.to_numeric(meter_df["warning"], errors="coerce").fillna(0) > 0]
    elif level == "NORMAL":
        total = pd.to_numeric(meter_df["total"], errors="coerce").fillna(0)
        danger = pd.to_numeric(meter_df["danger"], errors="coerce").fillna(0)
        warning = pd.to_numeric(meter_df["warning"], errors="coerce").fillna(0)
        meter_df = meter_df.loc[(total - danger - warning) > 0]

    if "ts" in meter_df.columns:
        if start is not None:
            meter_df = meter_df.loc[meter_df["ts"] >= start]
        if end is not None:
            meter_df = meter_df.loc[meter_df["ts"] <= end]

    return _clean_records(meter_df)
