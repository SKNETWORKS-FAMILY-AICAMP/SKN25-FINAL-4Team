from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd

from . import inference, training


DEFAULT_OUTPUT_DIR = inference.PROJECT_ROOT / "outputs" / "import_pmax"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one import P-max inference batch for all logical meters."
    )
    parser.add_argument("--table", default=training.DEFAULT_TABLE)
    parser.add_argument("--model-root", type=Path, default=inference.DEFAULT_MODEL_ROOT)
    parser.add_argument(
        "--as-of",
        help=(
            "Common forecast request time on a 15-minute boundary. If omitted, "
            "the earliest latest-complete timestamp across all meters is used."
        ),
    )
    parser.add_argument("--lookback-days", type=int, default=inference.DEFAULT_LOOKBACK_DAYS)
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Aggregate JSON output path. Defaults to outputs/import_pmax/{as_of}.json.",
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        help="Combined 16-row CSV output path. Defaults to outputs/import_pmax/{as_of}.csv.",
    )
    return parser.parse_args()


def default_output_paths(requested_as_of: pd.Timestamp) -> tuple[Path, Path]:
    stamp = requested_as_of.strftime("%Y%m%dT%H%M%SZ")
    return (
        DEFAULT_OUTPUT_DIR / f"import_pmax_{stamp}.json",
        DEFAULT_OUTPUT_DIR / f"import_pmax_{stamp}.csv",
    )


def resolve_requested_as_of(
    engine,
    table_name: str,
    requested_as_of: str | None,
) -> pd.Timestamp:
    if requested_as_of is not None:
        timestamp = inference.normalize_timestamp(requested_as_of)
    else:
        latest = [
            inference.latest_source_timestamp(engine, table_name, meter)
            for meter in training.LOGICAL_METERS
        ]
        timestamp = min(latest) + pd.Timedelta(minutes=training.STEP_MINUTES)

    if (
        timestamp.minute % training.STEP_MINUTES != 0
        or timestamp.second != 0
        or timestamp.microsecond != 0
    ):
        raise ValueError("--as-of must be aligned to an exact 15-minute boundary")
    return timestamp


def run_meter(
    engine,
    table_name: str,
    model_root: Path,
    logical_meter: str,
    requested_as_of: pd.Timestamp,
    lookback_days: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    artifacts = inference.load_artifacts(model_root, logical_meter)
    end_ts = requested_as_of
    start_ts = requested_as_of - pd.Timedelta(days=lookback_days)
    features = inference.fetch_logical_meter_range(
        engine,
        table_name,
        logical_meter,
        start_ts,
        end_ts,
    )
    window = inference.build_inference_window(
        features,
        logical_meter,
        requested_as_of,
    )
    raw_prediction, candidate_predictions = inference.predict_ensemble(
        artifacts,
        window,
    )
    result = inference.build_result(
        artifacts,
        window,
        raw_prediction,
        candidate_predictions,
        requested_as_of,
    )
    result["elapsed_seconds"] = time.perf_counter() - started
    return result


def run_batch(
    *,
    table_name: str,
    model_root: Path,
    requested_as_of: str | None,
    lookback_days: int,
) -> dict[str, Any]:
    if lookback_days < 4:
        raise ValueError("lookback-days must be at least 4")

    table_name = training.validate_table_name(table_name)
    engine = training.build_engine()
    common_as_of = resolve_requested_as_of(engine, table_name, requested_as_of)
    started = time.perf_counter()
    results = [
        run_meter(
            engine,
            table_name,
            model_root,
            logical_meter,
            common_as_of,
            lookback_days,
        )
        for logical_meter in training.LOGICAL_METERS
    ]
    return {
        "status": "success",
        "requested_as_of": common_as_of.isoformat(),
        "logical_meter_count": len(results),
        "prediction_row_count": sum(len(result["predictions"]) for result in results),
        "elapsed_seconds": time.perf_counter() - started,
        "results": results,
    }


def batch_to_csv_frame(batch: dict[str, Any]) -> pd.DataFrame:
    created_at = pd.Timestamp.now(tz="UTC")
    frames = [
        inference.result_to_csv_frame(result, created_at=created_at)
        for result in batch["results"]
    ]
    return pd.concat(frames, ignore_index=True)


def write_batch_outputs(
    batch: dict[str, Any],
    json_output: Path,
    csv_output: Path,
) -> None:
    json_output.parent.mkdir(parents=True, exist_ok=True)
    csv_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(batch, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    batch_to_csv_frame(batch).to_csv(csv_output, index=False)


def main() -> None:
    args = parse_args()
    batch = run_batch(
        table_name=args.table,
        model_root=args.model_root,
        requested_as_of=args.as_of,
        lookback_days=args.lookback_days,
    )
    requested_as_of = inference.normalize_timestamp(batch["requested_as_of"])
    default_json, default_csv = default_output_paths(requested_as_of)
    json_output = args.json_output or default_json
    csv_output = args.csv_output or default_csv
    write_batch_outputs(batch, json_output, csv_output)
    batch["json_output"] = str(json_output)
    batch["csv_output"] = str(csv_output)
    print(json.dumps(batch, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
