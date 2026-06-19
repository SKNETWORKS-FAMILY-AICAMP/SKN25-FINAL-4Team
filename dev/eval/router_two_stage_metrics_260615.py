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
from concurrent.futures import ThreadPoolExecutor
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
DEFAULT_DATASET = ROOT / "dev" / "eval" / "data" / "router_two_stage_eval_300_260617.json"
REPORT_ROOT = ROOT / "reports" / "experiments" / "router_two_stage_classification"

ROUTE1_LABELS = ["query", "action_request", "approval_required", "off_topic", "multi_intent"]
ROUTE2_LABELS = ["anomaly", "cms", "report", "forecast", "rag"]
FINAL_LABELS = [*(f"route:{r}" for r in ROUTE2_LABELS), *(f"gate:{r}" for r in ROUTE1_LABELS if r != "query")]

ANOMALY_KW = ["이상", "이슈", "경보", "알람", "PowerSpike", "COPDrop", "CHPOutage", "NightConsumption", "PVNightNonZero", "HIGH", "MEDIUM", "LOW", "anomaly"]
CMS_KW = ["설비 상태", "점검", "유지보수", "정비", "CMS", "health", "체크리스트", "운전 상태"]
REPORT_KW = ["보고서", "리포트", "요약", "KPI", "개선 포인트", "운영 리스크", "report", "경영진"]
FORECAST_KW = ["예측", "전망", "앞으로", "다음", "계속", "추세", "forecast", "60분 뒤", "내일", "늘어날", "낮아질"]
RAG_KW = ["무엇", "의미", "설명", "계량기", "전압", "전류", "역률", "COP 계산", "자급률", "V.Z", "H1.Z", "H2.Z", "U1", "PF"]
ACTION_KW = ["등록", "배정", "티켓", "일정", "실행", "시작", "백업", "완료로 변경", "작업 관리"]
APPROVAL_KW = ["삭제", "초기화", "강제로 변경", "승인 처리", "권한", "원본", "수정", "중단", "교체"]
OFF_KW = ["점심", "주식", "축구", "연예", "감기약", "라면", "여행", "야구", "SNS", "영화"]
MULTI_INTENT_KW = ["하고", "한 뒤", "동시에", "같이", "까지", "분석하고", "요약하고", "등록", "티켓", "배정", "보고서", "예측"]


def load_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"] if isinstance(data, dict) and "rows" in data else data
    if not isinstance(rows, list):
        raise ValueError(f"dataset rows must be list: {path}")
    return [r for r in rows if isinstance(r, dict)]


def contains_any(text: str, kws: list[str]) -> bool:
    low = text.lower()
    return any(kw.lower() in low for kw in kws)


def is_multi_intent(message: str) -> bool:
    low = message.lower()
    branch_hits = sum(1 for kws in [ANOMALY_KW, CMS_KW, REPORT_KW, FORECAST_KW, RAG_KW, ACTION_KW, APPROVAL_KW] if contains_any(low, kws))
    connector_hit = any(x in message for x in ["하고", "한 뒤", "동시에", "같이", "까지", "및", "그리고"])
    # Multi-intent is only for compound operational requests; do not classify
    # a simple report about anomalies as multi_intent unless it also asks for
    # another action/forecast/CMS/RAG task in the same utterance.
    return connector_hit and branch_hits >= 2


def rule_predict(message: str) -> dict[str, str | None]:
    if is_multi_intent(message):
        route1 = "multi_intent"
        return {"route1": route1, "route2": None, "final_action": f"gate:{route1}"}
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


def api_chat_endpoint_from_base(base_url: str) -> str:
    endpoint = base_url.rstrip("/")
    if endpoint.endswith("/v1/chat/completions"):
        endpoint = endpoint[: -len("/v1/chat/completions")]
    elif endpoint.endswith("/v1"):
        endpoint = endpoint[: -len("/v1")]
    return endpoint + "/api/chat"


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse model output robustly: raw JSON, fenced JSON, or first balanced object."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    candidates = [cleaned]
    start = cleaned.find("{")
    if start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(cleaned[start:], start):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append(cleaned[start : i + 1])
                        break
    last_exc: Exception | None = None
    for cand in candidates:
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:
            last_exc = exc
    raise ValueError(f"cannot parse JSON object: {last_exc}")


def normalize_route1(value: Any) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "query": "query", "질문": "query", "qa": "query", "question": "query", "route": "query",
        "action": "action_request", "action_request": "action_request", "job_or_workflow": "action_request", "작업": "action_request", "실행": "action_request",
        "approval": "approval_required", "approval_required": "approval_required", "safety_or_control": "approval_required", "승인": "approval_required", "위험": "approval_required",
        "offtopic": "off_topic", "off_topic": "off_topic", "other": "off_topic", "irrelevant": "off_topic", "일반": "off_topic", "무관": "off_topic",
        "multi_intent": "multi_intent", "multiintent": "multi_intent", "clarification_required": "multi_intent", "clarification": "multi_intent", "both": "multi_intent", "복합": "multi_intent", "다중": "multi_intent",
    }
    if v in aliases:
        return aliases[v]

    # If a model puts a natural-language explanation inside the value, extract
    # the branch only when exactly one allowed label is unambiguously present.
    # The original value is preserved under _raw_route1; route1 itself is never
    # allowed to store natural-language text.
    raw = str(value).strip().lower()
    patterns = {
        "query": [r"\bquery\b", "일반 질의", "질의", "질문"],
        "action_request": [r"\baction_request\b", "작업 요청", "작업", "실행 요청"],
        "approval_required": [r"\bapproval_required\b", "승인 필요", "승인", "권한 확인"],
        "off_topic": [r"\boff_topic\b", "무관", "관련 없는", "범위 밖"],
        "multi_intent": [r"\bmulti_intent\b", r"\bclarification_required\b", "복합", "다중", "여러 작업", "질문을 분리"],
    }
    hits = [label for label, pats in patterns.items() if any(re.search(pat, raw) for pat in pats)]
    uniq = list(dict.fromkeys(hits))
    return uniq[0] if len(uniq) == 1 else None


def normalize_route2(value: Any) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower().replace("-", "_").replace(" ", "_").replace("route:", "")
    aliases = {
        "anomaly": "anomaly", "anomalies": "anomaly", "abnormal": "anomaly", "이상": "anomaly", "경보": "anomaly",
        "cms": "cms", "maintenance": "cms", "equipment": "cms", "설비": "cms", "정비": "cms",
        "report": "report", "summary": "report", "리포트": "report", "보고서": "report", "요약": "report",
        "forecast": "forecast", "prediction": "forecast", "예측": "forecast", "전망": "forecast",
        "rag": "rag", "lookup": "rag", "knowledge": "rag", "definition": "rag", "검색": "rag", "설명": "rag",
    }
    if v in aliases:
        return aliases[v]

    raw = str(value).strip().lower()
    patterns = {
        "anomaly": [r"\banomaly\b", "이상", "경보", "알람"],
        "cms": [r"\bcms\b", "설비", "정비", "점검", "유지보수"],
        "report": [r"\breport\b", "보고서", "리포트", "요약"],
        "forecast": [r"\bforecast\b", "예측", "전망", "추세"],
        "rag": [r"\brag\b", "검색", "설명", "정의", "용어"],
    }
    hits = [label for label, pats in patterns.items() if any(re.search(pat, raw) for pat in pats)]
    uniq = list(dict.fromkeys(hits))
    return uniq[0] if len(uniq) == 1 else None


def classify_fallback_reason(error_text: str | None = None, *, raw_content: str | None = None, parsed_status: str | None = None, invalid_label: bool = False) -> str:
    """Classify fallback root cause for post-run diagnostics."""
    text = " ".join(x for x in [error_text or "", raw_content or "", parsed_status or ""] if x).lower()
    if "out of memory" in text or "oom" in text or "cuda error" in text:
        return "runtime_oom"
    if "timed out" in text or "timeout" in text:
        return "runtime_timeout"
    if "urlerror" in text or "httperror" in text or "connection refused" in text or "api" in text and "error" in text:
        return "runtime_api_error"
    if invalid_label:
        return "contract_invalid_label"
    if "cannot parse json" in text or "expecting value" in text or "jsondecodeerror" in text:
        if not (raw_content or "").strip():
            return "contract_empty_or_reasoning_only"
        return "contract_malformed_json"
    return "contract_or_unknown"


def ollama_predict(message: str, model: str, base_url: str, timeout: int = 180, think: bool | None = False) -> dict[str, str | None]:
    endpoint = api_chat_endpoint_from_base(base_url)
    system = (
        "You are a deterministic branch selector for an EMS router. "
        "Your task is NOT to answer the user. Your only task is to choose the routing branch. "
        "Return exactly one JSON object and nothing else. No prose, no markdown, no comments, no reasoning. "
        "The JSON object must contain exactly these three keys: route1, route2, final_action. "
        "Every value must be one of the allowed literal values below. Do not put explanations inside values. "
        "Allowed route1 values: query, action_request, approval_required, off_topic, multi_intent. "
        "Allowed route2 values when route1 is query: anomaly, cms, report, forecast, rag. "
        "Use route1=multi_intent only when the user asks for two or more distinct tasks/branches in one request and the safe response should ask them to split or clarify. "
        "When route1 is not query, route2 must be null. "
        "Allowed final_action values: route:anomaly, route:cms, route:report, route:forecast, route:rag, "
        "gate:action_request, gate:approval_required, gate:off_topic, gate:multi_intent. "
        "Consistency rules: if route1 is query, final_action must be route:<route2>; "
        "if route1 is not query, final_action must be gate:<route1>. "
        "Valid example: {\"route1\":\"query\",\"route2\":\"anomaly\",\"final_action\":\"route:anomaly\"}. "
        "Valid multi_intent example: {\"route1\":\"multi_intent\",\"route2\":null,\"final_action\":\"gate:multi_intent\"}. "
        "Invalid examples: natural-language values, extra keys, markdown fences, multiple JSON objects, empty content."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": message},
        ],
        "stream": False,
        "think": think,
        "format": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "route1": {"type": "string", "enum": ROUTE1_LABELS},
                "route2": {"type": ["string", "null"], "enum": [*ROUTE2_LABELS, None]},
                "final_action": {"type": "string", "enum": FINAL_LABELS},
            },
            "required": ["route1", "route2", "final_action"],
        },
        "options": {"temperature": 0, "top_p": 0, "num_predict": 96, "repeat_penalty": 1.0},
    }
    req = request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        msg = data.get("message", {})
        content = str(msg.get("content") or "").strip()
        reasoning = str(msg.get("reasoning") or "").strip()
        if not content and reasoning:
            content = reasoning
        parsed = extract_json_object(content)
        if not isinstance(parsed, dict):
            raise ValueError(f"router output is not an object: {type(parsed).__name__}")
        parsed["_raw_content"] = content
        parsed["_raw_reasoning_prefix"] = reasoning[:500]
        parsed["_parse_status"] = "parsed_llm_json"
        parsed["_api_model"] = data.get("model")
        parsed["_api_endpoint"] = "/api/chat"
        parsed["_think"] = think
        parsed["_raw_message_keys"] = sorted(msg.keys())
    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)[:200]}"
        rule_fallback = rule_predict(message)
        parsed = {
            "route1": "__fallback__",
            "route2": "__fallback__",
            "final_action": "__fallback__",
            "_fallback": "invalid_after_llm_error",
            "_fallback_reason": classify_fallback_reason(err),
            "_fallback_rule_prediction": rule_fallback,
            "_parse_error": err,
            "_think": think,
        }
    r1_raw = parsed.get("route1")
    r2_raw = parsed.get("route2")
    r1 = normalize_route1(r1_raw)
    r2 = normalize_route2(r2_raw)
    parsed["_raw_route1"] = r1_raw
    parsed["_raw_route2"] = r2_raw
    if r1 not in ROUTE1_LABELS:
        invalid = {"route1": "__fallback__", "route2": "__fallback__", "final_action": "__fallback__"}
        invalid["_fallback"] = "invalid_after_bad_route1"  # type: ignore[index]
        invalid["_fallback_reason"] = classify_fallback_reason(parsed.get("_parse_error"), raw_content=parsed.get("_raw_content"), parsed_status=parsed.get("_parse_status"), invalid_label=True)  # type: ignore[index]
        invalid["_fallback_rule_prediction"] = rule_predict(message)  # type: ignore[index]
        invalid["_parse_status"] = parsed.get("_parse_status")  # type: ignore[index]
        invalid["_parse_error"] = parsed.get("_parse_error") or "invalid_route1"  # type: ignore[index]
        invalid["_raw_route1"] = r1_raw  # type: ignore[index]
        invalid["_raw_route2"] = r2_raw  # type: ignore[index]
        invalid["_raw_content"] = parsed.get("_raw_content")  # type: ignore[index]
        invalid["_think"] = think  # type: ignore[index]
        return invalid
    if r1 == "query":
        if r2 not in ROUTE2_LABELS:
            r2 = "rag"
        final = f"route:{r2}"
    else:
        r2 = None
        final = f"gate:{r1}"
    out = {"route1": r1, "route2": r2, "final_action": final}
    for k, v in parsed.items():
        if str(k).startswith("_"):
            out[k] = v
    return out


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
    ap.add_argument("--workers", type=int, default=int(os.getenv("ROUTER_WORKERS", "1")))
    ap.add_argument("--think", choices=["on", "off", "none"], default=os.getenv("OLLAMA_THINK", "off"))
    args = ap.parse_args()

    dataset = Path(args.dataset)
    rows = load_rows(dataset)
    if args.limit:
        rows = rows[: args.limit]

    preds = []
    start = time.time()
    think_value = None if args.think == "none" else args.think == "on"
    if args.mode == "ollama" and args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            preds = list(ex.map(lambda row: ollama_predict(row["message"], args.model, args.ollama_url, think=think_value), rows))
    else:
        for row in rows:
            if args.mode == "rule":
                pred = rule_predict(row["message"])
            else:
                pred = ollama_predict(row["message"], args.model, args.ollama_url, think=think_value)
            preds.append(pred)
    elapsed_ms = round((time.time() - start) * 1000, 2)
    metrics = evaluate(rows, preds)

    out_dir = REPORT_ROOT / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    parsed_llm_json_count = sum(1 for p in preds if p.get("_parse_status") == "parsed_llm_json")
    fallback_count = sum(1 for p in preds if p.get("_fallback"))
    parse_error_count = sum(1 for p in preds if p.get("_parse_error"))
    invalid_prediction_count = sum(1 for p in preds if p.get("route1") == "__fallback__" or p.get("final_action") == "__fallback__")
    fallback_reason_counts = dict(Counter(str(p.get("_fallback_reason") or "unknown") for p in preds if p.get("_fallback")))
    payload = {
        "schema_version": "experiment-metrics.v1",
        "test_id": "router_two_stage_classification",
        "run_id": args.run_id,
        "metric_family": "two_stage_router_no_confidence",
        "metric_note": "Fallback rows are scored as invalid labels (__fallback__), not as rule-router predictions. Therefore all-fallback runs produce zero accuracy/F1; fallback_rule_prediction is retained only for debugging.",
        "dataset": {"path": str(dataset.relative_to(ROOT) if dataset.is_relative_to(ROOT) else dataset), "row_count": len(rows)},
        "mode": args.mode,
        "model": args.model if args.mode == "ollama" else None,
        "workers": args.workers,
        "think": args.think if args.mode == "ollama" else None,
        "runtime": {
            "ollama_url": args.ollama_url if args.mode == "ollama" else None,
            "ollama_num_parallel": os.getenv("OLLAMA_NUM_PARALLEL"),
            "ollama_max_loaded_models": os.getenv("OLLAMA_MAX_LOADED_MODELS"),
            "ollama_keep_alive": os.getenv("OLLAMA_KEEP_ALIVE"),
        },
        "phase_latency_ms": {"total": elapsed_ms},
        "summary": {
            **metrics["route1"],
            **{k: v for k, v in metrics["route2"].items() if k not in {"route2_confusion_matrix", "route2_per_label_precision_recall_f1", "route2_top_confusion_pairs"}},
            **{k: v for k, v in metrics["final"].items() if k != "final_action_per_label_precision_recall_f1"},
            "parsed_llm_json_count": parsed_llm_json_count,
            "fallback_count": fallback_count,
            "parse_error_count": parse_error_count,
            "invalid_prediction_count": invalid_prediction_count,
            "fallback_reason_counts": fallback_reason_counts,
        },
        "gates": {
            "route1_accuracy_min_0_90": metrics["route1"]["route1_accuracy"] >= 0.90,
            "route2_accuracy_min_0_75": metrics["route2"]["route2_accuracy_on_query"] >= 0.75,
            "final_action_accuracy_min_0_70": metrics["final"]["final_action_accuracy"] >= 0.70,
            "leakage_error_rate_max_0_10": metrics["route1"]["route1_leakage_error_rate"] <= 0.10,
        },
        "errors": [{"id": r["id"], "error": p.get("_parse_error"), "fallback": p.get("_fallback"), "fallback_reason": p.get("_fallback_reason")} for r, p in zip(rows, preds) if p.get("_parse_error") or p.get("_fallback")],
        "details": {"metrics": metrics, "predictions": [{"id": r["id"], "message": r["message"], "expected": {"route1": r["expected_route1"], "route2": r["expected_route2"], "final_action": r["expected_final_action"]}, "predicted": p} for r, p in zip(rows, preds)]},
    }
    (out_dir / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"metrics": str((out_dir / "metrics.json").relative_to(ROOT)), "summary": payload["summary"], "gates": payload["gates"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
