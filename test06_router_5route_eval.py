# -*- coding: utf-8 -*-
"""Evaluate deterministic 5-route classifier on a 500-question route set."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cms.contracts.core import AgentRequest, ChatRoute
from cms.contracts.agent import classify_route


DEFAULT_DATASET = Path("evals/data/router_5route_eval_500_260610.json")
DEFAULT_OUT_JSON = Path("reports/experiments/test06_router_5route_eval/metrics.json")
DEFAULT_OUT_MD = Path("reports/experiments/test06_router_5route_eval/report.md")
ROUTES = [r.name for r in ChatRoute]


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    predictions = []
    confusion: dict[str, dict[str, int]] = {
        expected: {pred: 0 for pred in ROUTES} for expected in ROUTES
    }
    support = Counter()
    correct_by_route = Counter()
    pred_counts = Counter()

    for row in rows:
        req = AgentRequest(
            user_message=row.get("message", ""),
            route_hint=row.get("route_hint"),
            context=row.get("context", {}),
        )
        route, reason = classify_route(req)
        expected = row["expected_route"]
        predicted = route.name
        is_correct = expected == predicted
        support[expected] += 1
        pred_counts[predicted] += 1
        confusion[expected][predicted] += 1
        if is_correct:
            correct_by_route[expected] += 1
        predictions.append(
            {
                "id": row.get("id"),
                "message": row.get("message"),
                "expected_route": expected,
                "predicted_route": predicted,
                "correct": is_correct,
                "reason": reason,
                "context_keys": sorted((row.get("context") or {}).keys()),
            }
        )

    per_route = {}
    for route in ROUTES:
        tp = confusion[route][route]
        fp = sum(confusion[other][route] for other in ROUTES if other != route)
        fn = sum(confusion[route][other] for other in ROUTES if other != route)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_route[route] = {
            "support": support[route],
            "predicted_count": pred_counts[route],
            "correct": correct_by_route[route],
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    total = len(rows)
    correct = sum(1 for p in predictions if p["correct"])
    macro_f1 = sum(v["f1"] for v in per_route.values()) / len(per_route)
    return {
        "test_id": "test06_router_5route_eval",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_count": total,
        "dataset_note": (
            "Balanced 500-question route-intent set: route2/evidence_answer contains "
            "the original 97 evidence QA hold-out questions plus 3 additions; "
            "routes 1/3/4/5 contain 100 route-specific questions each."
        ),
        "routes": ROUTES,
        "overall": {
            "correct": correct,
            "accuracy": correct / total if total else 0.0,
            "macro_f1": macro_f1,
        },
        "per_route": per_route,
        "confusion_matrix": confusion,
        "errors": [p for p in predictions if not p["correct"]],
        "predictions": predictions,
    }


def write_markdown(result: dict[str, Any], out_md: Path) -> None:
    overall = result["overall"]
    lines = [
        "# Test 06 — 5-router classification evaluation",
        "",
        f"> **실험 ID**: `{result['test_id']}` | **평가 시각(UTC)**: `{result['evaluated_at']}`",
        "",
        "## 1. 목적",
        "",
        "기존 97개 evidence QA hold-out을 route2(`evidence_answer`)에 포함하고 3문항을 추가해 100개로 맞춘 뒤, route1/3/4/5에도 각각 100개 질문을 구성하여 총 500개 기준으로 5-route 분리 성능을 확인한다.",
        "",
        "## 2. Overall",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| dataset_count | {result['dataset_count']} |",
        f"| correct | {overall['correct']} |",
        f"| accuracy | {overall['accuracy']:.1%} |",
        f"| macro_f1 | {overall['macro_f1']:.1%} |",
        "",
        "## 3. Per-route metrics",
        "",
        "| route | support | predicted | correct | precision | recall | f1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for route, m in result["per_route"].items():
        lines.append(
            f"| {route} | {m['support']} | {m['predicted_count']} | {m['correct']} | "
            f"{m['precision']:.1%} | {m['recall']:.1%} | {m['f1']:.1%} |"
        )

    lines.extend(["", "## 4. Confusion matrix", "", "Rows=expected, columns=predicted.", ""])
    routes = result["routes"]
    lines.append("| expected \\ predicted | " + " | ".join(routes) + " |")
    lines.append("|---" + "|---:" * len(routes) + "|")
    for expected in routes:
        row = result["confusion_matrix"][expected]
        lines.append("| " + expected + " | " + " | ".join(str(row[p]) for p in routes) + " |")

    lines.extend(["", "## 5. Errors", ""])
    if not result["errors"]:
        lines.append("No errors.")
    else:
        lines.extend([
            "| id | expected | predicted | reason | message |",
            "|---|---|---|---|---|",
        ])
        for err in result["errors"]:
            message = str(err["message"]).replace("|", "\\|")
            lines.append(
                f"| {err['id']} | {err['expected_route']} | {err['predicted_route']} | "
                f"{err['reason']} | {message} |"
            )

    lines.extend([
        "",
        "## 6. 해석",
        "",
        "- route2(`evidence_answer`)는 기존 97개 evidence QA hold-out 전체를 포함하며, 3문항을 추가해 100개로 맞췄다.",
        "- route1(`quick_answer`), route3(`needs_job`), route4(`approval_required`), route5(`report_shell`)는 각각 100개 질문으로 구성했다.",
        "- `report_shell`은 현재 정책상 `context.qa_blocked=True`가 핵심 신호이므로 메시지 단독 분류가 아니라 context-aware route로 측정했다.",
        "- 이 결과는 deterministic keyword router 기준이다. LLM router를 붙이면 같은 데이터셋으로 baseline 비교가 가능하다.",
    ])
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    rows = json.loads(args.dataset.read_text(encoding="utf-8"))
    result = evaluate(rows)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(result, args.out_md)
    print(json.dumps({
        "dataset_count": result["dataset_count"],
        "accuracy": result["overall"]["accuracy"],
        "macro_f1": result["overall"]["macro_f1"],
        "error_count": len(result["errors"]),
        "out_json": str(args.out_json),
        "out_md": str(args.out_md),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
