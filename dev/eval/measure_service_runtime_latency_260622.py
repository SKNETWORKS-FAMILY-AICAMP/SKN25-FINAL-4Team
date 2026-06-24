#!/usr/bin/env python3
"""Measure service-runtime latency edges for the LangGraph runtime diagram.

This runner is intentionally separate from the old 300/60 evaluation harness.
It measures:
- Router LLM latency with Ollama qwen3.5:9b over the 300-row dataset that contains QA60.
- Representative DB/DW/DM/ontology lookup latency for diagram edges.

Outputs JSON with edge_avg_ms values that can be pasted into the Mermaid arrows.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as stats
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "dev/eval/data/router_two_stage_eval_300_with_qa60_260622.json"
DEFAULT_OUT = ROOT / "reports/experiments/service_runtime_latency/service_runtime_latency_260622_qwen35_9b.json"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def ms_since(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def summarize(samples: list[float]) -> dict:
    if not samples:
        return {"count": 0, "avg_ms": None, "p50_ms": None, "p95_ms": None, "max_ms": None}
    s = sorted(samples)
    p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]
    return {
        "count": len(s),
        "avg_ms": round(sum(s) / len(s), 2),
        "p50_ms": round(stats.median(s), 2),
        "p95_ms": round(p95, 2),
        "max_ms": round(max(s), 2),
    }


def call_ollama(url: str, model: str, message: str, timeout: int = 120) -> float:
    endpoint = url.rstrip("/").replace("/v1", "") + "/api/chat"
    system = (
        "Return exactly one JSON object with keys route1, route2, final_action. "
        "route1 in query, action_request, approval_required, off_topic, multi_intent. "
        "route2 in anomaly, cms, report, forecast, rag or null. No prose."
    )
    payload = {
        "model": model,
        "stream": False,
        # Ollama 0.30+ expects boolean think; string "off" returns HTTP 400.
        "think": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": message}],
        "options": {"temperature": 0, "top_p": 0, "num_predict": 96},
    }
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()
    return ms_since(t0)


DB_EDGE_SQL = {
    "DOMAIN->ONT": "select meter_urn, meter_domain, equipment_group_label from ontology.meter_context limit 50",
    "DOMAIN->DOCKB": "select id, source, length(content) as content_len from ops.energy_doc order by created_at desc nulls last limit 20",
    "DOMAIN->DWTS": "select meter_urn, measurement, value from reference.corrected_resampled_1h limit 100",
    "CMS->OPSDB": "select id, equipment_id, status, created_at from ops.work_order order by created_at desc limit 50",
    "CMS->DWTS": "select meter_urn, measurement, value from reference.corrected_resampled_1h limit 100",
    "ANOMALY->ANOMART": "select * from mart.anomaly_warning_1h limit 100",
    "ANOMALY->DWTS": "select meter_urn, measurement, value from reference.corrected_resampled_1h limit 100",
    "FORECAST->FOREMART": "select * from mart.peak_feature_15min limit 100",
    "FORECAST->DWTS": "select meter_urn, measurement, value from reference.corrected_resampled_15min limit 100",
    "REPORT->REPORTDM": "select * from ops.monthly_report order by generated_at desc nulls last limit 20",
    "REPORT->DWTS": "select meter_urn, measurement, value from reference.corrected_resampled_1h limit 100",
}


def measure_db_edges(repeats: int) -> dict:
    """Measure DB edge latency via psql so the runner has no psycopg dependency."""
    import subprocess

    out = {}
    env = os.environ.copy()
    env["PGPASSWORD"] = os.environ["DB_PASSWORD"]
    base_cmd = [
        "psql",
        "-h", os.environ["DB_HOST"],
        "-p", os.environ.get("DB_PORT", "5432"),
        "-U", os.environ["DB_USER"],
        "-d", os.environ["DB_NAME"],
        "-v", "ON_ERROR_STOP=1",
        "-qAt",
    ]
    for edge, sql in DB_EDGE_SQL.items():
        samples = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            subprocess.run(base_cmd + ["-c", sql], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, text=True)
            samples.append(ms_since(t0))
        out[edge] = {"sql": sql, **summarize(samples)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--model", default=os.environ.get("TEST_LLM_MODEL", "qwen3.5:9b"))
    ap.add_argument("--ollama-url", default=os.environ.get("OLLAMA_BASE_URL") or os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434")
    ap.add_argument("--router-limit", type=int, default=300)
    ap.add_argument("--db-repeats", type=int, default=5)
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--skip-db", action="store_true")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    load_dotenv(ROOT.parent / ".env")
    load_dotenv(ROOT / ".env")

    rows = json.loads(Path(args.dataset).read_text(encoding="utf-8"))["rows"]
    dataset_path = Path(args.dataset)
    try:
        dataset_label = str(dataset_path.resolve().relative_to(ROOT))
    except Exception:
        dataset_label = str(dataset_path)
    payload = {
        "run_id": "service_runtime_latency_260622_qwen35_9b",
        "dataset": dataset_label,
        "model": args.model,
        "ollama_url": args.ollama_url,
        "router_row_count_requested": min(args.router_limit, len(rows)),
        "edge_timings": {},
        "notes": "Use edge_timings.*.avg_ms to replace 'avg ms: measure' labels in Mermaid arrows.",
    }

    if not args.skip_llm:
        router_samples = []
        for r in rows[: args.router_limit]:
            router_samples.append(call_ollama(args.ollama_url, args.model, r["message"]))
        payload["edge_timings"]["API->ST1_ST2_router_llm"] = summarize(router_samples)

    if not args.skip_db:
        payload["edge_timings"].update(measure_db_edges(args.db_repeats))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "edge_count": len(payload["edge_timings"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
