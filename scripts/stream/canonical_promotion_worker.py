#!/usr/bin/env python3
"""Run one bounded canonical promotion worker pass.

Default mode prints the SQL plan only. Runtime writes require both
``--runtime`` and ``CMS_ENABLE_CANONICAL_PROMOTION=1``.
"""

from __future__ import annotations

import argparse
import json
import os

from cms.data.canonical_promotion_runner import execute_canonical_promotion_command, make_canonical_promotion_command


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a bounded canonical promotion pass")
    parser.add_argument("--promotion-id", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--source-table", action="append", default=None)
    parser.add_argument("--min-coverage-ratio", type=float, default=0.0)
    parser.add_argument("--runtime", action="store_true", help="execute DB writes; also requires CMS_ENABLE_CANONICAL_PROMOTION=1")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    command = make_canonical_promotion_command(
        promotion_id=args.promotion_id,
        approval_id=args.approval_id,
        batch_size=args.batch_size,
        source_tables=tuple(args.source_table) if args.source_table else ("live.measurement_15min", "live.measurement_1h"),
        min_coverage_ratio=args.min_coverage_ratio,
    )
    if not args.runtime:
        payload = {
            "mode": "dry_run",
            "write_attempted": False,
            "write_gate_env": command.write_gate_env,
            "source_tables": list(command.source_tables),
            "target_tables": list(command.target_tables),
            "promotion_id": command.promotion_id,
            "approval_id": command.approval_id,
            "batch_size": command.batch_size,
            "sql": command.sql,
            "params": command.params,
        }
        print(json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True), flush=True)
        return 0

    result = execute_canonical_promotion_command(command, allow_write=True, env=os.environ)
    print(json.dumps({**result.__dict__, "promoted_count": result.promoted_count}, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
