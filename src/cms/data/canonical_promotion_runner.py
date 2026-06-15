"""Import-safe canonical promotion worker contract.

This module connects the existing live validation chain to canonical promotion as
an explicit bounded worker plan:

``live.promotion_check -> live.measurement_15min/1h -> canonical.measurement_15min/1h``

It builds reviewable SQL and an optional runtime executor behind a double gate.
Importing the module never opens a DB connection and never writes canonical rows.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from typing import Any, Literal

from cms.contracts.live_pipeline import (
    CANONICAL_MEASUREMENT_15MIN,
    CANONICAL_MEASUREMENT_1H,
    LIVE_MEASUREMENT_15MIN,
    LIVE_MEASUREMENT_1H,
    LIVE_MEASUREMENT_POLICY,
    LIVE_PROMOTION_CHECK,
    MART_PEAK_FEATURE_15MIN,
    MART_PEAK_INPUT_15MIN,
)
from cms.data.runtime_postgres import PsycopgConnectionConfig, load_postgres_config_from_env

CANONICAL_PROMOTION_WRITE_ENV_FLAG = "CMS_ENABLE_CANONICAL_PROMOTION"
CANONICAL_PROMOTION_ALLOWED_SOURCE_TABLES = (LIVE_MEASUREMENT_15MIN, LIVE_MEASUREMENT_1H)
CANONICAL_PROMOTION_FORBIDDEN_SOURCE_TABLES = (MART_PEAK_FEATURE_15MIN, MART_PEAK_INPUT_15MIN)
CanonicalResolution = Literal["15min", "1h"]


@dataclass(frozen=True)
class CanonicalPromotionCommand:
    """One bounded canonical promotion SQL command."""

    sql: str
    params: dict[str, object]
    source_tables: tuple[str, ...]
    target_tables: tuple[str, ...]
    approval_id: str
    promotion_id: str
    batch_size: int
    write_gate_env: str = CANONICAL_PROMOTION_WRITE_ENV_FLAG


@dataclass(frozen=True)
class CanonicalPromotionResult:
    """Portable result shape for one promotion pass."""

    ok: bool
    attempted: bool
    promoted_15min_count: int = 0
    promoted_1h_count: int = 0
    promotion_check_count: int = 0
    marked_promotion_check_count: int = 0
    blocked: bool = False
    errors: tuple[str, ...] = ()

    @property
    def promoted_count(self) -> int:
        return self.promoted_15min_count + self.promoted_1h_count


def make_canonical_promotion_command(
    *,
    promotion_id: str,
    approval_id: str,
    batch_size: int = 100,
    source_tables: Sequence[str] = CANONICAL_PROMOTION_ALLOWED_SOURCE_TABLES,
    min_coverage_ratio: float = 0.0,
    max_bucket_ts: datetime | None = None,
) -> CanonicalPromotionCommand:
    """Build a bounded SQL plan for controlled live-to-canonical promotion."""

    _require_non_empty(promotion_id, "promotion_id")
    _require_non_empty(approval_id, "approval_id")
    if not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    if not 0 <= min_coverage_ratio <= 1:
        raise ValueError("min_coverage_ratio must be between 0 and 1")
    if max_bucket_ts is not None and (max_bucket_ts.tzinfo is None or max_bucket_ts.utcoffset() is None):
        raise ValueError("max_bucket_ts must be timezone-aware")
    normalized_sources = tuple(dict.fromkeys(source_tables))
    _validate_source_tables(normalized_sources)
    target_tables = tuple(_target_table_for_source(source) for source in normalized_sources)

    allowed_sources_sql = ", ".join(f"'{source}'" for source in normalized_sources)
    sql = f"""
WITH eligible_checks AS (
    SELECT
        pc.check_id,
        pc.source_table,
        pc.meter_urn,
        pc.measurement,
        pc.resolution,
        pc.bucket_ts,
        pc.policy_id,
        pc.policy_version,
        pc.evidence
    FROM {LIVE_PROMOTION_CHECK} AS pc
    JOIN {LIVE_MEASUREMENT_POLICY} AS policy
      ON policy.policy_id = pc.policy_id
     AND policy.policy_version = pc.policy_version
     AND policy.meter_urn = pc.meter_urn
     AND policy.measurement = pc.measurement
    WHERE pc.eligibility_status = 'pass'
      AND pc.source_table IN ({allowed_sources_sql})
      AND pc.block_reasons = ARRAY[]::text[]
      AND policy.enabled = true
      AND policy.canonical_eligible = true
      AND policy.cadence_group = 'native_1min'
      AND policy.source_native_interval_seconds = 60
      AND COALESCE((pc.evidence->>'canonical_write')::boolean, false) = false
      AND COALESCE((pc.evidence->>'coverage_ratio')::numeric, 1.0) >= %(min_coverage_ratio)s
      AND (
          %(max_bucket_ts)s::timestamptz IS NULL OR
          (pc.source_table = '{LIVE_MEASUREMENT_15MIN}' AND pc.bucket_ts + interval '15 minutes' <= %(max_bucket_ts)s::timestamptz) OR
          (pc.source_table = '{LIVE_MEASUREMENT_1H}' AND pc.bucket_ts + interval '1 hour' <= %(max_bucket_ts)s::timestamptz)
      )
      AND (
          (pc.source_table = '{LIVE_MEASUREMENT_15MIN}' AND EXISTS (
              SELECT 1
              FROM {LIVE_MEASUREMENT_15MIN} AS src
              WHERE src.bucket_ts = pc.bucket_ts
                AND src.meter_urn = pc.meter_urn
                AND src.measurement = pc.measurement
                AND src.resolution = '15min'
                AND src.policy_version = pc.policy_version
                AND src.expected_points = 15
                AND src.observed_points >= 0
                AND src.gap_points >= 0
                AND src.observed_points <= src.expected_points
                AND src.gap_points <= src.expected_points
                AND (src.observed_points + src.gap_points) <= src.expected_points
                AND abs((src.coverage_ratio::double precision - (src.observed_points::double precision / src.expected_points::double precision))) < 0.000001
          )) OR
          (pc.source_table = '{LIVE_MEASUREMENT_1H}' AND EXISTS (
              SELECT 1
              FROM {LIVE_MEASUREMENT_1H} AS src
              WHERE src.bucket_ts = pc.bucket_ts
                AND src.meter_urn = pc.meter_urn
                AND src.measurement = pc.measurement
                AND src.resolution = '1h'
                AND src.policy_version = pc.policy_version
                AND src.expected_points = 60
                AND src.observed_points >= 0
                AND src.gap_points >= 0
                AND src.observed_points <= src.expected_points
                AND src.gap_points <= src.expected_points
                AND (src.observed_points + src.gap_points) <= src.expected_points
                AND abs((src.coverage_ratio::double precision - (src.observed_points::double precision / src.expected_points::double precision))) < 0.000001
          ))
      )
    ORDER BY pc.bucket_ts, pc.check_id
    LIMIT %(batch_size)s
    FOR UPDATE SKIP LOCKED
), source_15min AS (
    SELECT DISTINCT ON (src.bucket_ts, src.meter_urn, src.measurement) src.*, checks.check_id
    FROM eligible_checks AS checks
    JOIN {LIVE_MEASUREMENT_15MIN} AS src
      ON checks.source_table = '{LIVE_MEASUREMENT_15MIN}'
     AND src.bucket_ts = checks.bucket_ts
     AND src.meter_urn = checks.meter_urn
     AND src.measurement = checks.measurement
     AND src.resolution = '15min'
     AND src.policy_version = checks.policy_version
     AND src.expected_points = 15
     AND src.observed_points >= 0
     AND src.gap_points >= 0
     AND src.observed_points <= src.expected_points
     AND src.gap_points <= src.expected_points
     AND (src.observed_points + src.gap_points) <= src.expected_points
     AND abs((src.coverage_ratio::double precision - (src.observed_points::double precision / src.expected_points::double precision))) < 0.000001
    ORDER BY src.bucket_ts, src.meter_urn, src.measurement, checks.check_id DESC
), promoted_15min AS (
    INSERT INTO {CANONICAL_MEASUREMENT_15MIN} (
        bucket_ts, resolution, meter_urn, measurement, value, unit,
        aggregation_policy, expected_points, observed_points, gap_points,
        coverage_ratio, mask_code, quality_code, quality_summary, provenance,
        source_event_ids, source_run_id, promotion_id, lineage_key, loaded_at
    )
    SELECT
        bucket_ts, '15min', meter_urn, measurement, value, unit,
        aggregation_policy, expected_points, observed_points, gap_points,
        coverage_ratio::double precision,
        COALESCE(mask_code, 'observed'),
        COALESCE(quality_code, 'observed_mean'),
        quality_summary,
        provenance || jsonb_build_object(
            'promotion_id', %(promotion_id)s::text,
            'approval_id', %(approval_id)s::text,
            'promotion_check_id', check_id,
            'source_table', '{LIVE_MEASUREMENT_15MIN}',
            'canonical_write', true
        ),
        source_event_ids,
        COALESCE(source_run_id, 'unknown'),
        %(promotion_id)s,
        COALESCE(lineage_key, check_id::text),
        now()
    FROM source_15min
    ON CONFLICT (bucket_ts, meter_urn, measurement)
    DO UPDATE SET
        value = EXCLUDED.value,
        unit = EXCLUDED.unit,
        aggregation_policy = EXCLUDED.aggregation_policy,
        expected_points = EXCLUDED.expected_points,
        observed_points = EXCLUDED.observed_points,
        gap_points = EXCLUDED.gap_points,
        coverage_ratio = EXCLUDED.coverage_ratio,
        mask_code = EXCLUDED.mask_code,
        quality_code = EXCLUDED.quality_code,
        quality_summary = EXCLUDED.quality_summary,
        provenance = EXCLUDED.provenance,
        source_event_ids = EXCLUDED.source_event_ids,
        source_run_id = EXCLUDED.source_run_id,
        promotion_id = EXCLUDED.promotion_id,
        lineage_key = EXCLUDED.lineage_key,
        loaded_at = EXCLUDED.loaded_at
    RETURNING bucket_ts, meter_urn, measurement
), source_1h AS (
    SELECT DISTINCT ON (src.bucket_ts, src.meter_urn, src.measurement) src.*, checks.check_id
    FROM eligible_checks AS checks
    JOIN {LIVE_MEASUREMENT_1H} AS src
      ON checks.source_table = '{LIVE_MEASUREMENT_1H}'
     AND src.bucket_ts = checks.bucket_ts
     AND src.meter_urn = checks.meter_urn
     AND src.measurement = checks.measurement
     AND src.resolution = '1h'
     AND src.policy_version = checks.policy_version
     AND src.expected_points = 60
     AND src.observed_points >= 0
     AND src.gap_points >= 0
     AND src.observed_points <= src.expected_points
     AND src.gap_points <= src.expected_points
     AND (src.observed_points + src.gap_points) <= src.expected_points
     AND abs((src.coverage_ratio::double precision - (src.observed_points::double precision / src.expected_points::double precision))) < 0.000001
    ORDER BY src.bucket_ts, src.meter_urn, src.measurement, checks.check_id DESC
), promoted_1h AS (
    INSERT INTO {CANONICAL_MEASUREMENT_1H} (
        bucket_ts, resolution, meter_urn, measurement, value, unit,
        aggregation_policy, expected_points, observed_points, gap_points,
        coverage_ratio, mask_code, quality_code, quality_summary, provenance,
        source_event_ids, source_run_id, promotion_id, lineage_key, loaded_at
    )
    SELECT
        bucket_ts, '1h', meter_urn, measurement, value, unit,
        aggregation_policy, expected_points, observed_points, gap_points,
        coverage_ratio::double precision,
        COALESCE(mask_code, 'observed'),
        COALESCE(quality_code, 'observed_mean'),
        quality_summary,
        provenance || jsonb_build_object(
            'promotion_id', %(promotion_id)s::text,
            'approval_id', %(approval_id)s::text,
            'promotion_check_id', check_id,
            'source_table', '{LIVE_MEASUREMENT_1H}',
            'canonical_write', true
        ),
        source_event_ids,
        COALESCE(source_run_id, 'unknown'),
        %(promotion_id)s,
        COALESCE(lineage_key, check_id::text),
        now()
    FROM source_1h
    ON CONFLICT (bucket_ts, meter_urn, measurement)
    DO UPDATE SET
        value = EXCLUDED.value,
        unit = EXCLUDED.unit,
        aggregation_policy = EXCLUDED.aggregation_policy,
        expected_points = EXCLUDED.expected_points,
        observed_points = EXCLUDED.observed_points,
        gap_points = EXCLUDED.gap_points,
        coverage_ratio = EXCLUDED.coverage_ratio,
        mask_code = EXCLUDED.mask_code,
        quality_code = EXCLUDED.quality_code,
        quality_summary = EXCLUDED.quality_summary,
        provenance = EXCLUDED.provenance,
        source_event_ids = EXCLUDED.source_event_ids,
        source_run_id = EXCLUDED.source_run_id,
        promotion_id = EXCLUDED.promotion_id,
        lineage_key = EXCLUDED.lineage_key,
        loaded_at = EXCLUDED.loaded_at
    RETURNING bucket_ts, meter_urn, measurement
), promoted_checks AS (
    SELECT check_id FROM source_15min
    UNION
    SELECT check_id FROM source_1h
), marked_checks AS (
    UPDATE {LIVE_PROMOTION_CHECK} AS pc
    SET evidence = pc.evidence || jsonb_build_object(
            'canonical_write', true,
            'promotion_id', %(promotion_id)s::text,
            'approval_id', %(approval_id)s::text,
            'promoted_at', now()
        ),
        checked_at = now()
    FROM promoted_checks AS done
    WHERE pc.check_id = done.check_id
    RETURNING pc.check_id
)
SELECT
    (SELECT count(*) FROM eligible_checks)::integer AS promotion_check_count,
    (SELECT count(*) FROM promoted_15min)::integer AS promoted_15min_count,
    (SELECT count(*) FROM promoted_1h)::integer AS promoted_1h_count,
    (SELECT count(*) FROM marked_checks)::integer AS marked_promotion_check_count
""".strip()
    return CanonicalPromotionCommand(
        sql=sql,
        params={
            "promotion_id": promotion_id,
            "approval_id": approval_id,
            "batch_size": batch_size,
            "min_coverage_ratio": min_coverage_ratio,
            "max_bucket_ts": max_bucket_ts,
        },
        source_tables=normalized_sources,
        target_tables=target_tables,
        approval_id=approval_id,
        promotion_id=promotion_id,
        batch_size=batch_size,
    )


def execute_canonical_promotion_command(
    command: CanonicalPromotionCommand,
    *,
    config: PsycopgConnectionConfig | None = None,
    allow_write: bool = False,
    env: Mapping[str, str] | None = None,
) -> CanonicalPromotionResult:
    """Execute a prepared promotion command only when the double gate passes."""

    gate_errors = _write_gate_errors(command=command, allow_write=allow_write, env=env)
    if gate_errors:
        return CanonicalPromotionResult(ok=False, attempted=False, blocked=True, errors=gate_errors)
    runtime_env = dict(os.environ if env is None else env)
    db_config = config or load_postgres_config_from_env(runtime_env)
    psycopg = import_module("psycopg")
    rows_mod = import_module("psycopg.rows")
    with psycopg.connect(**db_config.connect_kwargs(), row_factory=rows_mod.dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(command.sql, command.params)
            row = cur.fetchone() or {}
        conn.commit()
    return CanonicalPromotionResult(
        ok=True,
        attempted=True,
        promoted_15min_count=int(row.get("promoted_15min_count", 0)),
        promoted_1h_count=int(row.get("promoted_1h_count", 0)),
        promotion_check_count=int(row.get("promotion_check_count", 0)),
        marked_promotion_check_count=int(row.get("marked_promotion_check_count", 0)),
    )


def _write_gate_errors(*, command: CanonicalPromotionCommand, allow_write: bool, env: Mapping[str, str] | None) -> tuple[str, ...]:
    errors: list[str] = []
    runtime_env = os.environ if env is None else env
    if not allow_write:
        errors.append("allow_write_required")
    if runtime_env.get(command.write_gate_env) != "1":
        errors.append(f"{command.write_gate_env}=1_required")
    if not command.approval_id:
        errors.append("approval_id_required")
    if not command.promotion_id:
        errors.append("promotion_id_required")
    return tuple(errors)


def _validate_source_tables(source_tables: Sequence[str]) -> None:
    if not source_tables:
        raise ValueError("at least one source table is required")
    for source in source_tables:
        if source in CANONICAL_PROMOTION_FORBIDDEN_SOURCE_TABLES or source.startswith("mart."):
            raise ValueError(f"canonical promotion must not use mart/peak source table: {source}")
        if source.startswith("reference.") or "corrected" in source:
            raise ValueError(f"canonical promotion must not use reference/corrected source table: {source}")
        if source not in CANONICAL_PROMOTION_ALLOWED_SOURCE_TABLES:
            raise ValueError(f"unsupported canonical promotion source table: {source}")


def _target_table_for_source(source_table: str) -> str:
    if source_table == LIVE_MEASUREMENT_15MIN:
        return CANONICAL_MEASUREMENT_15MIN
    if source_table == LIVE_MEASUREMENT_1H:
        return CANONICAL_MEASUREMENT_1H
    raise ValueError(f"unsupported canonical promotion source table: {source_table}")


def _require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")


__all__ = [
    "CANONICAL_PROMOTION_ALLOWED_SOURCE_TABLES",
    "CANONICAL_PROMOTION_FORBIDDEN_SOURCE_TABLES",
    "CANONICAL_PROMOTION_WRITE_ENV_FLAG",
    "CanonicalPromotionCommand",
    "CanonicalPromotionResult",
    "execute_canonical_promotion_command",
    "make_canonical_promotion_command",
]
