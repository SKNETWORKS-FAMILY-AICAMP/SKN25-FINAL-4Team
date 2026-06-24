"""Read-only approval packet helpers for P-Max strict live-observed materialization.

This module does not connect to PostgreSQL or mutate state. It defines the
bounded scope and SQL needed to inspect the current mart lineage before a
separate production mart write gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from cms.contracts.live_pipeline import SOURCE_MODE_LIVE_OBSERVED
from cms.contracts.pmax_forecast_15min import (
    PMAX_FORECAST_INPUT_TABLE,
    PMAX_FORECAST_LOGICAL_METER_SOURCES,
    PMAX_FORECAST_REQUIRED_HISTORY_WINDOWS,
    PMAX_FORECAST_REQUIRED_MEASUREMENTS,
    pmax_live_observed_source_meters,
)


@dataclass(frozen=True)
class PmaxMaterializationScope:
    """Bounded P-Max strict-serving input scope."""

    base_ts: datetime
    input_start_ts: datetime
    input_end_ts: datetime
    history_windows: int
    logical_meters: tuple[str, ...]
    source_meters: tuple[str, ...]
    measurements: tuple[str, ...]
    target_table: str = PMAX_FORECAST_INPUT_TABLE


@dataclass(frozen=True)
class PacketQuery:
    """Parameterized SQL for a read-only approval packet step."""

    name: str
    sql: str
    params: Mapping[str, Any]


def build_scope(
    *,
    base_ts: datetime,
    history_windows: int = PMAX_FORECAST_REQUIRED_HISTORY_WINDOWS,
    logical_meters: Sequence[str] = tuple(PMAX_FORECAST_LOGICAL_METER_SOURCES),
) -> PmaxMaterializationScope:
    if base_ts.tzinfo is None or base_ts.utcoffset() is None:
        raise ValueError("base_ts must be timezone-aware")
    if history_windows < PMAX_FORECAST_REQUIRED_HISTORY_WINDOWS:
        raise ValueError(f"history_windows must be >= {PMAX_FORECAST_REQUIRED_HISTORY_WINDOWS}")
    unsupported = tuple(meter for meter in logical_meters if meter not in PMAX_FORECAST_LOGICAL_METER_SOURCES)
    if unsupported:
        raise ValueError("unsupported logical_meters: " + ",".join(unsupported))

    input_end_ts = base_ts - timedelta(minutes=15)
    input_start_ts = input_end_ts - timedelta(minutes=15 * (history_windows - 1))
    source_meters = tuple(dict.fromkeys(source for logical in logical_meters for source in pmax_live_observed_source_meters(logical)))
    return PmaxMaterializationScope(
        base_ts=base_ts,
        input_start_ts=input_start_ts,
        input_end_ts=input_end_ts,
        history_windows=history_windows,
        logical_meters=tuple(logical_meters),
        source_meters=source_meters,
        measurements=tuple(PMAX_FORECAST_REQUIRED_MEASUREMENTS),
    )


def build_inventory_queries(scope: PmaxMaterializationScope) -> tuple[PacketQuery, ...]:
    params = _scope_params(scope)
    return (
        PacketQuery(
            name="strict_live_observed_coverage",
            sql=f"""
SELECT meter_urn, measurement,
       count(DISTINCT window_ts)::int AS live_windows,
       min(window_ts)::text AS min_window_ts,
       max(window_ts)::text AS max_window_ts
FROM {scope.target_table}
WHERE meter_urn = ANY(%(source_meters)s)
  AND measurement = ANY(%(measurements)s)
  AND window_ts BETWEEN %(input_start_ts)s AND %(input_end_ts)s
  AND source_mode = %(source_mode)s
GROUP BY meter_urn, measurement
ORDER BY meter_urn, measurement
""".strip(),
            params=params,
        ),
        PacketQuery(
            name="null_lineage_coverage",
            sql=f"""
SELECT meter_urn, measurement,
       count(*)::int AS rows,
       count(DISTINCT window_ts)::int AS windows,
       count(*) FILTER (WHERE source_layer IS NULL)::int AS null_source_layer_rows,
       count(*) FILTER (WHERE provenance IS NULL)::int AS null_provenance_rows,
       count(DISTINCT source_file)::int AS source_files,
       min(source_file) AS sample_source_file,
       array_agg(DISTINCT run_id ORDER BY run_id) AS run_ids
FROM {scope.target_table}
WHERE meter_urn = ANY(%(source_meters)s)
  AND measurement = ANY(%(measurements)s)
  AND window_ts BETWEEN %(input_start_ts)s AND %(input_end_ts)s
  AND source_mode IS NULL
GROUP BY meter_urn, measurement
ORDER BY meter_urn, measurement
""".strip(),
            params=params,
        ),
        PacketQuery(
            name="write_scope_estimate",
            sql=f"""
SELECT count(*)::int AS candidate_rows,
       count(DISTINCT (meter_urn, measurement, window_ts))::int AS candidate_keys,
       count(DISTINCT run_id)::int AS source_run_ids
FROM {scope.target_table}
WHERE meter_urn = ANY(%(source_meters)s)
  AND measurement = ANY(%(measurements)s)
  AND window_ts BETWEEN %(input_start_ts)s AND %(input_end_ts)s
  AND source_mode IS NULL
""".strip(),
            params=params,
        ),
        PacketQuery(
            name="sample_null_lineage_rows",
            sql=f"""
SELECT window_ts::text, meter_urn, measurement, source_file, source_layer,
       source_mode, provenance::text, run_id
FROM {scope.target_table}
WHERE meter_urn = ANY(%(source_meters)s)
  AND measurement = ANY(%(measurements)s)
  AND window_ts BETWEEN %(input_start_ts)s AND %(input_end_ts)s
  AND source_mode IS NULL
ORDER BY meter_urn, measurement, window_ts, run_id
LIMIT 24
""".strip(),
            params=params,
        ),
    )


def build_packet(scope: PmaxMaterializationScope, query_results: Mapping[str, object]) -> dict[str, object]:
    """Build a secret-free approval packet from read-only query results."""

    return {
        "packet_type": "pmax_strict_live_observed_materialization_approval",
        "target_table": scope.target_table,
        "base_ts": scope.base_ts.isoformat(),
        "input_start_ts": scope.input_start_ts.isoformat(),
        "input_end_ts": scope.input_end_ts.isoformat(),
        "history_windows": scope.history_windows,
        "logical_meters": list(scope.logical_meters),
        "source_meters": list(scope.source_meters),
        "measurements": list(scope.measurements),
        "current_evidence": dict(query_results),
        "current_verdict": "strict_blocked_until_live_observed_lineage_exists_for_all_required_windows",
        "recommended_write_path": "bounded_mart_rematerialization_from_approved_live_observed_source",
        "forbidden_shortcut": "do_not_relabel_corrected_resampled_or_null_lineage_rows_as_live_observed_without_source_approval",
        "write_gate": {
            "requires_separate_production_mart_write_approval": True,
            "canonical_write_allowed": False,
            "destructive_cleanup_allowed": False,
            "target_only": scope.target_table,
            "cleanup_key_required": True,
            "suggested_run_id_prefix": "pmax_live_observed_",
        },
        "post_write_acceptance": [
            "strict model-serving run passes without --allow-harmonized-observed-input",
            "write_attempted remains false for no-write runner verification",
            f"source_mode='live_observed' coverage is {scope.history_windows}/{scope.history_windows} for every required meter/measurement",
            "source_layer/provenance are non-null for every serving input row",
            "no canonical schema/table writes are performed",
        ],
    }


def _scope_params(scope: PmaxMaterializationScope) -> dict[str, object]:
    return {
        "source_meters": list(scope.source_meters),
        "measurements": list(scope.measurements),
        "input_start_ts": scope.input_start_ts,
        "input_end_ts": scope.input_end_ts,
        "source_mode": SOURCE_MODE_LIVE_OBSERVED,
    }


__all__ = ["PacketQuery", "PmaxMaterializationScope", "build_inventory_queries", "build_packet", "build_scope"]
