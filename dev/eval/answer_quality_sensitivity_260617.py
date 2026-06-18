# -*- coding: utf-8 -*-
"""Answer quality sensitivity evaluation for anomaly QA rows.

This evaluator is intentionally separate from router classification metrics.
It uses rows that contain answer_text/answer_evidence and asks the sLLM to
produce an operational factory EMS chatbot answer from the supplied evidence.

Metrics are lightweight RAGAS-style proxies that do not require external judge LLMs:
- reference_token_f1: token overlap against answer_text
- rouge_l_f1: LCS-based token ROUGE-L F1 against answer_text
- numeric_precision/recall/f1: numeric fact preservation vs answer_text
- evidence_numeric_recall: numeric coverage against answer_evidence
- source_leakage_rate: whether answer leaks provenance wording such as Nature/논문
- answer_quality_composite: weighted summary score
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "dev" / "eval" / "data" / "router_two_stage_eval_300_260617.json"
REPORT_ROOT = ROOT / "reports" / "experiments" / "answer_quality_sensitivity"

MODEL_PARAM_B = {
    "qwen3.5:0.8b": 0.8,
    "llama3.2:3b": 3.0,
    "qwen3.5:2b": 2.0,
    "qwen3.5:4b": 4.0,
    "phi4-mini:3.8b": 3.8,
    "qwen3:8b": 8.0,
    "llama3.1:8b": 8.0,
    "deepseek-r1:8b": 8.0,
    "exaone3.5:7.8b": 7.8,
    "qwen3.5:9b": 9.0,
    "gemma4:12b": 12.0,
}

SOURCE_LEAKAGE_TERMS = ["nature", "논문", "paper", "table", "출처", "source_url", "scientific data"]


def load_rows(path: Path, limit: int = 0) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"] if isinstance(data, dict) and "rows" in data else data
    qa_rows = []
    for r in rows:
        ref = r.get("reference_answer") or r.get("reference_answer_gpt55") or r.get("answer_text")
        ev = r.get("answer_evidence") or r.get("evidence")
        if ref and ev:
            nr = dict(r)
            nr["answer_text"] = ref
            nr["answer_evidence"] = ev
            qa_rows.append(nr)
    return qa_rows[:limit] if limit else qa_rows


def api_chat_endpoint_from_base(base_url: str) -> str:
    endpoint = base_url.rstrip("/")
    # Use native Ollama /api/chat for answer generation because qwen thinking
    # models may return empty `content` through the OpenAI-compatible endpoint.
    if endpoint.endswith("/v1/chat/completions"):
        endpoint = endpoint[: -len("/v1/chat/completions")]
    elif endpoint.endswith("/v1"):
        endpoint = endpoint[: -len("/v1")]
    return endpoint + "/api/chat"


def call_ollama(model: str, base_url: str, message: str, evidence: Any, timeout: int = 180, think: bool | None = False) -> tuple[str, dict[str, Any]]:
    endpoint = api_chat_endpoint_from_base(base_url)
    system = (
        "너는 실제 공장 에너지 관리 시스템의 챗봇이다. "
        "사용자에게 연구 출처, 논문, Nature, table, source_url 같은 출처 표현을 절대 말하지 않는다. "
        "제공된 evidence 안의 수치와 설비 의미만 사용해서 한국어로 간결하게 답한다. "
        "없는 내용은 추측하지 않는다."
    )
    user = (
        "질문:\n" + message + "\n\n"
        "evidence(JSON):\n" + json.dumps(evidence, ensure_ascii=False, sort_keys=True) + "\n\n"
        "답변만 작성해."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        # Qwen thinking models may otherwise place all text in `reasoning` and
        # leave the user-facing OpenAI-compatible `content` field empty.
        "think": think,
        "options": {"temperature": 0, "num_predict": 384},
    }
    req = request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    msg = data.get("message", {})
    # Prefer user-facing content. Ollama thinking models commonly return
    # `thinking` instead of `content`; do NOT score thinking as an answer.
    content = str(msg.get("content") or "").strip()
    reasoning_text = str(msg.get("reasoning") or msg.get("thinking") or "").strip()
    used_reasoning_fallback = False
    if not content and msg.get("reasoning"):
        content = str(msg.get("reasoning") or "").strip()
        used_reasoning_fallback = True
    empty_content_with_thinking = (not content) and bool(reasoning_text)
    return content, {
        "api_model": data.get("model"),
        "api_endpoint": "/api/chat",
        "raw_message_keys": sorted(msg.keys()),
        "used_reasoning_fallback": used_reasoning_fallback,
        "empty_content_with_thinking": empty_content_with_thinking,
        "reasoning_or_thinking_chars": len(reasoning_text),
        "think": think,
    }


def tokenize(text: str) -> list[str]:
    # Korean-friendly lightweight tokenization: Korean/Latin/number groups + symbols ignored.
    return re.findall(r"[가-힣A-Za-z]+|\d+(?:\.\d+)?", text.lower().replace(",", ""))


def token_f1(pred: str, ref: str) -> float:
    p = tokenize(pred)
    r = tokenize(ref)
    if not p and not r:
        return 1.0
    if not p or not r:
        return 0.0
    cp, cr = Counter(p), Counter(r)
    common = sum((cp & cr).values())
    precision = common / len(p) if p else 0.0
    recall = common / len(r) if r else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def rouge_l_f1(pred: str, ref: str) -> float:
    p, r = tokenize(pred), tokenize(ref)
    if not p and not r:
        return 1.0
    if not p or not r:
        return 0.0
    # DP with small answer lengths.
    dp = [0] * (len(r) + 1)
    for x in p:
        prev = 0
        for j, y in enumerate(r, 1):
            cur = dp[j]
            if x == y:
                dp[j] = prev + 1
            else:
                dp[j] = max(dp[j], dp[j - 1])
            prev = cur
    lcs = dp[-1]
    precision = lcs / len(p)
    recall = lcs / len(r)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def numbers_from_text(text: str) -> list[str]:
    vals = []
    for m in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", text):
        norm = m.replace(",", "")
        if norm.endswith(".0"):
            norm = norm[:-2]
        vals.append(norm)
    return vals


def numbers_from_obj(obj: Any) -> list[str]:
    vals: list[str] = []
    if isinstance(obj, dict):
        for v in obj.values():
            vals.extend(numbers_from_obj(v))
    elif isinstance(obj, list):
        for v in obj:
            vals.extend(numbers_from_obj(v))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if isinstance(obj, float) and obj.is_integer():
            vals.append(str(int(obj)))
        else:
            vals.append(str(obj))
    elif isinstance(obj, str):
        vals.extend(numbers_from_text(obj))
    return vals


def multiset_scores(pred_vals: list[str], ref_vals: list[str]) -> tuple[float, float, float]:
    cp, cr = Counter(pred_vals), Counter(ref_vals)
    common = sum((cp & cr).values())
    precision = common / sum(cp.values()) if cp else (1.0 if not cr else 0.0)
    recall = common / sum(cr.values()) if cr else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def leakage(text: str) -> bool:
    low = text.lower()
    return any(term in low for term in SOURCE_LEAKAGE_TERMS)


def evaluate_answer(pred: str, ref: str, evidence: Any) -> dict[str, float | bool | int]:
    tf1 = token_f1(pred, ref)
    rouge = rouge_l_f1(pred, ref)
    np_, nr, nf = multiset_scores(numbers_from_text(pred), numbers_from_text(ref))
    _, ev_nr, _ = multiset_scores(numbers_from_text(pred), numbers_from_obj(evidence))
    leak = leakage(pred)
    composite = 0.35 * nf + 0.25 * ev_nr + 0.20 * rouge + 0.15 * tf1 + 0.05 * (0.0 if leak else 1.0)
    return {
        "reference_token_f1": round(tf1, 4),
        "rouge_l_f1": round(rouge, 4),
        "numeric_precision": round(np_, 4),
        "numeric_recall": round(nr, 4),
        "numeric_f1": round(nf, 4),
        "evidence_numeric_recall": round(ev_nr, 4),
        "source_leakage": leak,
        "answer_quality_composite": round(composite, 4),
    }


def mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    def ranks(vals: list[float]) -> list[float]:
        order = sorted((v, i) for i, v in enumerate(vals))
        out = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and order[j + 1][0] == order[i][0]:
                j += 1
            rank = (i + j + 2) / 2.0
            for _, idx in order[i:j+1]:
                out[idx] = rank
            i = j + 1
        return out
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx)/len(rx), sum(ry)/len(ry)
    num = sum((a-mx)*(b-my) for a,b in zip(rx,ry))
    denx = math.sqrt(sum((a-mx)**2 for a in rx))
    deny = math.sqrt(sum((b-my)**2 for b in ry))
    return round(num/(denx*deny), 4) if denx and deny else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--model", required=True)
    ap.add_argument("--ollama-url", default=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/v1"))
    ap.add_argument("--run-id", default="run_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=int(os.getenv("RAGAS_WORKERS", "1")))
    ap.add_argument("--think", choices=["on", "off", "none"], default=os.getenv("OLLAMA_THINK", "off"))
    ap.add_argument("--bertscore", action="store_true", help="Compute BERTScore with the bert-score package if installed.")
    ap.add_argument("--bertscore-model", default=os.getenv("BERTSCORE_MODEL", "distilbert-base-multilingual-cased"))
    args = ap.parse_args()

    dataset = Path(args.dataset)
    rows = load_rows(dataset, args.limit)
    out_dir = REPORT_ROOT / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    start = time.time()
    think_value = None if args.think == "none" else args.think == "on"
    def run_one(row: dict[str, Any]) -> dict[str, Any]:
        t0 = time.time()
        try:
            answer, meta = call_ollama(args.model, args.ollama_url, row["message"], row.get("answer_evidence"), think=think_value)
            err = "empty_content_with_thinking" if meta.get("empty_content_with_thinking") else ("empty_answer" if not answer.strip() else None)
        except Exception as exc:
            answer, meta = "", {"api_model": None, "raw_message_keys": []}
            err = f"{type(exc).__name__}: {str(exc)[:300]}"
        scores = evaluate_answer(answer, row["answer_text"], row.get("answer_evidence"))
        return {
            "id": row.get("id"),
            "message": row.get("message"),
            "reference_answer": row.get("answer_text"),
            "predicted_answer": answer,
            "answer_evidence": row.get("answer_evidence"),
            "scores": scores,
            "latency_ms": round((time.time() - t0) * 1000, 2),
            "api_meta": meta,
            "error": err,
        }

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            details = list(ex.map(run_one, rows))
    else:
        details = [run_one(row) for row in rows]
    errors = [{"id": d.get("id"), "error": d.get("error")} for d in details if d.get("error")]
    total_ms = round((time.time() - start) * 1000, 2)
    metric_names = [
        "reference_token_f1", "rouge_l_f1", "numeric_precision", "numeric_recall", "numeric_f1",
        "evidence_numeric_recall", "answer_quality_composite",
    ]
    summary = {name: mean([float(d["scores"][name]) for d in details]) for name in metric_names}
    summary["source_leakage_rate"] = mean([1.0 if d["scores"]["source_leakage"] else 0.0 for d in details])
    summary["error_rate"] = mean([1.0 if d["error"] else 0.0 for d in details])
    summary["avg_latency_ms_per_row"] = mean([float(d["latency_ms"]) for d in details])
    bertscore_status = {"enabled": args.bertscore, "model": args.bertscore_model, "error": None}
    if args.bertscore:
        try:
            from bert_score import score as bert_score
            preds_text = [d["predicted_answer"] for d in details]
            refs_text = [d["reference_answer"] for d in details]
            bp, br, bf = bert_score(preds_text, refs_text, model_type=args.bertscore_model, lang="ko", verbose=False)
            for d, p_val, r_val, f_val in zip(details, bp.tolist(), br.tolist(), bf.tolist()):
                d["scores"]["bertscore_precision"] = round(float(p_val), 4)
                d["scores"]["bertscore_recall"] = round(float(r_val), 4)
                d["scores"]["bertscore_f1"] = round(float(f_val), 4)
            summary["bertscore_precision"] = mean([float(d["scores"]["bertscore_precision"]) for d in details])
            summary["bertscore_recall"] = mean([float(d["scores"]["bertscore_recall"]) for d in details])
            summary["bertscore_f1"] = mean([float(d["scores"]["bertscore_f1"]) for d in details])
        except Exception as exc:
            bertscore_status["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"

    payload = {
        "schema_version": "answer-quality-metrics.v1",
        "test_id": "answer_quality_sensitivity",
        "run_id": args.run_id,
        "metric_family": "ragas_style_reference_and_evidence_proxy",
        "dataset": {"path": str(dataset.relative_to(ROOT) if dataset.is_relative_to(ROOT) else dataset), "row_count": len(rows), "filter": "rows with answer_text and answer_evidence"},
        "mode": "ollama_answer_generation",
        "model": args.model,
        "model_params_b": MODEL_PARAM_B.get(args.model),
        "think": args.think,
        "bertscore": bertscore_status,
        "workers": args.workers,
        "runtime": {
            "ollama_url": args.ollama_url,
            "ollama_num_parallel": os.getenv("OLLAMA_NUM_PARALLEL"),
            "ollama_max_loaded_models": os.getenv("OLLAMA_MAX_LOADED_MODELS"),
            "ollama_keep_alive": os.getenv("OLLAMA_KEEP_ALIVE"),
        },
        "phase_latency_ms": {"total": total_ms},
        "summary": summary,
        "gates": {
            "numeric_f1_min_0_90": summary["numeric_f1"] >= 0.90,
            "evidence_numeric_recall_min_0_90": summary["evidence_numeric_recall"] >= 0.90,
            "source_leakage_rate_eq_0": summary["source_leakage_rate"] == 0.0,
            "error_rate_eq_0": summary["error_rate"] == 0.0,
        },
        "errors": errors,
        "details": {"predictions": details},
    }
    (out_dir / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"metrics": str((out_dir / "metrics.json").relative_to(ROOT)), "model": args.model, "summary": summary, "gates": payload["gates"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
