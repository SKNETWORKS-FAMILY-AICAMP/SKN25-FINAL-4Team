"""Pure report freshness contract for scheduled CMS reports.

Scheduled reports are read-only consumers. They must not silently treat stale or
missing model outputs as current report values. This module builds the exact
read-only checks for the report timestamp and evaluates their row counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

PMAX_REPORT_OUTPUT_TABLE = "mart.pmax_forecast_15min"
ANOMALY_REPORT_OUTPUT_TABLE = "mart.anomaly_warning_1h"
MODEL_SERVING_EVIDENCE_TABLE = "qa.serving_evidence"


@dataclass(frozen=True)
class ReportFreshnessQuerySet:
    report_ts: datetime
    queries: dict[str, str]
    params: dict[str, object]


@dataclass(frozen=True)
class ReportFreshnessStatus:
    ok: bool
    blocked: bool
    pmax_rows: int
    anomaly_rows: int
    evidence_rows: int
    block_reasons: tuple[str, ...]


def build_model_result_freshness_queries(*, report_ts: datetime) -> ReportFreshnessQuerySet:
    """Build exact timestamp read-only freshness checks for a report run."""

    _require_aware_datetime(report_ts, "report_ts")
    return ReportFreshnessQuerySet(
        report_ts=report_ts,
        params={"report_ts": report_ts},
        queries={
            "pmax_result_at_report_ts": f"""
SELECT count(*) AS rows, max(created_at) AS latest_created_at
FROM {PMAX_REPORT_OUTPUT_TABLE}
WHERE base_ts = %(report_ts)s
""".strip(),
            "anomaly_result_at_report_ts": f"""
SELECT count(*) AS rows, max(created_at) AS latest_created_at
FROM {ANOMALY_REPORT_OUTPUT_TABLE}
WHERE forecast_origin_ts = %(report_ts)s
""".strip(),
            "model_serving_evidence_at_report_ts": f"""
SELECT count(*) AS rows, max(created_at) AS latest_created_at
FROM {MODEL_SERVING_EVIDENCE_TABLE}
WHERE base_ts = %(report_ts)s
   OR forecast_origin_ts = %(report_ts)s
""".strip(),
        },
    )


def evaluate_model_result_freshness(*, pmax_rows: int, anomaly_rows: int, evidence_rows: int) -> ReportFreshnessStatus:
    """Return a blocking status for the report model-result section."""

    for name, value in {"pmax_rows": pmax_rows, "anomaly_rows": anomaly_rows, "evidence_rows": evidence_rows}.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
    reasons: list[str] = []
    if pmax_rows <= 0:
        reasons.append("missing_pmax_result_at_report_ts")
    if anomaly_rows <= 0:
        reasons.append("missing_anomaly_result_at_report_ts")
    if evidence_rows <= 0:
        reasons.append("missing_model_serving_evidence_at_report_ts")
    return ReportFreshnessStatus(
        ok=not reasons,
        blocked=bool(reasons),
        pmax_rows=pmax_rows,
        anomaly_rows=anomaly_rows,
        evidence_rows=evidence_rows,
        block_reasons=tuple(reasons),
    )


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


__all__ = [
    "ANOMALY_REPORT_OUTPUT_TABLE",
    "MODEL_SERVING_EVIDENCE_TABLE",
    "PMAX_REPORT_OUTPUT_TABLE",
    "ReportFreshnessQuerySet",
    "ReportFreshnessStatus",
    "build_model_result_freshness_queries",
    "evaluate_model_result_freshness",
]
