"""P-Max 15-minute forecast contracts.

This module reflects the attached P-Max champion-model DB integration
specification. It is import-safe and performs no database, network, Airflow,
Kafka, AWS, or filesystem I/O.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from cms.contracts.live_pipeline import (
    SOURCE_MODE_HYBRID_WARM_START,
    SOURCE_MODE_LIVE_OBSERVED,
    SOURCE_MODE_REFERENCE_BACKFILL,
    classify_source_mode,
    is_live_observed_source,
)

PMAX_FORECAST_INPUT_TABLE = "mart.peak_feature_15min"
PMAX_HOLDOUT_FILTER_VIEW = "mart.peak_feature_15min"
PMAX_FORECAST_TABLE = "mart.pmax_forecast_15min"
PMAX_FORECAST_INFERENCE_LOG_TABLE = "ops.pmax_log"
PMAX_FORECAST_EVALUATION_TABLE = "qa.pmax_eval"

PMAX_FORECAST_MODEL_GRAIN = "15min"
PMAX_FORECAST_WINDOW_POINTS = 96
PMAX_FORECAST_REQUIRED_HISTORY_WINDOWS = 288
PMAX_FORECAST_QUERY_HISTORY_DAYS = 14
PMAX_FORECAST_TARGET_MEASUREMENT = "P_max"
PMAX_FORECAST_HORIZON_MINUTES = (15, 30, 45, 60)
PMAX_FORECAST_REQUIRED_MEASUREMENTS = ("P", "U1", "PF")
PMAX_FORECAST_REQUIRED_AGGREGATES = ("P_mean", "P_max", "P_std", "U1_mean", "PF_mean")
PMAX_FORECAST_MIN_FEATURE_COVERAGE_RATIO = 1.0
PMAX_FORECAST_ARTIFACT_ADAPTER_STUB = "pmax_forecast_artifact_stub"
PMAX_FORECAST_PRODUCTION_RELEASE = "import_pmax_production_release_20260608"
PMAX_FORECAST_PRODUCTION_RELEASE_SHA256 = "fc3848ea0bb76afd75252d8fc32f189709b5f323629bfd069efaf86ddc58bd80"
PMAX_FORECAST_MODEL_VERSION = "v29"
PMAX_FORECAST_CANDIDATE_VERSIONS = ("v20", "v23", "v25", "v27")
PMAX_FORECAST_FEATURE_COLUMNS = (
    "P_mean",
    "P_max",
    "P_std",
    "U1_mean",
    "PF_mean",
    "hour_sin",
    "hour_cos",
    "dayofweek_sin",
    "P_max_lag_1",
    "P_max_lag_96",
    "P_max_lag_192",
    "P_max_roll_1h_mean",
    "P_max_roll_1h_max",
    "P_max_roll_1h_std",
    "P_max_roll_3h_mean",
    "P_max_roll_3h_max",
    "P_max_roll_6h_mean",
    "P_max_diff_1",
    "P_max_diff_4",
    "P_mean_diff_1",
    "U1_mean_diff_1",
    "PF_mean_diff_1",
)
PMAX_FEATURE_LATEST_SELECTION_SQL = """
WITH ranked_peak_features AS (
  SELECT
    pf.*,
    row_number() OVER (
      PARTITION BY window_ts, meter_urn, measurement
      ORDER BY created_at DESC NULLS LAST, run_id DESC NULLS LAST
    ) AS rn
  FROM mart.peak_feature_15min AS pf
  WHERE pf.source_mode = 'live_observed'
)
SELECT *
FROM ranked_peak_features
WHERE rn = 1
""".strip()
PMAX_FORECAST_LOGICAL_METER_SOURCES: Mapping[str, tuple[str, ...]] = {
    "V.Z81": ("V.Z81",),
    "V.Z82": ("V.Z82",),
    "H2.Z35x": ("H2.Z35", "H2.Z351"),
    "H2.Z36x": ("H2.Z36", "H2.Z361"),
}
PMAX_FORECAST_2023_HOLDOUT_START_UTC = datetime(2022, 12, 31, 15, 0, tzinfo=UTC)
PMAX_FORECAST_2023_HOLDOUT_END_UTC = datetime(2023, 12, 31, 15, 0, tzinfo=UTC)
PMAX_FORECAST_2023_LIVE_SOURCE_METERS = ("V.Z81", "V.Z82", "H2.Z351", "H2.Z361")
PMAX_FORECAST_2023_LOGICAL_METER_SOURCES: Mapping[str, tuple[str, ...]] = {
    "V.Z81": ("V.Z81",),
    "V.Z82": ("V.Z82",),
    "H2.Z35x": ("H2.Z351",),
    "H2.Z36x": ("H2.Z361",),
}
PMAX_FORECAST_CURRENT_DB_LOGICAL_METER_SOURCES: Mapping[str, tuple[str, ...]] = PMAX_FORECAST_2023_LOGICAL_METER_SOURCES

PmaxForecastRunStatus = Literal["success", "degraded", "failed"]


@dataclass(frozen=True)
class PmaxForecastRow:
    """One mart forecast row for one logical meter and one 15-minute horizon.

    ``target_ts`` is the predicted bucket end timestamp. The actual bucket start
    for post-hoc joins is ``target_ts - 15 minutes``.
    """

    logical_meter: str
    source_meter_urn: str
    base_ts: datetime
    input_end_ts: datetime
    target_ts: datetime
    horizon_minutes: int
    predicted_p_max: float
    created_at: datetime


@dataclass(frozen=True)
class PmaxForecastValidationIssue:
    """Structured validation issue for P-Max forecast rows."""

    issue: str
    field: str
    expected: str | int | float | None = None
    observed: str | int | float | None = None


@dataclass(frozen=True)
class PmaxFeatureReadinessRow:
    """One deployed ``mart.peak_feature_15min`` row used as P-Max input.

    The table is known to contain duplicate physical keys. Readiness validation
    therefore consumes these rows through :func:`select_latest_pmax_feature_rows`,
    selecting one row per ``(window_ts, meter_urn, measurement)`` by latest
    ``created_at`` then ``run_id``.
    """

    window_ts: datetime
    meter_urn: str
    measurement: str
    mean_value: float | int | None
    max_value: float | int | None
    min_value: float | int | None
    p95_value: float | int | None
    p99_value: float | int | None
    std_value: float | int | None
    last_value: float | int | None
    peak_ts: datetime
    peak_value: float | int | None
    observed_points: int
    expected_points: int
    coverage_ratio: float | int
    source_file: str
    run_id: str
    created_at: datetime | None
    source_layer: str | None = None
    source_mode: str | None = None
    provenance: Mapping[str, object] | None = None


@dataclass(frozen=True)
class PmaxFeatureReadinessIssue:
    """Structured readiness issue for P-Max input features."""

    issue: str
    logical_meter: str | None = None
    meter_urn: str | None = None
    measurement: str | None = None
    window_ts: datetime | None = None
    field: str | None = None
    expected: str | int | float | None = None
    observed: str | int | float | None = None


@dataclass(frozen=True)
class PmaxFeatureReadinessResult:
    """Side-effect-free P-Max feature readiness result."""

    base_ts: datetime
    input_end_ts: datetime
    selected_row_count: int
    issues: tuple[PmaxFeatureReadinessIssue, ...] = ()
    source_mode: str = SOURCE_MODE_LIVE_OBSERVED
    source_composition: Mapping[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def blocking_issues(self) -> tuple[PmaxFeatureReadinessIssue, ...]:
        return self.issues


@dataclass(frozen=True)
class PmaxForecastArtifactBoundary:
    """Repo-local boundary for a future P-Max Drive artifact adapter.

    No artifact lookup is performed here. Until the Drive artifact is verified,
    model-serving code can depend only on this explicit unavailable/stub boundary.
    """

    adapter_name: str = PMAX_FORECAST_ARTIFACT_ADAPTER_STUB
    drive_artifact_verified: bool = False
    external_io_enabled: bool = False
    artifact_uri: str | None = None
    model_version: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.drive_artifact_verified and self.artifact_uri and self.model_version and not self.external_io_enabled)


@dataclass(frozen=True)
class PmaxForecastRunLogContract:
    """Ops-side run log contract for quality/runtime status.

    Forecast values belong in ``mart``. Runtime execution status, failure reason,
    missing-value repair counts, and degraded flags belong in ``ops`` so they do
    not become model prediction facts.
    """

    table_name: str
    allowed_statuses: tuple[PmaxForecastRunStatus, ...]
    max_replacement_rows: int
    max_internal_missing_segments: int
    max_internal_interpolation_minutes: int
    latest_missing_single_bucket_policy: str
    latest_missing_30min_policy: str
    external_alert_thresholds_in_model_scope: bool


PMAX_FORECAST_RUN_LOG_CONTRACT = PmaxForecastRunLogContract(
    table_name=PMAX_FORECAST_INFERENCE_LOG_TABLE,
    allowed_statuses=("success", "degraded", "failed"),
    max_replacement_rows=4,
    max_internal_missing_segments=1,
    max_internal_interpolation_minutes=60,
    latest_missing_single_bucket_policy="previous_observation_degraded",
    latest_missing_30min_policy="fail",
    external_alert_thresholds_in_model_scope=False,
)


def validate_pmax_forecast_row(row: PmaxForecastRow) -> tuple[PmaxForecastValidationIssue, ...]:
    """Validate one P-Max mart forecast row.

    The checks are deliberately row-local and side-effect-free. Batch uniqueness
    remains the database/key contract: ``(logical_meter, base_ts, target_ts)``.
    """

    issues: list[PmaxForecastValidationIssue] = []
    if row.logical_meter not in PMAX_FORECAST_LOGICAL_METER_SOURCES:
        issues.append(
            PmaxForecastValidationIssue(
                issue="unsupported_logical_meter",
                field="logical_meter",
                expected="|".join(PMAX_FORECAST_LOGICAL_METER_SOURCES),
                observed=row.logical_meter,
            )
        )
    elif row.source_meter_urn not in pmax_allowed_source_meters(row.logical_meter, at_ts=row.input_end_ts):
        issues.append(
            PmaxForecastValidationIssue(
                issue="source_meter_not_allowed_for_logical_meter",
                field="source_meter_urn",
                expected="|".join(pmax_allowed_source_meters(row.logical_meter, at_ts=row.input_end_ts)),
                observed=row.source_meter_urn,
            )
        )

    if not _is_15min_aligned(row.base_ts):
        issues.append(PmaxForecastValidationIssue("base_ts_not_15min_aligned", "base_ts", "15min boundary", row.base_ts.isoformat()))
    if not _is_15min_aligned(row.input_end_ts):
        issues.append(PmaxForecastValidationIssue("input_end_ts_not_15min_aligned", "input_end_ts", "15min boundary", row.input_end_ts.isoformat()))
    if not _is_15min_aligned(row.target_ts):
        issues.append(PmaxForecastValidationIssue("target_ts_not_15min_aligned", "target_ts", "15min boundary", row.target_ts.isoformat()))

    if row.horizon_minutes not in PMAX_FORECAST_HORIZON_MINUTES:
        issues.append(
            PmaxForecastValidationIssue(
                "unsupported_horizon_minutes",
                "horizon_minutes",
                ",".join(str(horizon) for horizon in PMAX_FORECAST_HORIZON_MINUTES),
                row.horizon_minutes,
            )
        )
    else:
        expected_target_ts = row.base_ts + timedelta(minutes=row.horizon_minutes)
        if row.target_ts != expected_target_ts:
            issues.append(
                PmaxForecastValidationIssue(
                    "target_ts_must_equal_base_ts_plus_horizon",
                    "target_ts",
                    expected_target_ts.isoformat(),
                    row.target_ts.isoformat(),
                )
            )

    expected_input_end_ts = row.base_ts - timedelta(minutes=15)
    if row.input_end_ts != expected_input_end_ts:
        issues.append(
            PmaxForecastValidationIssue(
                "input_end_ts_must_be_base_ts_minus_15min",
                "input_end_ts",
                expected_input_end_ts.isoformat(),
                row.input_end_ts.isoformat(),
            )
        )

    if row.predicted_p_max < 0:
        issues.append(PmaxForecastValidationIssue("predicted_p_max_must_be_nonnegative", "predicted_p_max", 0, row.predicted_p_max))

    return tuple(issues)


def actual_window_ts_for_forecast(row: PmaxForecastRow) -> datetime:
    """Return the actual ``mart.peak_feature_15min.window_ts`` for evaluation."""

    return row.target_ts - timedelta(minutes=15)


def is_pmax_2023_live_holdout_window(ts: datetime) -> bool:
    """Return whether ``ts`` belongs to the R2 2023 KST live holdout window.

    The database migration defines the window as half-open
    ``[2023-01-01 00:00:00+09, 2024-01-01 00:00:00+09)``. Internally the pure
    Python contract compares in UTC so callers may pass any aware timezone.
    Naive timestamps retain legacy import-safe behaviour and are interpreted by
    calendar year only instead of raising during validation.
    """

    if ts.tzinfo is None or ts.utcoffset() is None:
        return ts.year == 2023
    utc_ts = ts.astimezone(UTC)
    return PMAX_FORECAST_2023_HOLDOUT_START_UTC <= utc_ts < PMAX_FORECAST_2023_HOLDOUT_END_UTC


def pmax_allowed_source_meters(logical_meter: str, *, at_ts: datetime | None = None) -> tuple[str, ...]:
    """Return source meters allowed for a logical P-Max meter at a timestamp.

    Replacement predecessors ``H2.Z35``/``H2.Z36`` remain represented in the
    broad model contract for non-R2 compatibility, but they must not be used for
    the R2 2023 live-observed holdout. During that window the corrected meter
    basis is exactly ``V.Z81,V.Z82,H2.Z351,H2.Z361``.
    """

    source_map = PMAX_FORECAST_2023_LOGICAL_METER_SOURCES if at_ts is not None and is_pmax_2023_live_holdout_window(at_ts) else PMAX_FORECAST_LOGICAL_METER_SOURCES
    return source_map[logical_meter]


def pmax_live_observed_source_meters(logical_meter: str) -> tuple[str, ...]:
    """Return the current DB source meter(s) for live-observed P-Max serving.

    The model/artifact contract keeps legacy replacement aliases
    ``H2.Z35``/``H2.Z36`` for non-R2 compatibility. Runtime DB reads for
    live-observed serving use the current meter basis instead:
    ``V.Z81,V.Z82,H2.Z351,H2.Z361``.
    """

    return PMAX_FORECAST_CURRENT_DB_LOGICAL_METER_SOURCES[logical_meter]


def select_latest_pmax_feature_rows(rows: Iterable[PmaxFeatureReadinessRow], *, source_mode: str = SOURCE_MODE_LIVE_OBSERVED) -> tuple[PmaxFeatureReadinessRow, ...]:
    """Select latest duplicate physical feature keys for P-Max readiness.

    Mirrors the deployed-table SQL contract: ``row_number()`` partitioned by
    ``window_ts, meter_urn, measurement`` and ordered by ``created_at desc,
    run_id desc``. This function is pure and accepts already-fetched rows only.
    """

    _require_pmax_serving_source_mode(source_mode)
    latest_by_key: dict[tuple[datetime, str, str], PmaxFeatureReadinessRow] = {}
    for row in rows:
        key = (row.window_ts, row.meter_urn, row.measurement)
        existing = latest_by_key.get(key)
        if existing is None or _feature_sort_key(row, source_mode=source_mode) > _feature_sort_key(existing, source_mode=source_mode):
            latest_by_key[key] = row
    return tuple(sorted(latest_by_key.values(), key=lambda row: (row.window_ts, row.meter_urn, row.measurement)))


def validate_pmax_feature_readiness(
    rows: Iterable[PmaxFeatureReadinessRow],
    *,
    base_ts: datetime,
    logical_meters: Sequence[str] = tuple(PMAX_FORECAST_LOGICAL_METER_SOURCES),
    required_measurements: Sequence[str] = PMAX_FORECAST_REQUIRED_MEASUREMENTS,
    window_points: int = PMAX_FORECAST_WINDOW_POINTS,
    min_coverage_ratio: float = PMAX_FORECAST_MIN_FEATURE_COVERAGE_RATIO,
    source_mode: str = SOURCE_MODE_LIVE_OBSERVED,
) -> PmaxFeatureReadinessResult:
    """Validate P-Max model-serving feature readiness from in-memory rows.

    Contract scope is intentionally repo-local and import-safe. It does not read
    the database or Drive artifacts. Callers pass candidate rows from
    ``mart.peak_feature_15min``; this helper performs duplicate-key latest
    selection, checks 15-minute alignment, verifies the recent 96-point lookback
    ending at ``base_ts - 15 minutes``, and blocks mixed replacement sources.
    """

    _require_pmax_serving_source_mode(source_mode)
    original_rows = tuple(rows)
    selected_rows = select_latest_pmax_feature_rows(original_rows, source_mode=source_mode)
    input_end_ts = base_ts - timedelta(minutes=15)
    expected_windows = tuple(input_end_ts - timedelta(minutes=15 * offset) for offset in reversed(range(window_points)))
    expected_window_set = set(expected_windows)
    issues: list[PmaxFeatureReadinessIssue] = list(_duplicate_latest_ambiguous_issues(original_rows, source_mode=source_mode))

    if not _is_15min_aligned(base_ts):
        issues.append(PmaxFeatureReadinessIssue("base_ts_not_15min_aligned", field="base_ts", expected="15min boundary", observed=base_ts.isoformat()))
    if window_points <= 0:
        issues.append(PmaxFeatureReadinessIssue("window_points_must_be_positive", field="window_points", expected=">0", observed=window_points))
    if not selected_rows:
        issues.append(PmaxFeatureReadinessIssue("no_feature_rows", expected="mart.peak_feature_15min rows", observed=0))

    rows_by_logical_measurement: dict[tuple[str, str], list[PmaxFeatureReadinessRow]] = defaultdict(list)
    for row in selected_rows:
        issues.extend(_validate_feature_row_fields(row, min_coverage_ratio=min_coverage_ratio, source_mode=source_mode))
        for logical_meter, broad_allowed_sources in PMAX_FORECAST_LOGICAL_METER_SOURCES.items():
            allowed_sources = pmax_allowed_source_meters(logical_meter, at_ts=row.window_ts)
            if (
                logical_meter in logical_meters
                and row.meter_urn in broad_allowed_sources
                and row.measurement in required_measurements
                and row.window_ts in expected_window_set
                and row.meter_urn not in allowed_sources
            ):
                issues.append(
                    PmaxFeatureReadinessIssue(
                        "source_meter_not_allowed_for_2023_holdout",
                        logical_meter=logical_meter,
                        meter_urn=row.meter_urn,
                        measurement=row.measurement,
                        window_ts=row.window_ts,
                        field="meter_urn",
                        expected="|".join(allowed_sources),
                        observed=row.meter_urn,
                    )
                )
            if logical_meter in logical_meters and row.meter_urn in allowed_sources and row.measurement in required_measurements and row.window_ts in expected_window_set:
                rows_by_logical_measurement[(logical_meter, row.measurement)].append(row)

    for logical_meter in logical_meters:
        broad_allowed_sources = PMAX_FORECAST_LOGICAL_METER_SOURCES.get(logical_meter)
        if broad_allowed_sources is None:
            issues.append(
                PmaxFeatureReadinessIssue(
                    "unsupported_logical_meter",
                    logical_meter=logical_meter,
                    field="logical_meter",
                    expected="|".join(PMAX_FORECAST_LOGICAL_METER_SOURCES),
                    observed=logical_meter,
                )
            )
            continue

        logical_rows = [
            row
            for row in selected_rows
            if row.meter_urn in pmax_allowed_source_meters(logical_meter, at_ts=row.window_ts)
            and row.measurement in required_measurements
            and row.window_ts in expected_window_set
        ]
        observed_sources = tuple(sorted({row.meter_urn for row in logical_rows}))
        if len(observed_sources) > 1:
            issues.append(
                PmaxFeatureReadinessIssue(
                    "mixed_source_meters_for_logical_meter",
                    logical_meter=logical_meter,
                    field="meter_urn",
                    expected="single source from " + "|".join(broad_allowed_sources),
                    observed="|".join(observed_sources),
                )
            )

        for measurement in required_measurements:
            by_window = {row.window_ts: row for row in rows_by_logical_measurement.get((logical_meter, measurement), ())}
            for expected_window in expected_windows:
                if expected_window not in by_window:
                    issues.append(
                        PmaxFeatureReadinessIssue(
                            "missing_feature_window",
                            logical_meter=logical_meter,
                            measurement=measurement,
                            window_ts=expected_window,
                            expected="present",
                            observed="missing",
                        )
                    )

    return PmaxFeatureReadinessResult(
        base_ts=base_ts,
        input_end_ts=input_end_ts,
        selected_row_count=len(selected_rows),
        issues=tuple(issues),
        source_mode=source_mode,
        source_composition=_source_composition(selected_rows),
    )


def _duplicate_latest_ambiguous_issues(rows: Iterable[PmaxFeatureReadinessRow], *, source_mode: str = SOURCE_MODE_LIVE_OBSERVED) -> tuple[PmaxFeatureReadinessIssue, ...]:
    grouped: dict[tuple[datetime, str, str], list[PmaxFeatureReadinessRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.window_ts, row.meter_urn, row.measurement)].append(row)

    issues: list[PmaxFeatureReadinessIssue] = []
    for (window_ts, meter_urn, measurement), candidates in grouped.items():
        if len(candidates) <= 1:
            continue
        max_key = max(_feature_sort_key(candidate, source_mode=source_mode) for candidate in candidates)
        winners = [candidate for candidate in candidates if _feature_sort_key(candidate, source_mode=source_mode) == max_key]
        if len(winners) > 1:
            issues.append(
                PmaxFeatureReadinessIssue(
                    "duplicate_latest_ambiguous",
                    meter_urn=meter_urn,
                    measurement=measurement,
                    window_ts=window_ts,
                    field="created_at/run_id",
                    expected="unique latest row",
                    observed=f"{len(winners)} tied latest rows",
                )
            )
    return tuple(issues)


def _require_pmax_serving_source_mode(source_mode: str) -> None:
    allowed = (SOURCE_MODE_LIVE_OBSERVED, SOURCE_MODE_REFERENCE_BACKFILL, SOURCE_MODE_HYBRID_WARM_START)
    if source_mode not in allowed:
        raise ValueError("P-Max serving source_mode must be one of: " + ",".join(allowed))


def _source_composition(rows: Iterable[PmaxFeatureReadinessRow]) -> Mapping[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = row.source_mode or "null_source_mode"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _is_15min_aligned(ts: datetime) -> bool:
    return ts.minute % 15 == 0 and ts.second == 0 and ts.microsecond == 0


def _feature_sort_key(row: PmaxFeatureReadinessRow, *, source_mode: str = SOURCE_MODE_LIVE_OBSERVED) -> tuple[int, datetime, str]:
    return (_source_mode_priority(row, source_mode=source_mode), row.created_at or datetime.min.replace(tzinfo=row.window_ts.tzinfo), row.run_id)


def _source_mode_priority(row: PmaxFeatureReadinessRow, *, source_mode: str) -> int:
    if source_mode == SOURCE_MODE_HYBRID_WARM_START:
        if row.source_mode == SOURCE_MODE_LIVE_OBSERVED:
            return 2
        if row.source_mode == SOURCE_MODE_REFERENCE_BACKFILL:
            return 1
        return 0
    if source_mode == SOURCE_MODE_REFERENCE_BACKFILL:
        if row.source_mode == SOURCE_MODE_REFERENCE_BACKFILL:
            return 2
        if row.source_mode is None:
            return 1
        return 0
    return 0


def _validate_feature_row_fields(row: PmaxFeatureReadinessRow, *, min_coverage_ratio: float, source_mode: str) -> tuple[PmaxFeatureReadinessIssue, ...]:
    issues: list[PmaxFeatureReadinessIssue] = []
    if not _is_15min_aligned(row.window_ts):
        issues.append(_feature_issue("window_ts_not_15min_aligned", row, field="window_ts", expected="15min boundary", observed=row.window_ts.isoformat()))
    if not (row.window_ts <= row.peak_ts < row.window_ts + timedelta(minutes=15)):
        issues.append(_feature_issue("peak_ts_outside_window", row, field="peak_ts", expected="within 15min window", observed=row.peak_ts.isoformat()))
    if row.measurement not in PMAX_FORECAST_REQUIRED_MEASUREMENTS:
        issues.append(
            _feature_issue(
                "unexpected_measurement",
                row,
                field="measurement",
                expected="|".join(PMAX_FORECAST_REQUIRED_MEASUREMENTS),
                observed=row.measurement,
            )
        )
    if row.expected_points <= 0:
        issues.append(_feature_issue("expected_points_must_be_positive", row, field="expected_points", expected=">0", observed=row.expected_points))
    if row.observed_points < 0 or row.observed_points > row.expected_points:
        issues.append(_feature_issue("observed_points_out_of_range", row, field="observed_points", expected="0..expected_points", observed=row.observed_points))
    if row.coverage_ratio < min_coverage_ratio:
        issues.append(_feature_issue("coverage_ratio_below_threshold", row, field="coverage_ratio", expected=min_coverage_ratio, observed=float(row.coverage_ratio)))
    issues.extend(_validate_feature_row_source_boundary(row, serving_source_mode=source_mode))
    for field_name in _required_numeric_fields(row.measurement):
        if _is_missing_numeric(getattr(row, field_name)):
            issues.append(_feature_issue("missing_required_aggregate", row, field=field_name, expected="finite numeric", observed="missing"))
    return tuple(issues)


def _validate_feature_row_source_boundary(row: PmaxFeatureReadinessRow, *, serving_source_mode: str) -> tuple[PmaxFeatureReadinessIssue, ...]:
    issues: list[PmaxFeatureReadinessIssue] = []
    inferred_mode: str | None = None
    if row.source_layer:
        try:
            inferred_mode = classify_source_mode(row.source_layer)
        except ValueError as exc:
            if serving_source_mode == SOURCE_MODE_LIVE_OBSERVED or row.source_mode == SOURCE_MODE_LIVE_OBSERVED:
                issues.append(_feature_issue("source_layer_required", row, field="source_layer", expected=SOURCE_MODE_LIVE_OBSERVED, observed=str(exc)))
    elif serving_source_mode == SOURCE_MODE_LIVE_OBSERVED or row.source_mode == SOURCE_MODE_LIVE_OBSERVED:
        issues.append(_feature_issue("source_layer_required", row, field="source_layer", expected=SOURCE_MODE_LIVE_OBSERVED, observed=row.source_layer))

    if row.source_mode and row.source_mode not in {SOURCE_MODE_LIVE_OBSERVED, SOURCE_MODE_REFERENCE_BACKFILL}:
        issues.append(_feature_issue("unsupported_source_mode", row, field="source_mode", expected=f"{SOURCE_MODE_LIVE_OBSERVED}|{SOURCE_MODE_REFERENCE_BACKFILL}", observed=row.source_mode))
    if serving_source_mode == SOURCE_MODE_LIVE_OBSERVED and not row.source_mode:
        issues.append(_feature_issue("source_mode_required", row, field="source_mode", expected=SOURCE_MODE_LIVE_OBSERVED, observed=row.source_mode))
    if inferred_mode is not None and row.source_mode is not None and row.source_mode != inferred_mode:
        issues.append(_feature_issue("source_mode_mismatch", row, field="source_mode", expected=inferred_mode, observed=row.source_mode))

    if serving_source_mode == SOURCE_MODE_LIVE_OBSERVED:
        if inferred_mode == SOURCE_MODE_REFERENCE_BACKFILL or (row.source_layer and row.source_mode and not is_live_observed_source(row.source_layer, row.source_mode)):
            issues.append(_feature_issue("reference_source_for_live_serving", row, field="source_layer", expected=SOURCE_MODE_LIVE_OBSERVED, observed=row.source_layer))
        if "corrected_resampled" in row.source_file.lower():
            issues.append(_feature_issue("reference_source_file_for_live_serving", row, field="source_file", expected="live/canonical observed lineage", observed=row.source_file))
        if not row.provenance:
            issues.append(_feature_issue("provenance_required", row, field="provenance", expected="non-empty source provenance", observed="missing"))
    elif serving_source_mode == SOURCE_MODE_REFERENCE_BACKFILL:
        if row.source_mode == SOURCE_MODE_LIVE_OBSERVED or inferred_mode == SOURCE_MODE_LIVE_OBSERVED:
            issues.append(_feature_issue("live_source_for_reference_backfill", row, field="source_mode", expected=SOURCE_MODE_REFERENCE_BACKFILL, observed=row.source_mode or inferred_mode))
    elif row.source_mode == SOURCE_MODE_LIVE_OBSERVED:
        if "corrected_resampled" in row.source_file.lower():
            issues.append(_feature_issue("reference_source_file_labeled_live_observed", row, field="source_file", expected="live observed lineage", observed=row.source_file))
        if not row.provenance:
            issues.append(_feature_issue("provenance_required", row, field="provenance", expected="non-empty source provenance", observed="missing"))
    return tuple(issues)


def _required_numeric_fields(measurement: str) -> tuple[str, ...]:
    if measurement == "P":
        return ("mean_value", "max_value", "std_value")
    if measurement in {"U1", "PF"}:
        return ("mean_value",)
    return ()


def _is_missing_numeric(value: float | int | None) -> bool:
    if value is None:
        return True
    return isinstance(value, float) and math.isnan(value)


def _feature_issue(
    issue: str,
    row: PmaxFeatureReadinessRow,
    *,
    field: str,
    expected: str | int | float | None,
    observed: str | int | float | None,
) -> PmaxFeatureReadinessIssue:
    return PmaxFeatureReadinessIssue(
        issue=issue,
        meter_urn=row.meter_urn,
        measurement=row.measurement,
        window_ts=row.window_ts,
        field=field,
        expected=expected,
        observed=observed,
    )


__all__ = [
    "PMAX_FORECAST_EVALUATION_TABLE",
    "PMAX_FEATURE_LATEST_SELECTION_SQL",
    "PMAX_FORECAST_ARTIFACT_ADAPTER_STUB",
    "PMAX_FORECAST_2023_HOLDOUT_END_UTC",
    "PMAX_FORECAST_2023_HOLDOUT_START_UTC",
    "PMAX_FORECAST_2023_LIVE_SOURCE_METERS",
    "PMAX_FORECAST_2023_LOGICAL_METER_SOURCES",
    "PMAX_FORECAST_CANDIDATE_VERSIONS",
    "PMAX_FORECAST_CURRENT_DB_LOGICAL_METER_SOURCES",
    "PMAX_FORECAST_FEATURE_COLUMNS",
    "PMAX_FORECAST_TABLE",
    "PMAX_FORECAST_HORIZON_MINUTES",
    "PMAX_FORECAST_INFERENCE_LOG_TABLE",
    "PMAX_FORECAST_INPUT_TABLE",
    "PMAX_FORECAST_LOGICAL_METER_SOURCES",
    "PMAX_FORECAST_MIN_FEATURE_COVERAGE_RATIO",
    "PMAX_FORECAST_MODEL_GRAIN",
    "PMAX_FORECAST_MODEL_VERSION",
    "PMAX_FORECAST_PRODUCTION_RELEASE",
    "PMAX_FORECAST_PRODUCTION_RELEASE_SHA256",
    "PMAX_FORECAST_QUERY_HISTORY_DAYS",
    "PMAX_FORECAST_REQUIRED_AGGREGATES",
    "PMAX_FORECAST_REQUIRED_HISTORY_WINDOWS",
    "PMAX_FORECAST_REQUIRED_MEASUREMENTS",
    "PMAX_FORECAST_RUN_LOG_CONTRACT",
    "PMAX_FORECAST_TARGET_MEASUREMENT",
    "PMAX_FORECAST_WINDOW_POINTS",
    "PmaxFeatureReadinessIssue",
    "PmaxFeatureReadinessResult",
    "PmaxFeatureReadinessRow",
    "PmaxForecastArtifactBoundary",
    "PmaxForecastRow",
    "PmaxForecastRunLogContract",
    "PmaxForecastRunStatus",
    "PmaxForecastValidationIssue",
    "actual_window_ts_for_forecast",
    "is_pmax_2023_live_holdout_window",
    "pmax_allowed_source_meters",
    "pmax_live_observed_source_meters",
    "select_latest_pmax_feature_rows",
    "validate_pmax_feature_readiness",
    "validate_pmax_forecast_row",
]
