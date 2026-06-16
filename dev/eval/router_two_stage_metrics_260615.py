# -*- coding: utf-8 -*-
"""Evaluate two-stage router metrics without confidence diagnostics.

Metrics:
Route1:
- route1_accuracy
- route1_macro_f1
- route1_per_label_precision/recall/f1
- route1_confusion_matrix
- route1_gate_safety_recall
- route1_leakage_error_rate
- route1_blocking_error_rate

Route2:
- route2_accuracy_on_query
- route2_macro_f1
- route2_per_label_precision/recall/f1
- route2_confusion_matrix
- route2_top_confusion_pairs

Final:
- final_action_accuracy
- final_action_macro_f1
- query_route_accuracy
- risk_gate_accuracy
- branch_dropoff_rate
- route1_blocking_error_rate
- route1_leakage_error_rate

Prediction modes:
- rule: deterministic keyword router (offline smoke/baseline)
- ollama: call local/remote Ollama OpenAI-compatible API for model sensitivity
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request, error

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "dev" / "eval" / "data" / "router_two_stage_eval_300_v2_260615.json"
REPORT_ROOT = ROOT / "reports" / "experiments" / "router_two_stage_classification"

ROUTE1_LABELS = ["query", "action_request", "approval_required", "off_topic"]
ROUTE2_LABELS = ["anomaly", "cms", "report", "forecast", "rag"]
FINAL_LABELS = [*(f"route:{r}" for r in ROUTE2_LABELS), *(f"gate:{r}" for r in ROUTE1_LABELS if r != "query")]

ANOMALY_KW = ["이상", "알람", "PowerSpike", "COPDrop", "CHPOutage", "NightConsumption", "PVNightNonZero", "HIGH", "MEDIUM", "LOW", "anomaly"]
CMS_KW = ["설비 상태", "점검", "유지보수", "정비", "CMS", "health", "체크리스트", "운전 상태"]
REPORT_KW = ["보고서", "리포트", "요약", "KPI", "개선 포인트", "운영 리스크", "report", "경영진"]
FORECAST_KW = ["예측", "전망", "앞으로", "다음", "계속", "추세", "forecast", "60분 뒤", "내일", "늘어날", "낮아질"]
RAG_KW = ["무엇", "의미", "설명", "계량기", "전압", "전류", "역률", "COP 계산", "자급률", "V.Z", "H1.Z", "H2.Z", "U1", "PF"]
ACTION_KW = ["등록", "배정", "티켓", "일정", "실행", "시작", "백업", "완료로 변경", "작업 관리"]
APPROVAL_KW = ["삭제", "초기화", "강제로 변경", "승인 처리", "권한", "원본", "수정", "중단", "교체"]
OFF_KW = ["점심", "주식", "축구", "연예", "감기약", "라면", "여행", "야구", "SNS", "영화"]


def load_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"] if isinstance(data, dict) and "rows" in data else data
    if not isinstance(rows, list):
        raise ValueError(f"dataset rows must be list: {path}")
    return [r for r in rows if isinstance(r, dict)]


def contains_any(text: str, kws: list[str]) -> bool:
    low = text.lower()
    return any(kw.lower() in low for kw in kws)


def rule_predict(message: str) -> dict[str, str | None]:
    if contains_any(message, APPROVAL_KW):
        route1 = "approval_required"
        return {"route1": route1, "route2": None, "final_action": f"gate:{route1}"}
    if contains_any(message, OFF_KW):
        route1 = "off_topic"
        return {"route1": route1, "route2": None, "final_action": f"gate:{route1}"}
    if contains_any(message, ACTION_KW):
        route1 = "action_request"
        return {"route1": route1, "route2": None, "final_action": f"gate:{route1}"}

    route1 = "query"
    scores = {
        "anomaly": sum(1 for kw in ANOMALY_KW if kw.lower() in message.lower()),
        "cms": sum(1 for kw in CMS_KW if kw.lower() in message.lower()),
        "report": sum(1 for kw in REPORT_KW if kw.lower() in message.lower()),
        "forecast": sum(1 for kw in FORECAST_KW if kw.lower() in message.lower()),
        "rag": sum(1 for kw in RAG_KW if kw.lower() in message.lower()),
    }
    route2 = max(ROUTE2_LABELS, key=lambda r: (scores[r], -ROUTE2_LABELS.index(r)))
    if scores[route2] == 0:
        route2 = "rag"
    return {"route1": route1, "route2": route2, "final_action": f"route:{route2}"}


def ollama_predict(message: str, model: str, base_url: str, timeout: int = 60) -> dict[str, str | None]:
    endpoint = base_url.rstrip("/")
    if endpoint.endswith("/v1"):
        endpoint = endpoint + "/chat/completions"
    elif endpoint.endswith("/v1/chat/completions"):
        pass
    else:
        endpoint = endpoint + "/v1/chat/completions"
    system = (
        "You are a strict JSON router for an EMS agent. "
        "Route1 labels: query, action_request, approval_required, off_topic. "
        "If route1 is query, choose route2 from anomaly, cms, report, forecast, rag. "
        "If route1 is not query, route2 must be null. "
        "Return only JSON: {\"route1\":...,\"route2\":...,\"final_action\":...}. "
        "final_action is route:<route2> for query, gate:<route1> otherwise. No confidence field."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": message},
        ],
        "temperature": 0,
        "max_tokens": 128,
        "stream": False,
    }
    req = request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, flags=re.S)
        parsed = json.loads(match.group(0) if match else content)
        if isinstance(parsed, str):
            # Some Ollama models return a JSON string containing the object.
            inner = re.search(r"\{.*\}", parsed, flags=re.S)
            parsed = json.loads(inner.group(0) if inner else parsed)
        if not isinstance(parsed, dict):
            raise ValueError(f"router output is not an object: {type(parsed).__name__}")
        parsed["_raw_content"] = content
        parsed["_parse_status"] = "parsed_llm_json"
    except Exception as exc:
        parsed = rule_predict(message)
        parsed["_fallback"] = "rule_after_llm_error"  # type: ignore[index]
        parsed["_parse_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"  # type: ignore[index]
    r1 = parsed.get("route1")
    r2 = parsed.get("route2")
    if r1 not in ROUTE1_LABELS:
        fallback = rule_predict(message)
        fallback["_fallback"] = "rule_after_invalid_route1"  # type: ignore[index]
        return fallback
    if r1 == "query":
        if r2 not in ROUTE2_LABELS:
            r2 = "rag"
        final = f"route:{r2}"
    else:
        r2 = None
        final = f"gate:{r1}"
    return {"route1": r1, "route2": r2, "final_action": final}


def prf(labels: list[str], y_true: list[str], y_pred: list[str]) -> dict[str, Any]:
    per = {}
    f1s = []
    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        support = sum(1 for t in y_true if t == label)
        per[label] = {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4), "support": support}
        f1s.append(f1)
    return {"per_label": per, "macro_f1": round(sum(f1s) / len(f1s), 4)}


def confusion(labels: list[str], y_true: list[str], y_pred: list[str]) -> dict[str, dict[str, int]]:
    return {t: {p: sum(1 for tt, pp in zip(y_true, y_pred) if tt == t and pp == p) for p in labels} for t in labels}


def accuracy(y_true: list[str], y_pred: list[str]) -> float:
    return round(sum(1 for t, p in zip(y_true, y_pred) if t == p) / len(y_true), 4) if y_true else 0.0


def evaluate(rows: list[dict[str, Any]], preds: list[dict[str, str | None]]) -> dict[str, Any]:
    exp_r1 = [r["expected_route1"] for r in rows]
    pred_r1 = [str(p["route1"]) for p in preds]
    route1_prf = prf(ROUTE1_LABELS, exp_r1, pred_r1)

    query_idx = [i for i, r in enumerate(rows) if r["expected_route1"] == "query"]
    exp_r2 = [rows[i]["expected_route2"] for i in query_idx]
    pred_r2 = [str(preds[i]["route2"] or "") for i in query_idx]
    route2_prf = prf(ROUTE2_LABELS, exp_r2, pred_r2)

    exp_final = [r["expected_final_action"] for r in rows]
    pred_final = [str(p["final_action"]) for p in preds]
    final_prf = prf(FINAL_LABELS, exp_final, pred_final)

    non_query_idx = [i for i, r in enumerate(rows) if r["expected_route1"] != "query"]
    gate_safe = sum(1 for i in non_query_idx if preds[i]["route1"] != "query") / len(non_query_idx) if non_query_idx else 0.0
    leakage = sum(1 for i in non_query_idx if preds[i]["route1"] == "query") / len(non_query_idx) if non_query_idx else 0.0
    blocking = sum(1 for i in query_idx if preds[i]["route1"] != "query") / len(query_idx) if query_idx else 0.0
    branch_drop = sum(1 for i in query_idx if preds[i]["route1"] == "query" and preds[i]["route2"] != rows[i]["expected_route2"]) / len(query_idx) if query_idx else 0.0
    risk_gate_acc = accuracy([rows[i]["expected_final_action"] for i in non_query_idx], [str(preds[i]["final_action"]) for i in non_query_idx])
    query_route_acc = accuracy([rows[i]["expected_final_action"] for i in query_idx], [str(preds[i]["final_action"]) for i in query_idx])

    conf_pairs = Counter((t, p) for t, p in zip(exp_r2, pred_r2) if t != p)
    top_pairs = [{"expected": a, "predicted": b, "count": c} for (a, b), c in conf_pairs.most_common(10)]

    return {
        "route1": {
            "route1_accuracy": accuracy(exp_r1, pred_r1),
            "route1_macro_f1": route1_prf["macro_f1"],
            "route1_per_label_precision_recall_f1": route1_prf["per_label"],
            "route1_confusion_matrix": confusion(ROUTE1_LABELS, exp_r1, pred_r1),
            "route1_gate_safety_recall": round(gate_safe, 4),
            "route1_leakage_error_rate": round(leakage, 4),
            "route1_blocking_error_rate": round(blocking, 4),
        },
        "route2": {
            "route2_accuracy_on_query": accuracy(exp_r2, pred_r2),
            "route2_macro_f1": route2_prf["macro_f1"],
            "route2_per_label_precision_recall_f1": route2_prf["per_label"],
            "route2_confusion_matrix": confusion(ROUTE2_LABELS, exp_r2, pred_r2),
            "route2_top_confusion_pairs": top_pairs,
        },
        "final": {
            "final_action_accuracy": accuracy(exp_final, pred_final),
            "final_action_macro_f1": final_prf["macro_f1"],
            "query_route_accuracy": query_route_acc,
            "risk_gate_accuracy": risk_gate_acc,
            "branch_dropoff_rate": round(branch_drop, 4),
            "route1_blocking_error_rate": round(blocking, 4),
            "route1_leakage_error_rate": round(leakage, 4),
            "final_action_per_label_precision_recall_f1": final_prf["per_label"],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--run-id", default="run_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    ap.add_argument("--mode", choices=["rule", "ollama"], default="rule")
    ap.add_argument("--model", default=os.getenv("LLM_MODEL", "gemma4:12b"))
    ap.add_argument("--ollama-url", default=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/v1"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    dataset = Path(args.dataset)
    rows = load_rows(dataset)
    if args.limit:
        rows = rows[: args.limit]

    preds = []
    start = time.time()
    for row in rows:
        if args.mode == "rule":
            pred = rule_predict(row["message"])
        else:
            pred = ollama_predict(row["message"], args.model, args.ollama_url)
        preds.append(pred)
    elapsed_ms = round((time.time() - start) * 1000, 2)
    metrics = evaluate(rows, preds)

    out_dir = REPORT_ROOT / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "experiment-metrics.v1",
        "test_id": "router_two_stage_classification",
        "run_id": args.run_id,
        "metric_family": "two_stage_router_no_confidence",
        "dataset": {"path": str(dataset.relative_to(ROOT) if dataset.is_relative_to(ROOT) else dataset), "row_count": len(rows)},
        "mode": args.mode,
        "model": args.model if args.mode == "ollama" else None,
        "phase_latency_ms": {"total": elapsed_ms},
        "summary": {
            **metrics["route1"],
            **{k: v for k, v in metrics["route2"].items() if k not in {"route2_confusion_matrix", "route2_per_label_precision_recall_f1", "route2_top_confusion_pairs"}},
            **{k: v for k, v in metrics["final"].items() if k != "final_action_per_label_precision_recall_f1"},
        },
        "gates": {
            "route1_accuracy_min_0_90": metrics["route1"]["route1_accuracy"] >= 0.90,
            "route2_accuracy_min_0_75": metrics["route2"]["route2_accuracy_on_query"] >= 0.75,
            "final_action_accuracy_min_0_70": metrics["final"]["final_action_accuracy"] >= 0.70,
            "leakage_error_rate_max_0_10": metrics["route1"]["route1_leakage_error_rate"] <= 0.10,
        },
        "errors": [],
        "details": {"metrics": metrics, "predictions": [{"id": r["id"], "message": r["message"], "expected": {"route1": r["expected_route1"], "route2": r["expected_route2"], "final_action": r["expected_final_action"]}, "predicted": p} for r, p in zip(rows, preds)]},
    }
    (out_dir / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"metrics": str((out_dir / "metrics.json").relative_to(ROOT)), "summary": payload["summary"], "gates": payload["gates"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
