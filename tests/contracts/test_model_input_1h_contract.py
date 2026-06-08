from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cms.contracts.model_input_1h import (
    ELECTRIC_REQUIRED_FEATURES,
    HEAT_REQUIRED_FEATURES,
    HISTORY_HOURS,
    ModelInput1HRow,
    ModelInput1HValidationResult,
    ValidationIssue,
    assert_valid_model_input_1h,
    validate_model_input_1h,
)


def _hours(start: datetime, count: int) -> tuple[datetime, ...]:
    return tuple(start + timedelta(hours=offset) for offset in range(count))


def _row(meter_urn: str, meter_kind: str, ts: datetime, **features: float) -> ModelInput1HRow:
    return ModelInput1HRow(meter_urn=meter_urn, meter_kind=meter_kind, ts=ts, features=features)


def _valid_rows(meter_urn: str = "meter:electric:001", meter_kind: str = "electric") -> tuple[ModelInput1HRow, ...]:
    start = datetime(2026, 6, 1, tzinfo=UTC)
    required = ELECTRIC_REQUIRED_FEATURES if meter_kind == "electric" else HEAT_REQUIRED_FEATURES
    return tuple(_row(meter_urn, meter_kind, ts, **{feature: float(index + 1) for feature in required}) for index, ts in enumerate(_hours(start, HISTORY_HOURS)))


def _issue_names(result: ModelInput1HValidationResult) -> set[str]:
    return {issue.issue for issue in result.issues}


def test_valid_electric_and_heat_histories_pass_contract() -> None:
    rows = (*_valid_rows("meter:electric:001", "electric"), *_valid_rows("meter:heat:001", "heat"))

    assert validate_model_input_1h(rows).ok is True
    assert_valid_model_input_1h(rows)


def test_rejects_non_1h_aligned_timestamp() -> None:
    rows = list(_valid_rows())
    rows[0] = _row("meter:electric:001", "electric", datetime(2026, 6, 1, 0, 15, tzinfo=UTC), P=1.0, U1=1.0, PF=1.0)

    result = validate_model_input_1h(rows)

    assert result.ok is False
    assert ValidationIssue(meter_urn="meter:electric:001", ts=rows[0].ts, issue="ts_not_1h_aligned") in result.issues


def test_rejects_duplicate_meter_urn_and_ts() -> None:
    rows = list(_valid_rows())
    rows[1] = rows[0]

    result = validate_model_input_1h(rows)

    assert result.ok is False
    assert ValidationIssue(meter_urn="meter:electric:001", ts=rows[0].ts, issue="duplicate_meter_urn_ts") in result.issues


def test_reports_missing_electric_required_feature_as_structured_issue() -> None:
    rows = list(_valid_rows())
    ts = rows[-1].ts
    rows[-1] = _row("meter:electric:001", "electric", ts, P=168.0, U1=220.0)

    result = validate_model_input_1h(rows)

    assert result.ok is False
    assert result.missing_features == (ValidationIssue(meter_urn="meter:electric:001", ts=ts, issue="missing_feature", feature="PF"),)


def test_reports_missing_heat_required_feature_as_structured_issue() -> None:
    rows = list(_valid_rows("meter:heat:001", "heat"))
    ts = rows[-1].ts
    rows[-1] = _row("meter:heat:001", "heat", ts, P=168.0, qv=10.0)

    result = validate_model_input_1h(rows)

    assert result.ok is False
    assert result.missing_features == (ValidationIssue(meter_urn="meter:heat:001", ts=ts, issue="missing_feature", feature="Tdiff"),)


def test_requires_at_least_168_unique_hour_history_per_meter() -> None:
    start = datetime(2026, 6, 1, tzinfo=UTC)
    history_count = HISTORY_HOURS - 1
    rows = tuple(_row("meter:electric:001", "electric", ts, P=1.0, U1=1.0, PF=1.0) for ts in _hours(start, history_count))

    result = validate_model_input_1h(rows)

    assert result.ok is False
    assert ValidationIssue(
        meter_urn="meter:electric:001",
        ts=None,
        issue="insufficient_history_hours",
        expected=f">={HISTORY_HOURS}",
        observed=history_count,
    ) in result.issues


def test_rejects_167h_contiguous_recent_lookback_for_explicit_base_ts() -> None:
    base_ts = datetime(2026, 6, 7, 23, tzinfo=UTC)
    start = base_ts - timedelta(hours=HISTORY_HOURS - 2)
    rows = tuple(_row("meter:electric:001", "electric", ts, P=1.0, U1=1.0, PF=1.0) for ts in _hours(start, HISTORY_HOURS - 1))

    result = validate_model_input_1h(rows, base_ts=base_ts)

    assert result.ok is False
    assert ValidationIssue(
        meter_urn="meter:electric:001",
        ts=None,
        issue="insufficient_history_hours",
        expected=f">={HISTORY_HOURS}",
        observed=HISTORY_HOURS - 1,
    ) in result.issues


def test_rejects_sparse_2h_spaced_168_rows_with_missing_timestamp_and_gap_issues() -> None:
    start = datetime(2026, 6, 1, tzinfo=UTC)
    rows = tuple(
        _row("meter:electric:001", "electric", start + timedelta(hours=offset * 2), P=1.0, U1=1.0, PF=1.0)
        for offset in range(HISTORY_HOURS)
    )

    result = validate_model_input_1h(rows)

    assert result.ok is False
    assert {"missing_timestamp", "gap"}.issubset(_issue_names(result))
    assert any(issue.issue == "missing_timestamp" and issue.ts == rows[-1].ts - timedelta(hours=1) for issue in result.issues)
    assert any(issue.issue == "gap" and issue.expected == "1h" and issue.observed == "2h" for issue in result.issues)


def test_allows_more_than_168_unique_hour_history_per_meter() -> None:
    start = datetime(2026, 6, 1, tzinfo=UTC)
    rows = tuple(_row("meter:electric:001", "electric", ts, P=1.0, U1=1.0, PF=1.0) for ts in _hours(start, HISTORY_HOURS + 1))

    assert validate_model_input_1h(rows).ok is True


def test_allows_169h_when_last_168h_are_contiguous_to_explicit_base_ts() -> None:
    base_ts = datetime(2026, 6, 7, 23, tzinfo=UTC)
    start = base_ts - timedelta(hours=HISTORY_HOURS)
    rows = tuple(_row("meter:electric:001", "electric", ts, P=1.0, U1=1.0, PF=1.0) for ts in _hours(start, HISTORY_HOURS + 1))

    assert validate_model_input_1h(rows, base_ts=base_ts).ok is True


def test_rejects_rows_after_explicit_base_ts() -> None:
    base_ts = datetime(2026, 6, 7, 23, tzinfo=UTC)
    start = base_ts - timedelta(hours=HISTORY_HOURS - 1)
    rows = tuple(_row("meter:electric:001", "electric", ts, P=1.0, U1=1.0, PF=1.0) for ts in _hours(start, HISTORY_HOURS + 1))

    result = validate_model_input_1h(rows, base_ts=base_ts)

    assert result.ok is False
    assert ValidationIssue(
        meter_urn="meter:electric:001",
        ts=base_ts + timedelta(hours=1),
        issue="future_row_after_base_ts",
        expected=base_ts.isoformat(),
        observed=(base_ts + timedelta(hours=1)).isoformat(),
    ) in result.issues


def test_rejects_extra_feature_keys_for_electric_and_heat_meter_kinds() -> None:
    electric_rows = list(_valid_rows())
    electric_rows[-1] = _row("meter:electric:001", "electric", electric_rows[-1].ts, P=1.0, U1=1.0, PF=1.0, rolling_mean_24h=1.0)
    heat_rows = list(_valid_rows("meter:heat:001", "heat"))
    heat_rows[-1] = _row("meter:heat:001", "heat", heat_rows[-1].ts, P=1.0, qv=1.0, Tdiff=1.0, is_workday=1.0)

    result = validate_model_input_1h((*electric_rows, *heat_rows))

    assert result.ok is False
    assert ValidationIssue(
        meter_urn="meter:electric:001",
        ts=electric_rows[-1].ts,
        issue="unexpected_feature",
        feature="rolling_mean_24h",
        expected="P,U1,PF",
        observed="rolling_mean_24h",
    ) in result.issues
    assert ValidationIssue(
        meter_urn="meter:heat:001",
        ts=heat_rows[-1].ts,
        issue="unexpected_feature",
        feature="is_workday",
        expected="P,qv,Tdiff",
        observed="is_workday",
    ) in result.issues


@pytest.mark.parametrize("feature", ["diff_lag24", "diff_lag168", "actual", "error", "anomaly"])
def test_rejects_leakage_prone_pre_inference_feature_names(feature: str) -> None:
    rows = list(_valid_rows())
    rows[-1] = _row("meter:electric:001", "electric", rows[-1].ts, P=1.0, U1=1.0, PF=1.0, **{feature: 1.0})

    result = validate_model_input_1h(rows)

    assert result.ok is False
    assert any(issue.issue == "unexpected_feature" and issue.feature == feature for issue in result.issues)


def test_assert_valid_model_input_raises_structured_validation_error() -> None:
    rows = list(_valid_rows())
    rows[-1] = _row("meter:electric:001", "electric", rows[-1].ts, P=1.0, U1=1.0)

    with pytest.raises(ValueError, match="model_input_1h validation failed") as exc_info:
        assert_valid_model_input_1h(rows)

    assert exc_info.value.args[1].missing_features[0].feature == "PF"
