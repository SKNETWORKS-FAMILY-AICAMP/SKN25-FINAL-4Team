#!/usr/bin/env python3
"""Check deployed model-serving schema inventory against the repo contract.

Input is the JSON emitted by model_serving_schema_inventory.py --execute --json.
The script is pure/read-only: it does not connect to PostgreSQL and does not
modify files or databases.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "live.measurement_event": (
        "event_id",
        "business_idempotency_key",
        "source_event_id",
        "meter_urn",
        "measurement",
        "event_ts",
        "value_text",
        "value_numeric",
        "unit",
        "source_layer",
        "source_ref",
        "ingested_at",
        "received_at",
        "raw_payload_hash",
        "kafka_topic",
        "kafka_partition",
        "kafka_offset",
        "kafka_key",
        "consumer_group",
        "consumed_at",
        "schema_version",
        "policy_lookup_status",
    ),
    "mart.peak_feature_15min": (
        "window_ts",
        "meter_urn",
        "measurement",
        "mean_value",
        "max_value",
        "min_value",
        "p95_value",
        "p99_value",
        "std_value",
        "last_value",
        "peak_ts",
        "peak_value",
        "observed_points",
        "expected_points",
        "coverage_ratio",
        "source_file",
        "source_layer",
        "source_mode",
        "provenance",
        "run_id",
        "created_at",
    ),
    "mart.pmax_forecast_15min": (
        "logical_meter",
        "source_meter_urn",
        "base_ts",
        "input_end_ts",
        "target_ts",
        "actual_window_ts",
        "horizon_minutes",
        "predicted_p_max",
        "created_at",
    ),
    "ops.pmax_forecast_inference_log": (
        "run_id",
        "base_ts",
        "status",
        "quality_status",
        "logical_meter_count",
        "forecast_row_count",
        "replacement_row_count",
        "internal_missing_segment_count",
        "latest_missing_policy",
        "error_reason",
        "details",
        "started_at",
        "completed_at",
    ),
    "qa.pmax_forecast_evaluation": (
        "evaluation_id",
        "logical_meter",
        "source_meter_urn",
        "base_ts",
        "target_ts",
        "actual_window_ts",
        "horizon_minutes",
        "predicted_p_max",
        "actual_p_max",
        "absolute_error",
        "squared_error",
        "evaluated_at",
    ),
}

OPTIONAL_TABLES = frozenset({"mart.peak_feature_15min"})
KNOWN_ABSENT_TABLES = frozenset(
    {
        "mart.peak_training_frame_15min",
        "mart.anomaly_feature_1h",
        "mart.anomaly_warning_1h",
        "ops.anomaly_warning_inference_log",
        "qa.anomaly_warning_evaluation",
        "qa.model_serving_evidence_packet",
    }
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory_json", nargs="?", help="path to inventory JSON; stdin when omitted")
    args = parser.parse_args(argv)

    payload = json.loads(open(args.inventory_json, encoding="utf-8").read() if args.inventory_json else sys.stdin.read())
    result = check_inventory(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def check_inventory(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary", payload)
    tables = summary.get("tables", {})
    errors: list[str] = []
    warnings: list[str] = []
    for table, expected_columns in EXPECTED_COLUMNS.items():
        meta = tables.get(table)
        if not meta or not meta.get("exists"):
            errors.append(f"missing_table:{table}")
            continue
        observed = tuple(meta.get("columns", ()))
        missing_columns = tuple(column for column in expected_columns if column not in observed)
        if missing_columns:
            errors.append(f"missing_columns:{table}:{','.join(missing_columns)}")
    for table in OPTIONAL_TABLES:
        meta = tables.get(table)
        if not meta or not meta.get("exists"):
            warnings.append(f"optional_table_missing:{table}")
    for table in KNOWN_ABSENT_TABLES:
        meta = tables.get(table)
        if meta and meta.get("exists"):
            warnings.append(f"known_absent_table_present:{table}")
    if any(table.startswith("canonical.") for table in tables):
        errors.append("canonical_table_in_model_serving_inventory")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "checked_tables": tuple(EXPECTED_COLUMNS)}


if __name__ == "__main__":
    raise SystemExit(main())
