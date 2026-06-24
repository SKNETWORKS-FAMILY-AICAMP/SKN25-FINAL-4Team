#!/usr/bin/env python3
"""Full service E2E benchmark through FastAPI /chat and optional /chat/stream.

This is intentionally different from component-level router/QA/DB edge tests. It sends
real HTTP requests through the backend FastAPI app, which invokes the LangGraph
orchestrator and service agents. It measures the user-visible workflow latency that
matches the HTML Flow Chart path:

User question -> FastAPI /chat or /chat/stream -> LangGraph orchestration ->
Stage/router/agent/data-source steps -> Backend API output -> stream/final response.

It does not automate a browser frontend render; instead it measures backend API/SSE
latency, which is the E2E service path available in the RunPod headless environment.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request, error

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "dev/eval/data/router_two_stage_eval_300_with_qa60_260622.json"
DEFAULT_OUT = ROOT / "reports/experiments/service_e2e_flow_latency/e2e_service_flow_latency_260622_qwen35_9b.json"


def ms_since(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round((len(s) - 1) * q))))
    return round(s[idx], 2)


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "avg_ms": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0, "max_ms": 0}
    return {
        "count": len(values),
        "avg_ms": round(statistics.mean(values), 2),
        "p50_ms": pct(values, 0.50),
        "p95_ms": pct(values, 0.95),
        "p99_ms": pct(values, 0.99),
        "max_ms": round(max(values), 2),
    }


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 180) -> tuple[int, dict[str, Any], float]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = request.Request(url, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            elapsed = ms_since(t0)
            return resp.status, json.loads(raw) if raw else {}, elapsed
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        elapsed = ms_since(t0)
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw[:1000]}
        return e.code, body, elapsed


def call_chat(base_url: str, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    session_id = f"e2e-{uuid.uuid4()}"
    payload = {
        "question": row.get("message", ""),
        "history": [],
        "session_id": session_id,
        "is_first": True,
        "context": {
            "eval_run": "service_e2e_flow_latency_260622",
            "row_id": row.get("id"),
            "expected_route1": row.get("expected_route1"),
            "expected_route2": row.get("expected_route2"),
            "expected_final_action": row.get("expected_final_action"),
        },
    }
    status, body, total_ms = http_json("POST", base_url.rstrip("/") + "/chat", payload, timeout=timeout)
    ok = 200 <= status < 300 and isinstance(body, dict) and bool(body.get("answer") or body.get("intent"))
    return {
        "id": row.get("id"),
        "message": row.get("message"),
        "expected_route1": row.get("expected_route1"),
        "expected_route2": row.get("expected_route2"),
        "expected_final_action": row.get("expected_final_action"),
        "qa_subset": bool(row.get("qa_subset")),
        "endpoint": "/chat",
        "status_code": status,
        "ok": ok,
        "total_ms": total_ms,
        "intent": body.get("intent") if isinstance(body, dict) else None,
        "answer_chars": len(str(body.get("answer") or "")) if isinstance(body, dict) else 0,
        "session_id": body.get("session_id") if isinstance(body, dict) else None,
        "error": None if ok else body,
    }


def call_stream(base_url: str, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    session_id = f"e2e-stream-{uuid.uuid4()}"
    payload = {
        "question": row.get("message", ""),
        "history": [],
        "session_id": session_id,
        "is_first": True,
        "context": {"eval_run": "service_e2e_flow_latency_260622", "row_id": row.get("id")},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(base_url.rstrip("/") + "/chat/stream", data=data, headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    first_event_ms = None
    first_token_ms = None
    done_ms = None
    events: list[dict[str, Any]] = []
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            buf = ""
            while True:
                chunk = resp.read(1).decode("utf-8", errors="replace")
                if not chunk:
                    break
                buf += chunk
                if "\n\n" not in buf:
                    continue
                parts = buf.split("\n\n")
                buf = parts.pop()
                for part in parts:
                    if not part.startswith("data:"):
                        continue
                    if first_event_ms is None:
                        first_event_ms = ms_since(t0)
                    raw = part[5:].strip()
                    try:
                        ev = json.loads(raw)
                    except Exception:
                        ev = {"type": "raw", "content": raw[:300]}
                    events.append(ev)
                    if ev.get("type") == "token" and first_token_ms is None:
                        first_token_ms = ms_since(t0)
                    if ev.get("type") == "done":
                        done_ms = ms_since(t0)
                        return {
                            "id": row.get("id"),
                            "endpoint": "/chat/stream",
                            "status_code": status,
                            "ok": True,
                            "first_event_ms": first_event_ms,
                            "first_token_ms": first_token_ms,
                            "done_ms": done_ms,
                            "event_count": len(events),
                            "event_types": [e.get("type") for e in events[:20]],
                            "error": None,
                        }
            return {"id": row.get("id"), "endpoint": "/chat/stream", "status_code": status, "ok": False, "first_event_ms": first_event_ms, "first_token_ms": first_token_ms, "done_ms": done_ms or ms_since(t0), "event_count": len(events), "event_types": [e.get("type") for e in events[:20]], "error": "stream ended without done"}
    except Exception as exc:
        return {"id": row.get("id"), "endpoint": "/chat/stream", "status_code": 0, "ok": False, "first_event_ms": first_event_ms, "first_token_ms": first_token_ms, "done_ms": ms_since(t0), "event_count": len(events), "event_types": [e.get("type") for e in events[:20]], "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:18080")
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--workers", type=int, default=1, help="Use 1 for true sequential E2E latency; >1 for load test")
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--stream-sample", type=int, default=20, help="Measure /chat/stream on first N rows; 0 disables")
    args = ap.parse_args()

    rows = json.loads(Path(args.dataset).read_text(encoding="utf-8"))["rows"]
    if args.limit:
        rows = rows[: args.limit]

    # Preflight health. /health is preferred, root fallback is tolerated.
    preflight = {}
    for path in ["/health", "/docs"]:
        status, body, ms = http_json("GET", args.base_url.rstrip("/") + path, None, timeout=15)
        preflight[path] = {"status_code": status, "latency_ms": ms, "body_keys": sorted(body.keys()) if isinstance(body, dict) else []}
        if 200 <= status < 300:
            break

    details: list[dict[str, Any]] = []
    t_all = time.perf_counter()
    if args.workers <= 1:
        for i, row in enumerate(rows, 1):
            r = call_chat(args.base_url, row, args.timeout)
            details.append(r)
            print(f"[{i}/{len(rows)}] {r['id']} status={r['status_code']} ok={r['ok']} total_ms={r['total_ms']}", flush=True)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(call_chat, args.base_url, row, args.timeout): row for row in rows}
            for i, fut in enumerate(as_completed(futs), 1):
                r = fut.result()
                details.append(r)
                print(f"[{i}/{len(rows)}] {r['id']} status={r['status_code']} ok={r['ok']} total_ms={r['total_ms']}", flush=True)

    stream_details: list[dict[str, Any]] = []
    for i, row in enumerate(rows[: max(0, args.stream_sample)], 1):
        r = call_stream(args.base_url, row, args.timeout)
        stream_details.append(r)
        print(f"[stream {i}/{args.stream_sample}] {r['id']} ok={r['ok']} first_token_ms={r.get('first_token_ms')} done_ms={r.get('done_ms')}", flush=True)

    ok_rows = [r for r in details if r.get("ok")]
    total_values = [float(r["total_ms"]) for r in ok_rows]
    stream_ok = [r for r in stream_details if r.get("ok")]
    payload = {
        "schema_version": "service-e2e-flow-latency.v1",
        "run_id": "service_e2e_flow_latency_260622_qwen35_9b",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "flow_chart_logic": "User question -> FastAPI /chat or /chat/stream -> LangGraph orchestration -> Stage/router/agents/data sources -> Backend API outputs; frontend browser render not measured on RunPod.",
        "base_url": args.base_url,
        "dataset": str(Path(args.dataset).relative_to(ROOT)) if Path(args.dataset).is_absolute() else args.dataset,
        "row_count_requested": len(rows),
        "preflight": preflight,
        "summary": {
            "row_count": len(details),
            "ok_count": len(ok_rows),
            "error_count": len(details) - len(ok_rows),
            "success_rate": round(len(ok_rows) / len(details), 4) if details else 0,
            "chat_total_latency": summarize(total_values),
            "stream_sample_count": len(stream_details),
            "stream_ok_count": len(stream_ok),
            "stream_first_event_latency": summarize([float(r["first_event_ms"]) for r in stream_ok if r.get("first_event_ms") is not None]),
            "stream_first_token_latency": summarize([float(r["first_token_ms"]) for r in stream_ok if r.get("first_token_ms") is not None]),
            "stream_done_latency": summarize([float(r["done_ms"]) for r in stream_ok if r.get("done_ms") is not None]),
            "wall_clock_ms": ms_since(t_all),
        },
        "details": details,
        "stream_details": stream_details,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "summary": payload["summary"]}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
