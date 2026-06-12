from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from . import operations, training


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_ROOT = operations.DEPLOYED_ROOT
DEFAULT_LOOKBACK_DAYS = 14
EXPECTED_OUTPUT_RANGE = "60min"
EXPECTED_HORIZON_STEPS = 4
EXPECTED_CANDIDATES = ["v20", "v23", "v25", "v27"]


@dataclass(frozen=True)
class InferenceArtifacts:
    manifest: dict[str, Any]
    models: dict[str, Any]
    weights: dict[str, float]


@dataclass(frozen=True)
class InferenceWindow:
    x: np.ndarray
    logical_meter: str
    source_meter_urn: str
    segment_id: int
    input_start_ts: pd.Timestamp
    input_end_ts: pd.Timestamp
    last_import_p_max: float
    n_input_rows: int
    imputed_rows: int
    interpolation_rows: int
    forward_filled_rows: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run no-v16 v29 inference for four future 15-minute import P_max values."
    )
    parser.add_argument("--meter", required=True, choices=list(training.LOGICAL_METERS))
    parser.add_argument("--table", default=training.DEFAULT_TABLE)
    parser.add_argument("--model-root", type=Path, default=DEFAULT_MODEL_ROOT)
    parser.add_argument(
        "--as-of",
        help=(
            "Forecast request time on a 15-minute boundary. The final input bucket starts "
            "15 minutes earlier. Defaults to 15 minutes after the latest DB window_ts."
        ),
    )
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--csv-output",
        type=Path,
        help="Write four DB-shaped forecast rows to this CSV file.",
    )
    parser.add_argument(
        "--append-csv",
        action="store_true",
        help="Append to --csv-output instead of replacing it.",
    )
    parser.add_argument(
        "--verify-predictions",
        type=Path,
        help="Optional training test_predictions.csv used to verify exact prediction reproduction.",
    )
    parser.add_argument("--verify-row", type=int, default=-1)
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-2,
        help="Absolute tolerance for CSV reproduction checks. Default accounts for saved float rounding.",
    )
    return parser.parse_args()


def normalize_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError("Timestamp must include a timezone, for example 2023-06-01T12:00:00Z")
    return timestamp.tz_convert("UTC")


def artifact_paths(model_root: Path, meter: str) -> tuple[Path, Path, Path]:
    meter_dir = model_root / "input_24h" / "predict_60min" / meter
    return (
        meter_dir / "_candidate_models",
        meter_dir / "v29" / "manifest.json",
        meter_dir / "v29" / "ensemble_weights.csv",
    )


def load_artifacts(model_root: Path, meter: str) -> InferenceArtifacts:
    model_dir, manifest_path, weights_path = artifact_paths(model_root, meter)
    for required in [manifest_path, weights_path]:
        if not required.is_file():
            raise FileNotFoundError(f"Required inference artifact not found: {required}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks = {
        "logical_meter": meter,
        "output_range": EXPECTED_OUTPUT_RANGE,
        "horizon_steps": EXPECTED_HORIZON_STEPS,
        "input_hours": 24,
        "method": "v29",
    }
    for key, expected in checks.items():
        if manifest.get(key) != expected:
            raise ValueError(f"Manifest mismatch for {key}: expected {expected!r}, got {manifest.get(key)!r}")
    if manifest.get("feature_columns") != training.FEATURE_COLUMNS:
        raise ValueError("Manifest feature order does not match the inference feature order")
    if manifest.get("candidate_versions") != EXPECTED_CANDIDATES:
        raise ValueError(
            f"Unexpected candidate models: expected {EXPECTED_CANDIDATES}, got {manifest.get('candidate_versions')}"
        )

    weights_frame = pd.read_csv(weights_path)
    versions = weights_frame["candidate_version"].tolist()
    if versions != EXPECTED_CANDIDATES:
        raise ValueError(f"Weight rows do not match expected candidates: {versions}")
    weights = dict(zip(versions, weights_frame["weight"].astype(float), strict=True))
    if any(weight < 0 for weight in weights.values()) or not np.isclose(sum(weights.values()), 1.0, atol=1e-9):
        raise ValueError(f"Invalid v29 ensemble weights: {weights}")

    models = {}
    for version in EXPECTED_CANDIDATES:
        model_path = model_dir / f"{version}.joblib"
        if not model_path.is_file():
            raise FileNotFoundError(f"Candidate model not found: {model_path}")
        models[version] = joblib.load(model_path)
    return InferenceArtifacts(manifest=manifest, models=models, weights=weights)


def latest_source_timestamp(engine, table_name: str, logical_meter: str) -> pd.Timestamp:
    source_meters = [source for source, _ in training.LOGICAL_METERS[logical_meter]]
    placeholders = ", ".join(f":source_{index}" for index in range(len(source_meters)))
    sql = f"""
SELECT MAX(window_ts)
FROM {table_name}
WHERE meter_urn IN ({placeholders})
  AND measurement IN ('P', 'U1', 'PF')
"""
    params = {f"source_{index}": source for index, source in enumerate(source_meters)}
    frame = training.read_sql_with_retry(engine, sql, params)
    value = frame.iloc[0, 0]
    if pd.isna(value):
        raise ValueError(f"No source data found for logical meter {logical_meter}")
    return pd.Timestamp(value).tz_convert("UTC")


def fetch_source_range(
    engine,
    table_name: str,
    meter_urn: str,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    expected_input_end: pd.Timestamp,
) -> pd.DataFrame:
    meter = training.read_sql_with_retry(
        engine,
        training.feature_sql(table_name, meter_urn),
        {"meter_urn": meter_urn, "start_ts": start_ts, "end_ts": end_ts},
    )
    if meter.empty:
        return meter
    meter["window_ts"] = pd.to_datetime(meter["window_ts"], utc=True)
    for column in training.RAW_FEATURE_COLUMNS:
        meter[column] = pd.to_numeric(meter[column], errors="coerce")
    meter["P_mean"] = meter["P_mean"].clip(lower=0)
    meter["P_max"] = meter["P_max"].clip(lower=0)
    meter.loc[meter["U1_mean"] <= 0, "U1_mean"] = np.nan
    meter.loc[meter["U1_mean"] > 1000, "U1_mean"] = np.nan
    meter.loc[meter["PF_mean"].abs() > 1.5, "PF_mean"] = np.nan
    meter = meter.rename(columns={"window_ts": "ts"}).sort_values("ts").reset_index(drop=True)
    return training.impute_raw_feature_gaps(
        meter,
        meter_urn,
        end_ts=expected_input_end,
        allow_trailing_single_bucket=True,
    )


def fetch_logical_meter_range(
    engine,
    table_name: str,
    logical_meter: str,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
) -> pd.DataFrame:
    frames = []
    expected_input_end = end_ts - pd.Timedelta(minutes=training.STEP_MINUTES)
    for source_meter, segment_id in training.LOGICAL_METERS[logical_meter]:
        frame = fetch_source_range(
            engine,
            table_name,
            source_meter,
            start_ts,
            end_ts,
            expected_input_end,
        )
        if frame.empty:
            continue
        frame["logical_meter_id"] = logical_meter
        frame["source_meter_urn"] = source_meter
        frame["segment_id"] = segment_id
        frames.append(frame)
    if not frames:
        requested = [source for source, _ in training.LOGICAL_METERS[logical_meter]]
        raise ValueError(f"No data found for requested meter sources: {requested}")

    combined = pd.concat(frames, ignore_index=True)
    enriched = training.add_time_features(combined.sort_values(["segment_id", "ts"]).reset_index(drop=True))
    return (
        training.add_lag_rolling_features(enriched)
        .sort_values(["segment_id", "ts"])
        .reset_index(drop=True)
    )


def build_inference_window(
    features: pd.DataFrame,
    logical_meter: str,
    requested_as_of: pd.Timestamp,
    input_hours: int = 24,
) -> InferenceWindow:
    window_size = input_hours * 60 // training.STEP_MINUTES
    required = training.FEATURE_COLUMNS + [training.TARGET_COLUMN]
    expected_step = pd.Timedelta(minutes=training.STEP_MINUTES)
    expected_input_end = requested_as_of - expected_step
    candidates: list[pd.DataFrame] = []

    selected = features[features["ts"] <= expected_input_end].copy()
    latest_rows = selected[selected["ts"] == expected_input_end]
    if latest_rows.empty:
        raise ValueError(f"Latest input bucket {expected_input_end} is missing")
    latest_usable = latest_rows[
        latest_rows[training.INPUT_OBSERVED_COLUMN]
        | latest_rows[training.FORWARD_FILLED_COLUMN]
    ]
    if latest_usable.empty:
        raise ValueError(
            f"Latest input bucket {expected_input_end} cannot be recovered from one prior bucket"
        )
    for _, segment in selected.groupby(["source_meter_urn", "segment_id"], sort=True):
        segment = segment.sort_values("ts").dropna(subset=required).reset_index(drop=True)
        if segment.empty:
            continue
        continuity_group = segment["ts"].diff().ne(expected_step).cumsum()
        for _, continuous in segment.groupby(continuity_group, sort=True):
            if len(continuous) >= window_size:
                candidates.append(continuous.tail(window_size).reset_index(drop=True))

    if not candidates:
        raise ValueError(
            f"No complete continuous {input_hours}h feature window is available for {logical_meter} "
            f"at or before {requested_as_of}"
        )
    exact_candidates = [
        candidate for candidate in candidates if candidate.iloc[-1]["ts"] == expected_input_end
    ]
    if not exact_candidates:
        latest_available = max(candidate.iloc[-1]["ts"] for candidate in candidates)
        raise ValueError(
            f"Expected completed input bucket {expected_input_end} for request {requested_as_of}, "
            f"but the latest complete window ends at {latest_available}"
        )
    window = max(exact_candidates, key=lambda frame: int(frame.iloc[-1]["segment_id"]))
    if not training.input_imputation_is_allowed(window):
        raise ValueError(
            "Input window exceeds the missing-data policy: allow one internal gap and "
            "at most four imputed rows, with no more than one trailing forward-filled row"
        )
    timestamps = window["ts"].reset_index(drop=True)
    if len(window) != window_size or not timestamps.diff().iloc[1:].eq(expected_step).all():
        raise ValueError("Selected inference window is not exactly 96 continuous 15-minute rows")
    matrix = window[training.FEATURE_COLUMNS].to_numpy(dtype=np.float32)
    if matrix.shape != (window_size, len(training.FEATURE_COLUMNS)) or not np.isfinite(matrix).all():
        raise ValueError(f"Invalid inference feature matrix: shape={matrix.shape}")
    interpolation_rows = int(window[training.INTERPOLATED_COLUMN].fillna(False).sum())
    forward_filled_rows = int(window[training.FORWARD_FILLED_COLUMN].fillna(False).sum())
    return InferenceWindow(
        x=matrix.reshape(1, -1),
        logical_meter=logical_meter,
        source_meter_urn=str(window.iloc[-1]["source_meter_urn"]),
        segment_id=int(window.iloc[-1]["segment_id"]),
        input_start_ts=pd.Timestamp(window.iloc[0]["ts"]),
        input_end_ts=pd.Timestamp(window.iloc[-1]["ts"]),
        last_import_p_max=float(window.iloc[-1][training.TARGET_COLUMN]),
        n_input_rows=len(window),
        imputed_rows=int(
            (
                window[training.INTERPOLATED_COLUMN].fillna(False)
                | window[training.FORWARD_FILLED_COLUMN].fillna(False)
            ).sum()
        ),
        interpolation_rows=interpolation_rows,
        forward_filled_rows=forward_filled_rows,
    )


def predict_ensemble(artifacts: InferenceArtifacts, window: InferenceWindow) -> tuple[np.ndarray, dict[str, list[float]]]:
    candidate_predictions = {}
    ensemble = np.zeros(EXPECTED_HORIZON_STEPS, dtype=np.float64)
    for version in EXPECTED_CANDIDATES:
        prediction = training.predict_model(artifacts.models[version], window.x).reshape(-1).astype(np.float64)
        if prediction.shape != (EXPECTED_HORIZON_STEPS,):
            raise ValueError(f"{version} returned unexpected prediction shape {prediction.shape}")
        candidate_predictions[version] = prediction.tolist()
        ensemble += artifacts.weights[version] * prediction
    return ensemble, candidate_predictions


def build_result(
    artifacts: InferenceArtifacts,
    window: InferenceWindow,
    raw_prediction: np.ndarray,
    candidate_predictions: dict[str, list[float]],
    requested_as_of: pd.Timestamp,
) -> dict[str, Any]:
    clipped = np.clip(raw_prediction, 0.0, None)
    predictions = []
    for index, (raw_value, clipped_value) in enumerate(zip(raw_prediction, clipped, strict=True), start=1):
        target_start_ts = requested_as_of + (index - 1) * pd.Timedelta(
            minutes=training.STEP_MINUTES
        )
        target_end_ts = requested_as_of + index * pd.Timedelta(minutes=training.STEP_MINUTES)
        predictions.append(
            {
                "horizon_minutes": index * training.STEP_MINUTES,
                "target_start_ts": target_start_ts.isoformat(),
                "target_end_ts": target_end_ts.isoformat(),
                "timestamp": target_end_ts.isoformat(),
                "predicted_import_p_max": float(clipped_value),
                "raw_model_prediction": float(raw_value),
            }
        )
    peak_index = int(np.argmax(clipped))
    return {
        "logical_meter": window.logical_meter,
        "requested_source_meters": [
            source for source, _ in training.LOGICAL_METERS[window.logical_meter]
        ],
        "source_meter_urn": window.source_meter_urn,
        "segment_id": window.segment_id,
        "requested_as_of": requested_as_of.isoformat(),
        "input_start_ts": window.input_start_ts.isoformat(),
        "input_end_ts": window.input_end_ts.isoformat(),
        "data_lag_minutes": float(
            (
                requested_as_of
                - pd.Timedelta(minutes=training.STEP_MINUTES)
                - window.input_end_ts
            )
            / pd.Timedelta(minutes=1)
        ),
        "input_rows": window.n_input_rows,
        "last_import_p_max": window.last_import_p_max,
        "data_quality": {
            "status": "degraded" if window.imputed_rows else "normal",
            "imputed_rows": window.imputed_rows,
            "linear_interpolation_rows": window.interpolation_rows,
            "forward_filled_rows": window.forward_filled_rows,
            "policy": (
                "internal_linear_up_to_60min_and_trailing_forward_fill_up_to_15min"
            ),
        },
        "model": {
            "method": "v29",
            "output_range": EXPECTED_OUTPUT_RANGE,
            "candidate_versions": EXPECTED_CANDIDATES,
            "weights": artifacts.weights,
            "feature_count_per_step": len(training.FEATURE_COLUMNS),
            "flattened_feature_count": int(window.x.shape[1]),
        },
        "predictions": predictions,
        "next_60min_peak": predictions[peak_index],
        "candidate_raw_predictions": candidate_predictions,
    }


def result_to_csv_frame(result: dict[str, Any], created_at: pd.Timestamp | None = None) -> pd.DataFrame:
    created_at = created_at or pd.Timestamp.now(tz="UTC")
    rows = []
    for prediction in result["predictions"]:
        rows.append(
            {
                "logical_meter": result["logical_meter"],
                "source_meter_urn": result["source_meter_urn"],
                "base_ts": result["requested_as_of"],
                "input_end_ts": result["input_end_ts"],
                "target_ts": prediction["target_end_ts"],
                "horizon_minutes": prediction["horizon_minutes"],
                "predicted_p_max": prediction["predicted_import_p_max"],
                "created_at": created_at.isoformat(),
            }
        )
    return pd.DataFrame(rows)


def write_csv_result(result: dict[str, Any], output_path: Path, append: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    should_append = append and output_path.exists()
    result_to_csv_frame(result).to_csv(
        output_path,
        mode="a" if should_append else "w",
        header=not should_append,
        index=False,
    )


def verification_request(args: argparse.Namespace) -> tuple[pd.Timestamp | None, dict[str, Any] | None]:
    if args.verify_predictions is None:
        return None, None
    frame = pd.read_csv(args.verify_predictions)
    if frame.empty:
        raise ValueError(f"Verification prediction file is empty: {args.verify_predictions}")
    row = frame.iloc[args.verify_row]
    timestamp = normalize_timestamp(row["input_end_ts"]) + pd.Timedelta(
        minutes=training.STEP_MINUTES
    )
    expected = np.array([row[f"pred_t_plus_{index}"] for index in range(1, 5)], dtype=np.float64)
    return timestamp, {"row_index": int(row.name), "expected": expected}


def verify_reproduction(
    result: dict[str, Any],
    verification: dict[str, Any] | None,
    tolerance: float,
) -> dict[str, Any] | None:
    if verification is None:
        return None
    actual = np.array([item["raw_model_prediction"] for item in result["predictions"]], dtype=np.float64)
    expected = verification["expected"]
    absolute_error = np.abs(actual - expected)
    return {
        "row_index": verification["row_index"],
        "expected_raw_predictions": expected.tolist(),
        "reproduced_raw_predictions": actual.tolist(),
        "absolute_error": absolute_error.tolist(),
        "max_absolute_error": float(absolute_error.max()),
        "tolerance": tolerance,
        "passed": bool(np.all(absolute_error <= tolerance)),
    }


def main() -> None:
    args = parse_args()
    if args.lookback_days < 4:
        raise ValueError("lookback-days must be at least 4 to support the 48-hour lag and 24-hour input window")
    table_name = training.validate_table_name(args.table)
    verification_as_of, verification = verification_request(args)
    requested_as_of = verification_as_of or (normalize_timestamp(args.as_of) if args.as_of else None)
    if requested_as_of is not None and (
        requested_as_of.minute % training.STEP_MINUTES != 0
        or requested_as_of.second != 0
        or requested_as_of.microsecond != 0
    ):
        raise ValueError("--as-of must be aligned to an exact 15-minute boundary")

    artifacts = load_artifacts(args.model_root, args.meter)
    engine = training.build_engine()
    if requested_as_of is None:
        requested_as_of = latest_source_timestamp(engine, table_name, args.meter) + pd.Timedelta(
            minutes=training.STEP_MINUTES
        )
    end_ts = requested_as_of
    start_ts = requested_as_of - pd.Timedelta(days=args.lookback_days)
    features = fetch_logical_meter_range(engine, table_name, args.meter, start_ts, end_ts)
    window = build_inference_window(features, args.meter, requested_as_of)
    raw_prediction, candidate_predictions = predict_ensemble(artifacts, window)
    result = build_result(artifacts, window, raw_prediction, candidate_predictions, requested_as_of)
    reproduction = verify_reproduction(result, verification, args.tolerance)
    if reproduction is not None:
        result["reproduction_check"] = reproduction

    payload = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    if args.csv_output:
        write_csv_result(result, args.csv_output, args.append_csv)
    print(payload)
    if reproduction is not None and not reproduction["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
