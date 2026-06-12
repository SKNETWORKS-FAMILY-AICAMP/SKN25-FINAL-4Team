# -*- coding: utf-8 -*-
"""Compare shared experiment metrics under reports/experiments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT_DIR / "reports" / "experiments"


def _pick_headline(summary: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "accuracy",
        "macro_f1",
        "total",
        "avg_elapsed_ms",
        "avg_elapsed_s",
        "model",
        "provider",
        "judge",
    )
    return {k: summary[k] for k in keys if k in summary}


def collect() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metrics_path in sorted(REPORTS_DIR.glob("test*/run_*/metrics.json")):
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - report broken artifact without aborting all
            rows.append({"path": str(metrics_path), "error": str(exc)})
            continue
        summary = metrics.get("summary", {})
        rows.append(
            {
                "test_id": metrics.get("test_id", metrics_path.parents[1].name),
                "run_id": metrics.get("run_id", metrics_path.parent.name),
                "metric_family": metrics.get("metric_family"),
                "dataset_count": metrics.get("dataset", {}).get("count", metrics.get("dataset_count")),
                "evaluated_at": metrics.get("evaluated_at"),
                "summary": _pick_headline(summary),
                "gates": metrics.get("gates", {}),
                "path": str(metrics_path.relative_to(ROOT_DIR)),
            }
        )
    return rows


def write_markdown(rows: list[dict[str, Any]], out_md: Path) -> None:
    lines = [
        "# Experiment Metrics Comparison",
        "",
        "| test_id | run_id | family | count | headline | gates | path |",
        "|---|---|---|---:|---|---|---|",
    ]
    for row in rows:
        headline = ", ".join(f"{k}={v}" for k, v in row.get("summary", {}).items())
        gates = ", ".join(f"{k}={v}" for k, v in row.get("gates", {}).items())
        lines.append(
            f"| `{row.get('test_id')}` | `{row.get('run_id')}` | {row.get('metric_family')} | "
            f"{row.get('dataset_count')} | {headline} | {gates} | `{row.get('path')}` |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    rows = collect()
    out_json = REPORTS_DIR / "metrics_comparison.json"
    out_md = REPORTS_DIR / "metrics_comparison.md"
    out_json.write_text(json.dumps({"runs": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(rows, out_md)
    print(json.dumps({"run_count": len(rows), "out_json": str(out_json), "out_md": str(out_md)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
