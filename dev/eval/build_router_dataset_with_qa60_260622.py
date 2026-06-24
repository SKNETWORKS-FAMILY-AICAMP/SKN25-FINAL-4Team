#!/usr/bin/env python3
"""Build a 300-row router dataset that fully contains the 60-row QA dataset.

Purpose:
- Keep QA/BERTScore 60 rows as an exact subset of router 300 rows by id/message.
- Avoid the previous mismatch where QA ids overlapped but messages differed and 4 QA ids were missing.

Output:
- dev/eval/data/router_two_stage_eval_300_with_qa60_260622.json
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTER_IN = ROOT / "dev/eval/data/router_two_stage_eval_300_260617.json"
QA_IN = ROOT / "dev/eval/data/anomaly_qa_quality_eval_260617.json"
OUT = ROOT / "dev/eval/data/router_two_stage_eval_300_with_qa60_260622.json"


def load_rows(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["rows"]


def router_row_from_qa(q: dict) -> dict:
    return {
        "id": q["id"],
        "message": q.get("message") or q.get("question") or "",
        "expected_route1": q["expected_route1"],
        "expected_route2": q.get("expected_route2"),
        "expected_final_action": q["expected_final_action"],
        "difficulty": q.get("difficulty", "qa_subset"),
        "style": q.get("style", "qa_quality"),
        "source_type": "qa60_embedded_router_eval_260622",
        "notes": "Exact QA/BERTScore 60-row subset member; router message equals QA message.",
        "qa_subset": True,
        "source_qa_id": q["id"],
    }


def main() -> None:
    router_rows = load_rows(ROUTER_IN)
    qa_rows = load_rows(QA_IN)
    rows: list[dict] = []
    seen_ids: set[str] = set()
    seen_messages: set[str] = set()

    for q in qa_rows:
        r = router_row_from_qa(q)
        if r["id"] in seen_ids:
            raise SystemExit(f"duplicate QA id: {r['id']}")
        if r["message"] in seen_messages:
            raise SystemExit(f"duplicate QA message: {r['message']}")
        rows.append(r)
        seen_ids.add(r["id"])
        seen_messages.add(r["message"])

    for r0 in router_rows:
        if len(rows) >= 300:
            break
        if r0["id"] in seen_ids or r0["message"] in seen_messages:
            continue
        r = dict(r0)
        r["qa_subset"] = False
        r["source_type"] = r.get("source_type", "") + "+fill_non_qa_260622"
        rows.append(r)
        seen_ids.add(r["id"])
        seen_messages.add(r["message"])

    if len(rows) != 300:
        raise SystemExit(f"expected 300 rows, got {len(rows)}")

    qa_ids = {q["id"] for q in qa_rows}
    out_ids = {r["id"] for r in rows}
    missing = sorted(qa_ids - out_ids)
    if missing:
        raise SystemExit(f"QA ids missing from output: {missing[:10]}")

    by_id = {r["id"]: r for r in rows}
    mismatches = [q["id"] for q in qa_rows if by_id[q["id"]]["message"] != (q.get("message") or q.get("question") or "")]
    if mismatches:
        raise SystemExit(f"QA message mismatches in output: {mismatches[:10]}")

    payload = {
        "schema_version": "router-two-stage-eval.v4_qa60_contained_260622",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "description": "300-row router dataset with the 60-row QA/BERTScore dataset as an exact subset by id and message. Built for qwen3.5:9b local/service latency tests.",
        "source_files": {
            "router_fill_source": str(ROUTER_IN.relative_to(ROOT)),
            "qa_subset_source": str(QA_IN.relative_to(ROOT)),
        },
        "summary": {
            "row_count": len(rows),
            "qa_subset_count": sum(1 for r in rows if r.get("qa_subset")),
            "duplicate_message_count": len(rows) - len({r["message"] for r in rows}),
            "route1_distribution": dict(Counter(r["expected_route1"] for r in rows)),
            "route2_distribution_on_query": dict(Counter(r["expected_route2"] for r in rows if r["expected_route1"] == "query")),
        },
        "rows": rows,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(OUT)


if __name__ == "__main__":
    main()
