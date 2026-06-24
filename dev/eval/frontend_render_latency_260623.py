#!/usr/bin/env python3
"""Measure browser-side frontend render latency for chat API payload insertion.

This is intentionally separated from /api/chat E2E. It opens the deployed frontend,
performs N backend chat fetches from within Chromium, and measures:
- api_fetch_ms: browser fetch() request/response elapsed time
- json_parse_ms: response.json() time
- dom_render_ms: creating/appending/updating a chat bubble and forcing layout
- browser_total_ms: fetch + parse + DOM render timing inside browser
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://121.134.46.24:18000")
    ap.add_argument("--out", default="reports/experiments/frontend_chatbot_e2e_260623/frontend_render_latency_qwen35_9b_260623.json")
    ap.add_argument("--samples", type=int, default=10)
    ap.add_argument("--question", default="PF1은 무엇을 측정하는 값이야?")
    ap.add_argument("--timeout-ms", type=int, default=300000)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    rows: list[dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(args.base_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        page.evaluate(
            """
            () => {
              let root = document.getElementById('__latency_probe_root');
              if (!root) {
                root = document.createElement('div');
                root.id = '__latency_probe_root';
                root.style.cssText = 'position:fixed;left:-9999px;top:-9999px;width:420px;contain:layout style paint;';
                document.body.appendChild(root);
              }
            }
            """
        )
        for i in range(args.samples):
            row = page.evaluate(
                """
                async ({baseUrl, question, idx}) => {
                  const payload = {question, history: [], session_id: `browser-render-${idx}-${Date.now()}`, is_first: true};
                  const t0 = performance.now();
                  const resp = await fetch(`${baseUrl.replace(/\/$/, '')}/api/chat`, {
                    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
                  });
                  const t1 = performance.now();
                  const data = await resp.json();
                  const t2 = performance.now();
                  const root = document.getElementById('__latency_probe_root');
                  const bubble = document.createElement('div');
                  bubble.className = 'latency-probe-chat-bubble';
                  bubble.textContent = data.answer || '';
                  root.appendChild(bubble);
                  // Force style/layout and a paint opportunity.
                  const h = bubble.offsetHeight;
                  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                  const t3 = performance.now();
                  return {
                    idx,
                    status: resp.status,
                    ok: resp.ok && !!data.answer,
                    answer_chars: (data.answer || '').length,
                    api_fetch_ms: +(t1 - t0).toFixed(2),
                    json_parse_ms: +(t2 - t1).toFixed(2),
                    dom_render_ms: +(t3 - t2).toFixed(2),
                    browser_total_ms: +(t3 - t0).toFixed(2),
                    forced_height: h,
                    timing_trace: data.timing_trace || null,
                  };
                }
                """,
                {"baseUrl": args.base_url, "question": args.question, "idx": i + 1},
            )
            rows.append(row)
            print(f"[{i+1}/{args.samples}] status={row['status']} dom={row['dom_render_ms']}ms browser_total={row['browser_total_ms']}ms", flush=True)
        browser.close()

    summary = {
        "api_fetch_ms": summarize([float(r["api_fetch_ms"]) for r in rows]),
        "json_parse_ms": summarize([float(r["json_parse_ms"]) for r in rows]),
        "dom_render_ms": summarize([float(r["dom_render_ms"]) for r in rows]),
        "browser_total_ms": summarize([float(r["browser_total_ms"]) for r in rows]),
        "ok_count": sum(1 for r in rows if r.get("ok")),
        "row_count": len(rows),
    }
    payload = {
        "schema_version": "frontend-render-latency.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "samples": args.samples,
        "question": args.question,
        "summary": summary,
        "details": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "summary": summary}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
