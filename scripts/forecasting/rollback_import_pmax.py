from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from forecasting.import_pmax import operations, promotion


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Roll back Import P-Max runtime artifacts from an archive."
    )
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument(
        "--deployed-root",
        type=Path,
        default=operations.DEPLOYED_ROOT,
    )
    parser.add_argument("--approval-note")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = promotion.rollback_deployment(
        args.archive_root.resolve(),
        args.deployed_root.resolve(),
        approval_note=args.approval_note,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
