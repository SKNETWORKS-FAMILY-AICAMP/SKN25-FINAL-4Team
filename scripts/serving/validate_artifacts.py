#!/usr/bin/env python3
"""Validate local model-serving artifact layouts without DB writes.

This runner validates extracted P-Max and anomaly release directories. P-Max
candidate model binaries are not deserialized by default; a dummy loader verifies
that every expected file path is present and referenced. Anomaly inventory checks
all required v84 meter files and metadata.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cms.modeling.anomaly_artifact_loader import AnomalyArtifactInventoryLoader
from cms.modeling.pmax_artifact_loader import PmaxReleaseArtifactLoader


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmax-root", type=Path, help="extracted import_pmax_production_release_20260608 root")
    parser.add_argument("--anomaly-root", type=Path, help="extracted test6_residual_v84_3h_share_20260609 root")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    result: dict[str, Any] = {"ok": True, "pmax": None, "anomaly": None, "errors": []}
    if args.pmax_root is not None:
        result["pmax"] = _validate_pmax(args.pmax_root, result["errors"])
    if args.anomaly_root is not None:
        result["anomaly"] = _validate_anomaly(args.anomaly_root, result["errors"])
    if args.pmax_root is None and args.anomaly_root is None:
        result["ok"] = False
        result["errors"].append("at least one of --pmax-root or --anomaly-root is required")
    result["ok"] = not result["errors"]
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2) if args.json else _format_result(result))
    return 0 if result["ok"] else 1


def _validate_pmax(root: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        loaded = PmaxReleaseArtifactLoader(root, model_loader=lambda path: {"path": path.as_posix()}).load()
    except Exception as exc:  # noqa: BLE001 - CLI maps domain errors to JSON report.
        errors.append(f"pmax:{exc}")
        return None
    return {
        "release_root": root.as_posix(),
        "logical_meters": tuple(sorted(loaded.meters)),
        "meter_count": len(loaded.meters),
        "candidate_versions": {meter: tuple(ensemble.models) for meter, ensemble in loaded.meters.items()},
    }


def _validate_anomaly(root: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        inventory = AnomalyArtifactInventoryLoader(root).load()
    except Exception as exc:  # noqa: BLE001 - CLI maps domain errors to JSON report.
        errors.append(f"anomaly:{exc}")
        return None
    return {
        "release_root": root.as_posix(),
        "meter_count": inventory.meter_count,
        "meters_sample": tuple(sorted(inventory.meters)[:5]),
    }


def _format_result(result: dict[str, Any]) -> str:
    lines = [f"ok={result['ok']}"]
    if result.get("pmax"):
        lines.append(f"pmax_meter_count={result['pmax']['meter_count']}")
    if result.get("anomaly"):
        lines.append(f"anomaly_meter_count={result['anomaly']['meter_count']}")
    for error in result["errors"]:
        lines.append(f"error={error}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
