"""Import-safe anomaly feature materialization contract.

This materializer converts approved observed 1-hour measurement facts into the
strict anomaly-serving feature table. It keeps production strict mode separate
from reference/backfill evidence.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from typing import Literal

from cms.contracts.anomaly_detection_1h import ANOMALY_DETECTION_FEATURE_TABLE
from cms.contracts.live_pipeline import CANONICAL_MEASUREMENT_1H, LIVE_MEASUREMENT_1H, SOURCE_MODE_HYBRID_WARM_START, SOURCE_MODE_LIVE_OBSERVED
from cms.data.runtime_postgres import PsycopgConnectionConfig, load_postgres_config_from_env

ANOMALY_FEATURE_WRITE_ENV_FLAG = "CMS_ENABLE_ANOMALY_FEATURE_MATERIALIZATION"
ANOMALY_FEATURE_STRICT_SOURCE_TABLE = LIVE_MEASUREMENT_1H
ANOMALY_FEATURE_OBSERVED_SOURCE_TABLES = (LIVE_MEASUREMENT_1H, CANONICAL_MEASUREMENT_1H)
ANOMALY_FEATURE_WARM_START_SOURCE_TABLES = (LIVE_MEASUREMENT_1H,)
AnomalyFeatureSourceMode = Literal["live_observed", "hybrid_warm_start"]


@dataclass(frozen=True)
class AnomalyFeatureMaterializationCommand:
    sql: str
    params: dict[str, object]
    source_table: str
    target_table: str
    source_mode: str
    batch_size: int
    write_gate_env: str = ANOMALY_FEATURE_WRITE_ENV_FLAG


@dataclass(frozen=True)
class AnomalyFeatureMaterializationResult:
    ok: bool
    attempted: bool
    materialized_count: int = 0
    blocked: bool = False
    errors: tuple[str, ...] = ()


def make_anomaly_feature_materialization_command(
    *,
    start_ts: datetime,
    end_ts: datetime,
    source_table: str = ANOMALY_FEATURE_STRICT_SOURCE_TABLE,
    source_mode: str = SOURCE_MODE_LIVE_OBSERVED,
    meter_urns: Sequence[str] = (),
    batch_size: int = 1000,
) -> AnomalyFeatureMaterializationCommand:
    """Build a bounded SQL plan for `mart.anomaly_feature_1h`."""

    _require_aware_datetime(start_ts, "start_ts")
    _require_aware_datetime(end_ts, "end_ts")
    if end_ts <= start_ts:
        raise ValueError("end_ts must be after start_ts")
    if not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    _validate_source(source_table=source_table, source_mode=source_mode)
    meters = tuple(dict.fromkeys(meter_urns))
    meter_clause = ""
    params: dict[str, object] = {"start_ts": start_ts, "end_ts": end_ts, "batch_size": batch_size, "source_mode": source_mode}
    if meters:
        placeholders = ", ".join(f"%(meter_{idx})s" for idx, _ in enumerate(meters))
        meter_clause = f"AND meter_urn IN ({placeholders})"
        params.update({f"meter_{idx}": meter for idx, meter in enumerate(meters)})
    source_ref_tail = _source_ref_tail_sql(source_table)

    sql = f"""
WITH candidate_buckets AS (
    SELECT DISTINCT bucket_ts, meter_urn
    FROM {source_table}
    WHERE bucket_ts >= %(start_ts)s
      AND bucket_ts < %(end_ts)s
      {meter_clause}
    ORDER BY bucket_ts, meter_urn
    LIMIT %(batch_size)s
), pivoted AS (
    SELECT
        c.bucket_ts,
        c.meter_urn,
        max(src.value) FILTER (WHERE src.measurement = 'P') AS p_value,
        max(src.value) FILTER (WHERE src.measurement = 'U1') AS u1_value,
        max(src.value) FILTER (WHERE src.measurement = 'PF') AS pf_value,
        max(src.value) FILTER (WHERE src.measurement = 'qv') AS qv_value,
        max(src.value) FILTER (WHERE src.measurement = 'Tdiff') AS tdiff_value,
        jsonb_agg(
            jsonb_build_object(
                'source_table', '{source_table}',
                'bucket_ts', src.bucket_ts,
                'meter_urn', src.meter_urn,
                'measurement', src.measurement
                {source_ref_tail}
            ) ORDER BY src.measurement
        ) FILTER (WHERE src.measurement IS NOT NULL) AS source_refs
    FROM candidate_buckets AS c
    JOIN {source_table} AS src
      ON src.bucket_ts = c.bucket_ts
     AND src.meter_urn = c.meter_urn
     AND src.measurement IN ('P', 'U1', 'PF', 'qv', 'Tdiff')
    GROUP BY c.bucket_ts, c.meter_urn
), shaped AS (
    SELECT
        bucket_ts,
        meter_urn,
        CASE
            WHEN p_value IS NOT NULL AND u1_value IS NOT NULL THEN 'electric'
            WHEN p_value IS NOT NULL AND qv_value IS NOT NULL AND tdiff_value IS NOT NULL THEN 'heat'
            ELSE 'insufficient'
        END AS feature_set,
        p_value,
        u1_value,
        pf_value,
        qv_value,
        tdiff_value,
        jsonb_build_object(
            'source_mode', %(source_mode)s::text,
            'hour_sin', sin((extract(hour from bucket_ts)::double precision / 24.0) * 2.0 * pi()),
            'hour_cos', cos((extract(hour from bucket_ts)::double precision / 24.0) * 2.0 * pi()),
            'day_of_week', extract(dow from bucket_ts)::integer
        ) AS derived_features,
        CASE
            WHEN p_value IS NULL THEN 'bad'
            WHEN u1_value IS NULL AND qv_value IS NULL THEN 'warning'
            ELSE 'good'
        END AS input_quality,
        COALESCE(source_refs, '[]'::jsonb) AS source_refs
    FROM pivoted
)
INSERT INTO {ANOMALY_DETECTION_FEATURE_TABLE} (
    bucket_ts, meter_urn, feature_set, p_value, u1_value, pf_value, qv_value,
    tdiff_value, derived_features, input_quality, source_refs, created_at
)
SELECT
    bucket_ts, meter_urn, feature_set, p_value, u1_value, pf_value, qv_value,
    tdiff_value, derived_features, input_quality, source_refs, now()
FROM shaped
WHERE feature_set <> 'insufficient'
ON CONFLICT (bucket_ts, meter_urn)
DO UPDATE SET
    feature_set = EXCLUDED.feature_set,
    p_value = EXCLUDED.p_value,
    u1_value = EXCLUDED.u1_value,
    pf_value = EXCLUDED.pf_value,
    qv_value = EXCLUDED.qv_value,
    tdiff_value = EXCLUDED.tdiff_value,
    derived_features = EXCLUDED.derived_features,
    input_quality = EXCLUDED.input_quality,
    source_refs = EXCLUDED.source_refs,
    created_at = EXCLUDED.created_at
RETURNING bucket_ts, meter_urn
""".strip()
    return AnomalyFeatureMaterializationCommand(
        sql=sql,
        params=params,
        source_table=source_table,
        target_table=ANOMALY_DETECTION_FEATURE_TABLE,
        source_mode=source_mode,
        batch_size=batch_size,
    )


def execute_anomaly_feature_materialization_command(
    command: AnomalyFeatureMaterializationCommand,
    *,
    config: PsycopgConnectionConfig | None = None,
    allow_write: bool = False,
    env: Mapping[str, str] | None = None,
) -> AnomalyFeatureMaterializationResult:
    errors = _write_gate_errors(command=command, allow_write=allow_write, env=env)
    if errors:
        return AnomalyFeatureMaterializationResult(ok=False, attempted=False, blocked=True, errors=errors)
    runtime_env = dict(os.environ if env is None else env)
    db_config = config or load_postgres_config_from_env(runtime_env)
    psycopg = import_module("psycopg")
    with psycopg.connect(**db_config.connect_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(command.sql, command.params)
            rows = cur.fetchall()
        conn.commit()
    return AnomalyFeatureMaterializationResult(ok=True, attempted=True, materialized_count=len(rows))


def _validate_source(*, source_table: str, source_mode: str) -> None:
    if source_table.startswith("reference.") or "corrected" in source_table:
        raise ValueError("anomaly strict materialization must not use reference/corrected source tables")
    if source_table.startswith("mart."):
        raise ValueError("anomaly materialization source must be observed 1h facts, not mart feature/output tables")
    if source_mode == SOURCE_MODE_LIVE_OBSERVED and source_table not in ANOMALY_FEATURE_OBSERVED_SOURCE_TABLES:
        raise ValueError("live_observed anomaly materialization must read live.measurement_1h or canonical.measurement_1h")
    if source_mode == SOURCE_MODE_HYBRID_WARM_START and source_table not in (CANONICAL_MEASUREMENT_1H, *ANOMALY_FEATURE_WARM_START_SOURCE_TABLES):
        raise ValueError("hybrid_warm_start anomaly materialization source is not allowed")
    if source_mode not in {SOURCE_MODE_LIVE_OBSERVED, SOURCE_MODE_HYBRID_WARM_START}:
        raise ValueError("unsupported anomaly feature source_mode")


def _source_ref_tail_sql(source_table: str) -> str:
    if source_table == CANONICAL_MEASUREMENT_1H:
        return ", 'promotion_id', NULLIF(src.promotion_id, ''), 'source_run_id', NULLIF(src.source_run_id, '')"
    if source_table == LIVE_MEASUREMENT_1H:
        return ", 'policy_id', src.policy_id, 'policy_version', src.policy_version, 'source_run_id', NULLIF(src.source_run_id, '')"
    raise ValueError(f"unsupported anomaly feature source table: {source_table}")


def _write_gate_errors(*, command: AnomalyFeatureMaterializationCommand, allow_write: bool, env: Mapping[str, str] | None) -> tuple[str, ...]:
    runtime_env = os.environ if env is None else env
    errors: list[str] = []
    if not allow_write:
        errors.append("allow_write_required")
    if runtime_env.get(command.write_gate_env) != "1":
        errors.append(f"{command.write_gate_env}=1_required")
    return tuple(errors)


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


__all__ = [
    "ANOMALY_FEATURE_OBSERVED_SOURCE_TABLES",
    "ANOMALY_FEATURE_STRICT_SOURCE_TABLE",
    "ANOMALY_FEATURE_WARM_START_SOURCE_TABLES",
    "ANOMALY_FEATURE_WRITE_ENV_FLAG",
    "AnomalyFeatureMaterializationCommand",
    "AnomalyFeatureMaterializationResult",
    "execute_anomaly_feature_materialization_command",
    "make_anomaly_feature_materialization_command",
]
