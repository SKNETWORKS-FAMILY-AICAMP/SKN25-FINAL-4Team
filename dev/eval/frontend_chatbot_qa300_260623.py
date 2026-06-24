#!/usr/bin/env python3
"""Frontend/backend chatbot QA300 E2E runner with preflight gates.

This runner is for the deployed EMS frontend URL. It intentionally checks:
1) frontend HTML/static availability,
2) backend /api/health and /api/openapi.json,
3) backend LLM configuration via /api/settings and /api/settings/test-llm,
4) QA300 /api/chat execution only when the LLM gate is healthy unless --force is set.

It records HTTP status, structured FastAPI detail, expected route labels, latency, and
summary counters so failures are classified instead of collapsing into generic 500.
"""
from __future__ import annotations

import argparse
import json
import signal
import statistics
import sys
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "dev/eval/data/router_two_stage_eval_300_with_qa60_golden_answers_260622.json"
DEFAULT_OUT = ROOT / "reports/experiments/frontend_chatbot_e2e_260623/frontend_chatbot_qa300_260623_preflight_v2_metrics.json"


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
        return {"count": 0, "avg_ms": 0, "p50_ms": 0, "p95_ms": 0, "max_ms": 0}
    return {
        "count": len(values),
        "avg_ms": round(statistics.mean(values), 2),
        "p50_ms": pct(values, 0.50),
        "p95_ms": pct(values, 0.95),
        "max_ms": round(max(values), 2),
    }


def http(method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 60, read_limit: int | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = request.Request(url, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw_b = resp.read(read_limit) if read_limit else resp.read()
            raw = raw_b.decode("utf-8", errors="replace")
            body: Any
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {"raw": raw[:1000]}
            return {"status_code": resp.status, "ok": 200 <= resp.status < 300, "body": body, "raw": raw[:1000], "latency_ms": ms_since(t0)}
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {"raw": raw[:1000]}
        return {"status_code": e.code, "ok": False, "body": body, "raw": raw[:1000], "latency_ms": ms_since(t0), "error": f"HTTPError:{e.code}"}
    except Exception as e:
        return {"status_code": 0, "ok": False, "body": {}, "raw": "", "latency_ms": ms_since(t0), "error": f"{type(e).__name__}: {e}"}


def classify_error(result: dict[str, Any]) -> str:
    if result.get("ok"):
        return "ok"
    body = result.get("body") or {}
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        if detail.get("error_type") == "llm_unavailable":
            code = detail.get("status_code")
            return f"llm_unavailable_http_{code}" if code else "llm_unavailable"
    if result.get("status_code") == 503:
        return "service_unavailable"
    if result.get("status_code") == 500:
        return "internal_server_error"
    if result.get("status_code") == 422:
        return "validation_error"
    if result.get("status_code") == 0:
        return "network_or_timeout"
    return f"http_{result.get('status_code')}"


def preflight(base_url: str, llm_timeout: int = 180) -> dict[str, Any]:
    base = base_url.rstrip("/")
    checks: dict[str, Any] = {}
    checks["frontend_html"] = http("GET", base + "/", timeout=15, read_limit=800)
    checks["backend_health"] = http("GET", base + "/api/health", timeout=15)
    checks["openapi"] = http("GET", base + "/api/openapi.json", timeout=20, read_limit=1200)
    checks["settings"] = http("GET", base + "/api/settings", timeout=20)
    checks["llm"] = http("POST", base + "/api/settings/test-llm", payload={}, timeout=llm_timeout)
    return checks


def call_chat(base_url: str, row: dict[str, Any], idx: int, timeout: int) -> dict[str, Any]:
    payload = {
        "question": row.get("message", ""),
        "history": [],
        "session_id": f"qa300-260623-{idx}-{uuid.uuid4()}",
        "is_first": True,
        "context": {"eval_run": "frontend_chatbot_qa300_260623", "row_id": row.get("id")},
    }
    res = http("POST", base_url.rstrip("/") + "/api/chat", payload=payload, timeout=timeout)
    body = res.get("body") if isinstance(res.get("body"), dict) else {}
    answer = str(body.get("answer") or "") if isinstance(body, dict) else ""
    timing_trace = body.get("timing_trace") if isinstance(body, dict) and isinstance(body.get("timing_trace"), dict) else {}
    ok = bool(res.get("ok") and answer)
    return {
        "idx": idx,
        "id": row.get("id"),
        "message": row.get("message"),
        "expected_route1": row.get("expected_route1"),
        "expected_route2": row.get("expected_route2"),
        "expected_final_action": row.get("expected_final_action"),
        "status_code": res.get("status_code"),
        "ok": ok,
        "latency_ms": res.get("latency_ms"),
        "timing_trace": timing_trace,
        "intent": body.get("intent") if isinstance(body, dict) else None,
        "answer_chars": len(answer),
        "error_class": classify_error(res),
        "error_detail": body.get("detail") if isinstance(body, dict) else None,
        "body_sample": res.get("raw", "")[:300],
    }


def build_summary(details: list[dict[str, Any]]) -> dict[str, Any]:
    ok_rows = [r for r in details if r.get("ok")]
    all_lat = [float(r["latency_ms"]) for r in details if r.get("latency_ms") is not None]
    ok_lat = [float(r["latency_ms"]) for r in ok_rows if r.get("latency_ms") is not None]
    by_route: dict[str, list[float]] = defaultdict(list)
    by_route_ok: Counter[str] = Counter()
    trace_values: dict[str, list[float]] = defaultdict(list)
    trace_by_route: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    trace_methods: dict[str, Counter[str]] = defaultdict(Counter)
    for r in details:
        key = r.get("expected_route2") or r.get("expected_route1") or "unknown"
        if r.get("latency_ms") is not None:
            by_route[key].append(float(r["latency_ms"]))
        if r.get("ok"):
            by_route_ok[key] += 1
        trace = r.get("timing_trace") or {}
        if isinstance(trace, dict):
            for name, item in trace.items():
                if isinstance(item, dict) and item.get("latency_ms") is not None:
                    val = float(item["latency_ms"])
                    trace_values[name].append(val)
                    trace_by_route[key][name].append(val)
                    if item.get("method"):
                        trace_methods[name][str(item.get("method"))] += 1
    return {
        "ok_count": len(ok_rows),
        "error_count": len(details) - len(ok_rows),
        "success_rate": round(len(ok_rows) / len(details), 4) if details else 0.0,
        "status_counts": dict(Counter(str(r.get("status_code")) for r in details)),
        "error_class_counts": dict(Counter(r.get("error_class") for r in details)),
        "all_latency": summarize(all_lat),
        "ok_latency": summarize(ok_lat),
        "latency_by_expected_route": {k: {**summarize(v), "ok_count": by_route_ok[k]} for k, v in sorted(by_route.items())},
        "component_latency": {k: {**summarize(v), "method_counts": dict(trace_methods.get(k, {}))} for k, v in sorted(trace_values.items())},
        "component_latency_by_expected_route": {
            route: {name: summarize(vals) for name, vals in sorted(parts.items())}
            for route, parts in sorted(trace_by_route.items())
        },
    }


def build_payload(
    *,
    base_url: str,
    dataset: str,
    rows: list[dict[str, Any]],
    details: list[dict[str, Any]],
    preflight_result: dict[str, Any],
    complete: bool,
    skipped_reason: str | None,
    interrupted: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": "frontend-chatbot-e2e.v2",
        "run_id": "frontend_chatbot_qa300_260623",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "frontend_url": base_url.rstrip("/"),
        "api_url": base_url.rstrip("/") + "/api",
        "dataset": str(Path(dataset)),
        "row_count_requested": len(rows),
        "row_count_completed": len(details),
        "complete": complete,
        "interrupted": interrupted,
        "skipped_reason": skipped_reason,
        "preflight": preflight_result,
        "summary": build_summary(details),
        "details": sorted(details, key=lambda x: x["idx"]),
    }


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://121.134.46.24:18000")
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--preflight-llm-timeout", type=int, default=180)
    ap.add_argument("--force", action="store_true", help="Run QA300 even when the LLM preflight fails.")
    args = ap.parse_args()

    rows = json.loads(Path(args.dataset).read_text(encoding="utf-8"))["rows"][: args.limit]
    pf = preflight(args.base_url, llm_timeout=args.preflight_llm_timeout)
    llm_body = pf.get("llm", {}).get("body", {})
    llm_ok = bool(isinstance(llm_body, dict) and llm_body.get("ok") is True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    details: list[dict[str, Any]] = []
    complete = False
    skipped_reason = None
    interrupted = False

    def checkpoint(*, complete_flag: bool = False, interrupted_flag: bool = False) -> dict[str, Any]:
        payload = build_payload(
            base_url=args.base_url,
            dataset=args.dataset,
            rows=rows,
            details=details,
            preflight_result=pf,
            complete=complete_flag,
            skipped_reason=skipped_reason,
            interrupted=interrupted_flag,
        )
        write_payload(out, payload)
        return payload

    def handle_term(signum, frame):  # noqa: ANN001
        payload = checkpoint(complete_flag=False, interrupted_flag=True)
        print(json.dumps({
            "out": str(out),
            "complete": False,
            "interrupted": True,
            "signal": signum,
            "summary": payload["summary"],
        }, ensure_ascii=False, indent=2), flush=True)
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, handle_term)
    signal.signal(signal.SIGINT, handle_term)

    if not llm_ok and not args.force:
        skipped_reason = "llm_preflight_failed"
        print(json.dumps({"skipped": True, "reason": skipped_reason, "llm": llm_body}, ensure_ascii=False, indent=2), flush=True)
        payload = checkpoint(complete_flag=False, interrupted_flag=False)
    else:
        try:
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
                futs = [ex.submit(call_chat, args.base_url, row, i, args.timeout) for i, row in enumerate(rows, 1)]
                for n, fut in enumerate(as_completed(futs), 1):
                    details.append(fut.result())
                    if n <= 5 or n % 25 == 0:
                        print(f"[{n}/{len(rows)}] {build_summary(details)['error_class_counts']}", flush=True)
                    if n <= 5 or n % 10 == 0:
                        checkpoint(complete_flag=False, interrupted_flag=False)
            complete = True
            payload = checkpoint(complete_flag=True, interrupted_flag=False)
        except BaseException:
            interrupted = True
            payload = checkpoint(complete_flag=False, interrupted_flag=True)
            raise

    print(json.dumps({"out": str(out), "complete": complete, "interrupted": interrupted, "skipped_reason": skipped_reason, "summary": payload["summary"]}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
