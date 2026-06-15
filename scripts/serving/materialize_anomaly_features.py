#!/usr/bin/env python3
"""Materialize strict anomaly-serving 1h features.

Default mode prints a dry-run SQL plan. Runtime writes require both ``--runtime``
and ``CMS_ENABLE_ANOMALY_FEATURE_MATERIALIZATION=1``.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

from cms.data.anomaly_feature_materializer import execute_anomaly_feature_materialization_command, make_anomaly_feature_materialization_command


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize mart.anomaly_feature_1h from observed 1h facts")
    parser.add_argument("--start-ts", required=True, type=_parse_ts)
    parser.add_argument("--end-ts", required=True, type=_parse_ts)
    parser.add_argument("--source-table", default="live.measurement_1h")
    parser.add_argument("--source-mode", default="live_observed", choices=("live_observed", "hybrid_warm_start"))
    parser.add_argument("--meter-urn", action="append", default=[])
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--runtime", action="store_true", help="execute DB writes; also requires CMS_ENABLE_ANOMALY_FEATURE_MATERIALIZATION=1")
    args = parser.parse_args()

    command = make_anomaly_feature_materialization_command(
        start_ts=args.start_ts,
        end_ts=args.end_ts,
        source_table=args.source_table,
        source_mode=args.source_mode,
        meter_urns=tuple(args.meter_urn),
        batch_size=args.batch_size,
    )
    if not args.runtime:
        payload = {
            "mode": "dry_run",
            "write_attempted": False,
            "write_gate_env": command.write_gate_env,
            "source_table": command.source_table,
            "target_table": command.target_table,
            "source_mode": command.source_mode,
            "batch_size": command.batch_size,
            "sql": command.sql,
            "params": command.params,
        }
        print(json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True), flush=True)
        return 0

    result = execute_anomaly_feature_materialization_command(command, allow_write=True, env=os.environ)
    print(json.dumps(result.__dict__, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
