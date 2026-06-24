#!/usr/bin/env python3
"""Generate GPT-5.5 reference answers for the 300-row service eval dataset.

Input:  dev/eval/data/router_two_stage_eval_300_with_qa60_260622.json
Output: dev/eval/data/router_two_stage_eval_300_with_qa60_gpt55_answers_260622.json

The output keeps all router labels and adds reference_answer_gpt55 plus generation metadata.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IN = ROOT / "dev/eval/data/router_two_stage_eval_300_with_qa60_260622.json"
DEFAULT_OUT = ROOT / "dev/eval/data/router_two_stage_eval_300_with_qa60_gpt55_answers_260622.json"

SYSTEM = """너는 공장 에너지 관리 시스템(EMS)의 실제 서비스 챗봇 답변 기준문을 작성한다.
- 한국어로 간결하고 운영자 친화적으로 답한다.
- 로컬 파일 경로, 논문/Nature/source_url/table provenance 표현을 말하지 않는다.
- 질문이 작업 실행/승인/오프토픽/복합요청이면 실제 실행한 척하지 말고 안전한 안내/확인/분리를 요청한다.
- 수치 근거가 제공되지 않은 query는 구체 숫자를 지어내지 말고, 확인해야 할 데이터 축과 조회 기준을 명확히 말한다.
- 답변 본문만 출력한다."""


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_prompt(row: dict[str, Any]) -> str:
    route1 = row.get("expected_route1")
    route2 = row.get("expected_route2")
    msg = row.get("message", "")
    if route1 == "query":
        return f"""사용자 질문: {msg}
분류: query / {route2}
이 질문에 대한 EMS 챗봇 기준 답변을 작성하라. 현재 수치 evidence가 없으면 숫자를 만들지 말고, 어떤 DB/DW/DM 근거를 확인해야 하는지 간결히 포함하라."""
    if route1 == "action_request":
        return f"""사용자 요청: {msg}
분류: action_request
실제 작업을 수행한 척하지 말고, 작업지시 생성 전 필요한 확인 항목과 승인/담당자 확인이 필요하다는 기준 답변을 작성하라."""
    if route1 == "approval_required":
        return f"""사용자 요청: {msg}
분류: approval_required
승인 없이는 실행할 수 없음을 알리고, 승인 대기/승인권자 확인/안전 조건 확인이 필요하다는 기준 답변을 작성하라."""
    if route1 == "off_topic":
        return f"""사용자 질문: {msg}
분류: off_topic
EMS/에너지/설비 관리 범위를 벗어난 질문임을 정중히 알리고, 시스템 관련 질문으로 다시 요청하도록 안내하는 기준 답변을 작성하라."""
    if route1 == "multi_intent":
        return f"""사용자 질문: {msg}
분류: multi_intent
여러 의도가 섞여 있으므로 한 번에 처리하지 않고, 분석/보고서/작업요청 등으로 나눠 달라는 clarification 기준 답변을 작성하라."""
    return f"사용자 질문: {msg}\n분류: {route1}/{route2}\n기준 답변을 작성하라."


def call_openai(client: OpenAI, model: str, row: dict[str, Any], max_retries: int = 4) -> tuple[str, dict[str, Any]]:
    prompt = build_prompt(row)
    last_exc = None
    for attempt in range(1, max_retries + 1):
        t0 = time.perf_counter()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                # GPT-5.5 may spend part of this budget on reasoning tokens; keep
                # enough headroom so the visible answer is not empty.
                max_completion_tokens=1600,
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            content = (resp.choices[0].message.content or "").strip()
            return content, {
                "model": model,
                "latency_ms": round(latency_ms, 2),
                "attempt": attempt,
                "finish_reason": resp.choices[0].finish_reason,
                "usage": resp.usage.model_dump() if getattr(resp, "usage", None) else None,
            }
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == max_retries:
                break
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"OpenAI generation failed for {row.get('id')}: {type(last_exc).__name__}: {last_exc}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(DEFAULT_IN))
    ap.add_argument("--output", default=str(DEFAULT_OUT))
    ap.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.5"))
    ap.add_argument("--limit", type=int, default=0, help="0 means all rows")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is missing")

    src = load_json(Path(args.input))
    rows = src["rows"]
    if args.limit:
        rows = rows[: args.limit]

    out_path = Path(args.output)
    existing: dict[str, dict[str, Any]] = {}
    if args.resume and out_path.exists():
        old = load_json(out_path)
        existing = {r["id"]: r for r in old.get("rows", []) if r.get("reference_answer_gpt55")}

    client = OpenAI()
    out_rows = []
    for idx, row in enumerate(rows, 1):
        if row["id"] in existing:
            out_rows.append(existing[row["id"]])
            print(f"[{idx}/{len(rows)}] resume {row['id']}", flush=True)
            continue
        answer, meta = call_openai(client, args.model, row)
        new_row = dict(row)
        new_row["reference_answer_gpt55"] = answer
        new_row["gpt55_generation"] = meta
        out_rows.append(new_row)
        print(f"[{idx}/{len(rows)}] generated {row['id']} {meta['latency_ms']} ms", flush=True)
        # checkpoint every row for resume safety
        payload = {
            "schema_version": "router-two-stage-eval.v4_qa60_contained_gpt55_answers_260622",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_dataset": str(Path(args.input).relative_to(ROOT)) if Path(args.input).is_absolute() else args.input,
            "model": args.model,
            "summary": {
                "row_count": len(out_rows),
                "route1_distribution": dict(Counter(r.get("expected_route1") for r in out_rows)),
                "qa_subset_count": sum(1 for r in out_rows if r.get("qa_subset")),
            },
            "rows": out_rows,
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"output": str(out_path), "rows": len(out_rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
