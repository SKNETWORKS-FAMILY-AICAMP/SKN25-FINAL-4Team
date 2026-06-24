"""Observed-first report summary helpers.

The helpers in this module are pure Python and import-safe. They do not perform
DB, network, filesystem, Airflow, or model calls.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def build_observed_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a user-facing observed/feature summary from period rows.

    Forecast rows are intentionally not accepted here. The report can mention
    forecast later as supplementary context, but this summary is anchored on
    observed or feature-materialized values only.
    """

    normalized = [_normalize_observed_row(row) for row in rows]
    valid = [row for row in normalized if row["peak_value"] is not None]
    peak_row = max(valid, key=lambda row: float(row["peak_value"])) if valid else None
    meter_totals: dict[str, dict[str, Any]] = {}
    low_sample_windows: list[dict[str, Any]] = []
    for row in normalized:
        meter = row["meter_urn"]
        entry = meter_totals.setdefault(
            meter,
            {
                "meter_urn": meter,
                "measurement": row["measurement"],
                "peak_value": None,
                "peak_ts": None,
                "window_count": 0,
                "low_sample_count": 0,
                "min_coverage_ratio": None,
            },
        )
        entry["window_count"] += 1
        value = row["peak_value"]
        if value is not None and (entry["peak_value"] is None or value > entry["peak_value"]):
            entry["peak_value"] = value
            entry["peak_ts"] = row["peak_ts"] or row["window_ts"]
        coverage = row["coverage_ratio"]
        if coverage is not None:
            if entry["min_coverage_ratio"] is None or coverage < entry["min_coverage_ratio"]:
                entry["min_coverage_ratio"] = coverage
            if coverage < 0.8:
                entry["low_sample_count"] += 1
                low_sample_windows.append(
                    {
                        "meter_urn": meter,
                        "window_ts": row["window_ts"],
                        "coverage_ratio": coverage,
                        "user_message": "해당 구간은 관측 표본이 부족해 점검 우선순위 판단에 주의가 필요합니다.",
                    }
                )
    top_meters = sorted(
        meter_totals.values(),
        key=lambda item: (item["peak_value"] is not None, item["peak_value"] or 0),
        reverse=True,
    )[:10]
    return {
        "schema_version": "observed_summary.v1",
        "source_priority": "observed_first",
        "observed_row_count": len(normalized),
        "meter_count": len(meter_totals),
        "period_peak": peak_row or {},
        "top_meters": top_meters,
        "usage_patterns": _usage_patterns(normalized),
        "data_quality_notes": _quality_notes(low_sample_windows),
        "low_sample_windows": low_sample_windows[:20],
    }


def _usage_patterns(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_hour: dict[str, list[float]] = {}
    for row in rows:
        ts = str(row.get("peak_ts") or row.get("window_ts") or "")
        value = row.get("peak_value")
        if len(ts) < 13 or value is None:
            continue
        by_hour.setdefault(ts[11:13], []).append(float(value))
    ranked = sorted(
        (
            {"hour": hour, "avg_peak_value": sum(values) / len(values), "window_count": len(values)}
            for hour, values in by_hour.items()
        ),
        key=lambda item: item["avg_peak_value"],
        reverse=True,
    )
    return ranked[:6]


def _quality_notes(low_sample_windows: Sequence[Mapping[str, Any]]) -> list[str]:
    if not low_sample_windows:
        return ["관측 표본 부족으로 표시된 주요 구간은 없습니다."]
    meters = sorted({str(row.get("meter_urn")) for row in low_sample_windows if row.get("meter_urn")})
    return [f"관측 표본이 부족한 구간이 {len(low_sample_windows)}건 있습니다. 우선 확인 계량기: {', '.join(meters[:5])}"]


def _normalize_observed_row(row: Mapping[str, Any]) -> dict[str, Any]:
    peak = row.get("peak_value", row.get("max_value"))
    coverage = row.get("coverage_ratio")
    return {
        "window_ts": _text(row.get("window_ts")),
        "meter_urn": _text(row.get("meter_urn")) or "계량기 미확인",
        "measurement": _text(row.get("measurement")) or "P",
        "peak_ts": _text(row.get("peak_ts")),
        "peak_value": float(peak) if peak is not None else None,
        "coverage_ratio": float(coverage) if coverage is not None else None,
        "observed_points": _int_or_none(row.get("observed_points")),
        "expected_points": _int_or_none(row.get("expected_points")),
        "source_mode": _text(row.get("source_mode")) or "observed_or_feature",
    }


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = ["build_observed_summary"]
