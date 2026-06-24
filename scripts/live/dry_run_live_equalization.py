#!/usr/bin/env python
"""Dry-run TC0 inventory and eq_1min/eq_5min count planner.

This CLI is local/read-only by design: it scans file names under --data-root and
prints JSON to stdout. It writes no artifacts unless a future option explicitly
adds that behavior, and it never opens DB clients.

Run from repository root:

    PYTHONPATH=src python scripts/live/dry_run_live_equalization.py --window-minutes 60
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from cms.data.live_equalization_plan import build_equalization_count_plan, build_tc0_inventory


def main() -> None:
    args = parse_args()
    inventory = build_tc0_inventory(Path(args.data_root))
    count_plan = build_equalization_count_plan(
        measurement_series_count=args.measurement_series_count or inventory.measurement_series_count,
        window_minutes=args.window_minutes,
    )
    payload = {
        "side_effects_executed": False,
        "writes_allowed": False,
        "local_artifacts_written": False,
        "tc0_inventory": asdict(inventory),
        "count_plan": asdict(count_plan),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only CMS TC0 inventory and equalization count planner.")
    parser.add_argument("--data-root", default="/mnt/hgfs/Windows/EMS/data", help="Root containing local EMS/CMS *.csv.gz files.")
    parser.add_argument("--window-minutes", type=int, default=60, help="Replay/planning window in minutes; must be divisible by 60.")
    parser.add_argument(
        "--measurement-series-count",
        type=int,
        help="Optional override for count planning. Defaults to discovered TC0 harmonized series count.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
