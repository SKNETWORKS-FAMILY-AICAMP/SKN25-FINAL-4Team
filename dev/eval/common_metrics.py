# -*- coding: utf-8 -*-
"""Common experiment metrics helpers for app/ems-agent eval runners.

The app/ems-agent branch keeps its existing evaluation implementations under
``dev/eval``.  This module standardizes only the output envelope so every runner
can write ``reports/experiments/<test_id>/run_*/metrics.json`` without an extra
adapter step.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "experiment-metrics.v1"

PHASE_LATENCY_KEYS = (
    "load",
    "preprocess",
    "route",
    "retrieval",
    "rerank",
    "extraction",
    "answer",
    "qa",
    "report",
    "total",
)

COMPONENT_LATENCY_KEYS = (
    "api",
    "llm",
    "db",
    "kafka",
    "workflow",
    "file_io",
    "total",
)

ROOT_DIR = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT_DIR / "reports" / "experiments"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_run_id(suffix: str | None = None) -> str:
    stem = "run_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    if suffix:
        safe = suffix.replace(":", "_").replace("/", "_").replace(" ", "_")
        stem = f"{stem}_{safe}"
    return stem


def make_run_dir(test_id: str, run_id: str | None = None, *, suffix: str | None = None) -> Path:
    """Create and return a unique reports/experiments run directory."""
    base = REPORTS_DIR / test_id
    stem = run_id or timestamp_run_id(suffix)
    candidate = base / stem
    idx = 1
    while candidate.exists():
        candidate = base / f"{stem}_{idx:02d}"
        idx += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def infer_run_id(out_json: Path) -> str:
    parent = out_json.parent
    return parent.name if parent.name.startswith("run_") else "direct"


def default_output_paths(test_id: str, run_id: str | None = None, *, suffix: str | None = None) -> tuple[Path, Path]:
    run_dir = make_run_dir(test_id, run_id, suffix=suffix)
    return run_dir / "metrics.json", run_dir / "report.md"


def _stable_float_dict(keys: tuple[str, ...], values: dict[str, Any] | None = None) -> dict[str, float]:
    source = values or {}
    out: dict[str, float] = {}
    for key in keys:
        try:
            out[key] = float(source.get(key, 0.0))
        except (TypeError, ValueError):
            out[key] = 0.0
    return out


def phase_latency(**values: Any) -> dict[str, float]:
    return _stable_float_dict(PHASE_LATENCY_KEYS, values)


def component_latency(**values: Any) -> dict[str, float]:
    return _stable_float_dict(COMPONENT_LATENCY_KEYS, values)


def build_metrics_envelope(
    *,
    test_id: str,
    run_id: str,
    metric_family: str,
    dataset_path: Path | str,
    dataset_count: int,
    summary: dict[str, Any],
    phase_latency_ms: dict[str, Any] | None = None,
    component_latency_ms: dict[str, Any] | None = None,
    payload_metrics: dict[str, Any] | None = None,
    gates: dict[str, Any] | None = None,
    errors: list[Any] | None = None,
    details: dict[str, Any] | None = None,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "test_id": test_id,
        "run_id": run_id,
        "metric_family": metric_family,
        "evaluated_at": evaluated_at or utc_now_iso(),
        "dataset": {"path": str(dataset_path), "count": int(dataset_count)},
        "dataset_count": int(dataset_count),
        "phase_latency_ms": phase_latency(**(phase_latency_ms or {})),
        "component_latency_ms": component_latency(**(component_latency_ms or {})),
        "payload_metrics": payload_metrics or {},
        "summary": summary,
        "gates": gates or {},
        "errors": errors or [],
        "details": details or {},
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
