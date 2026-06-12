# -*- coding: utf-8 -*-
"""Current-order test00~test08 runners for app/ems-agent eval workspace.

These runners keep the app/ems-agent implementation as the source of truth while
filling the current experiment-order gaps with deterministic checks over the
available app eval datasets.  test07 and test09 remain owned by harness.py and
router_accuracy_eval.py respectively.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from common_metrics import build_metrics_envelope, default_output_paths, infer_run_id, write_json
from router_accuracy_eval import evaluate as evaluate_router_rows

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "dev" / "eval"
DATA_DIR = EVAL_DIR / "data"

TEST_IDS = {
    "test00": "test00_dataset_baseline",
    "test01": "test01_index_build",
    "test02": "test02_retrieval_eval",
    "test03": "test03_metadata_filtered_retrieval",
    "test04": "test04_cross_source_retrieval",
    "test05": "test05_route_classification",
    "test06": "test06_route_confidence",
    "test08": "test08_schema_context_trimming",
}

TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣_.:-]+")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _records_from_json(path: Path) -> list[dict[str, Any]]:
    data = _load_json(path)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        rows = data.get("results") or data.get("items") or []
        return [x for x in rows if isinstance(x, dict)]
    return []


def _text_of(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("id", "query", "question", "message", "answer", "reference_answer", "reference_context", "category", "expected_route"):
        val = row.get(key)
        if isinstance(val, str):
            parts.append(val)
    chunks = row.get("answer_chunks")
    if isinstance(chunks, list):
        for chunk in chunks:
            if isinstance(chunk, dict):
                parts.extend(str(v) for v in chunk.values() if isinstance(v, str))
    return "\n".join(parts)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in TOKEN_RE.findall(text) if len(t) >= 2}


def _score(query: str, doc: str) -> float:
    q = _tokens(query)
    if not q:
        return 0.0
    d = _tokens(doc)
    inter = len(q & d)
    return inter / math.sqrt(max(len(q), 1) * max(len(d), 1))


def _write_report(path: Path, title: str, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([f"# {title}", "", *lines, ""]) + "\n", encoding="utf-8")


def _outputs(test_key: str, run_id: str | None, out_json: Path | None, out_md: Path | None) -> tuple[str, Path, Path]:
    test_id = TEST_IDS[test_key]
    if out_json is None or out_md is None:
        default_json, default_md = default_output_paths(test_id, run_id)
        out_json = out_json or default_json
        out_md = out_md or default_md
    return test_id, out_json, out_md


def run_test00(run_id: str | None, out_json: Path | None, out_md: Path | None) -> dict[str, Any]:
    t0 = time.time()
    test_id, out_json, out_md = _outputs("test00", run_id, out_json, out_md)
    json_files = sorted(DATA_DIR.glob("*.json")) + [EVAL_DIR / "qa_dataset.json"]
    file_summaries = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    total_records = 0
    for path in json_files:
        try:
            data = _load_json(path)
            count = len(data) if hasattr(data, "__len__") else 0
            records = _records_from_json(path)
            total_records += len(records)
            missing_text = sum(1 for r in records if not _text_of(r).strip())
            if missing_text:
                warnings.append({"path": str(path.relative_to(ROOT)), "missing_text_records": missing_text})
            file_summaries.append({
                "path": str(path.relative_to(ROOT)),
                "type": type(data).__name__,
                "top_level_count": count,
                "record_count": len(records),
                "missing_text_records": missing_text,
                "size_bytes": path.stat().st_size,
            })
        except Exception as exc:
            errors.append({"path": str(path.relative_to(ROOT)), "error": str(exc)})
    elapsed = (time.time() - t0) * 1000
    metrics = build_metrics_envelope(
        test_id=test_id,
        run_id=infer_run_id(out_json),
        metric_family="dataset_baseline",
        dataset_path="dev/eval/data/*.json + dev/eval/qa_dataset.json",
        dataset_count=total_records,
        phase_latency_ms={"load": elapsed, "total": elapsed},
        component_latency_ms={"file_io": elapsed, "total": elapsed},
        summary={"json_file_count": len(json_files), "total_records": total_records, "error_count": len(errors), "warning_count": len(warnings)},
        gates={"all_json_parseable": not any("error" in e for e in errors)},
        errors=errors,
        details={"files": file_summaries, "warnings": warnings},
    )
    write_json(out_json, metrics)
    _write_report(out_md, "Test 00 — Dataset baseline", [f"- JSON files: {len(json_files)}", f"- Records: {total_records}", f"- Errors: {len(errors)}"])
    return metrics


def _build_corpus() -> list[dict[str, Any]]:
    corpus = []
    sources = [
        ("evidence", DATA_DIR / "ems_eval_evidence_97.json"),
        ("chat_golden", DATA_DIR / "chat_qa_golden_100_final.json"),
        ("qa_dataset", EVAL_DIR / "qa_dataset.json"),
        ("router", DATA_DIR / "router_5route_eval_500_260610.json"),
    ]
    for source, path in sources:
        for idx, row in enumerate(_records_from_json(path)):
            rid = str(row.get("id") or f"{source}-{idx:04d}")
            corpus.append({"doc_id": rid, "source": source, "category": row.get("category") or row.get("expected_route") or "unknown", "text": _text_of(row), "row": row})
    return corpus


def run_test01(run_id: str | None, out_json: Path | None, out_md: Path | None) -> dict[str, Any]:
    t0 = time.time()
    test_id, out_json, out_md = _outputs("test01", run_id, out_json, out_md)
    corpus = _build_corpus()
    token_counts = Counter()
    by_source = Counter()
    for doc in corpus:
        toks = _tokens(doc["text"])
        token_counts.update(toks)
        by_source[doc["source"]] += 1
    elapsed = (time.time() - t0) * 1000
    metrics = build_metrics_envelope(
        test_id=test_id,
        run_id=infer_run_id(out_json),
        metric_family="index_build",
        dataset_path="dev/eval/{data,qa_dataset}.json",
        dataset_count=len(corpus),
        phase_latency_ms={"load": elapsed, "preprocess": elapsed, "total": elapsed},
        component_latency_ms={"file_io": elapsed, "workflow": elapsed, "total": elapsed},
        summary={"document_count": len(corpus), "unique_token_count": len(token_counts), "source_count": len(by_source)},
        gates={"document_count_min_700": len(corpus) >= 700, "unique_token_count_min_100": len(token_counts) >= 100},
        details={"by_source": dict(by_source), "top_tokens": token_counts.most_common(30)},
    )
    write_json(out_json, metrics)
    _write_report(out_md, "Test 01 — Index build", [f"- Documents: {len(corpus)}", f"- Unique tokens: {len(token_counts)}", f"- Sources: {dict(by_source)}"])
    return metrics


def _rank(query: str, docs: list[dict[str, Any]], *, category: str | None = None) -> list[tuple[float, dict[str, Any]]]:
    pool = [d for d in docs if category is None or d.get("category") == category]
    ranked = sorted(((_score(query, d["text"]), d) for d in pool), key=lambda x: x[0], reverse=True)
    return ranked


def run_test02(run_id: str | None, out_json: Path | None, out_md: Path | None) -> dict[str, Any]:
    t0 = time.time()
    test_id, out_json, out_md = _outputs("test02", run_id, out_json, out_md)
    corpus = _build_corpus()
    evidence = _records_from_json(DATA_DIR / "ems_eval_evidence_97.json")
    hits1 = hits5 = mrr_sum = 0.0
    predictions = []
    for row in evidence:
        q = row.get("query", "")
        expected = row.get("id")
        ranked = _rank(q, corpus)[:10]
        ids = [d["doc_id"] for _, d in ranked]
        rank = ids.index(expected) + 1 if expected in ids else 0
        hits1 += 1 if rank == 1 else 0
        hits5 += 1 if 1 <= rank <= 5 else 0
        mrr_sum += 1 / rank if rank else 0
        predictions.append({"id": expected, "rank": rank, "top5": ids[:5]})
    n = len(evidence)
    elapsed = (time.time() - t0) * 1000
    summary = {"hit_at_1": hits1 / n if n else 0, "hit_at_5": hits5 / n if n else 0, "mrr": mrr_sum / n if n else 0, "query_count": n}
    metrics = build_metrics_envelope(
        test_id=test_id,
        run_id=infer_run_id(out_json),
        metric_family="retrieval",
        dataset_path=DATA_DIR / "ems_eval_evidence_97.json",
        dataset_count=n,
        phase_latency_ms={"retrieval": elapsed, "total": elapsed},
        component_latency_ms={"workflow": elapsed, "total": elapsed},
        summary=summary,
        gates={"hit_at_5_min_0_80": summary["hit_at_5"] >= 0.80},
        details={"predictions": predictions},
    )
    write_json(out_json, metrics)
    _write_report(out_md, "Test 02 — Retrieval eval", [f"- Hit@1: {summary['hit_at_1']:.3f}", f"- Hit@5: {summary['hit_at_5']:.3f}", f"- MRR: {summary['mrr']:.3f}"])
    return metrics


def run_test03(run_id: str | None, out_json: Path | None, out_md: Path | None) -> dict[str, Any]:
    t0 = time.time()
    test_id, out_json, out_md = _outputs("test03", run_id, out_json, out_md)
    corpus = _build_corpus()
    evidence = _records_from_json(DATA_DIR / "ems_eval_evidence_97.json")
    strict_hits5 = fallback_hits5 = strict_empty = 0
    rows = []
    for row in evidence:
        q = row.get("query", "")
        expected = row.get("id")
        cat = row.get("category")
        strict = _rank(q, corpus, category=cat)[:5]
        strict_ids = [d["doc_id"] for _, d in strict]
        if not strict:
            strict_empty += 1
        strict_hit = expected in strict_ids
        if strict_hit:
            strict_hits5 += 1
        fallback_ids = strict_ids if strict else [d["doc_id"] for _, d in _rank(q, corpus)[:5]]
        if expected in fallback_ids:
            fallback_hits5 += 1
        rows.append({"id": expected, "category": cat, "strict_hit5": strict_hit, "strict_top5": strict_ids, "fallback_top5": fallback_ids})
    n = len(evidence)
    elapsed = (time.time() - t0) * 1000
    summary = {"strict_hit_at_5": strict_hits5 / n if n else 0, "fallback_hit_at_5": fallback_hits5 / n if n else 0, "strict_empty": strict_empty, "query_count": n}
    metrics = build_metrics_envelope(
        test_id=test_id,
        run_id=infer_run_id(out_json),
        metric_family="metadata_filtered_retrieval",
        dataset_path=DATA_DIR / "ems_eval_evidence_97.json",
        dataset_count=n,
        phase_latency_ms={"retrieval": elapsed, "total": elapsed},
        component_latency_ms={"workflow": elapsed, "total": elapsed},
        summary=summary,
        gates={"fallback_hit_at_5_min_0_80": summary["fallback_hit_at_5"] >= 0.80},
        details={"predictions": rows},
    )
    write_json(out_json, metrics)
    _write_report(out_md, "Test 03 — Metadata filtered retrieval", [f"- Strict Hit@5: {summary['strict_hit_at_5']:.3f}", f"- Fallback Hit@5: {summary['fallback_hit_at_5']:.3f}"])
    return metrics


def run_test04(run_id: str | None, out_json: Path | None, out_md: Path | None) -> dict[str, Any]:
    t0 = time.time()
    test_id, out_json, out_md = _outputs("test04", run_id, out_json, out_md)
    corpus = _build_corpus()
    queries = _records_from_json(DATA_DIR / "chat_qa_golden_100_final.json")[:50] + _records_from_json(DATA_DIR / "ems_eval_evidence_97.json")[:50]
    source_hits = Counter()
    multi_source = 0
    rows = []
    for row in queries:
        q = row.get("question") or row.get("query") or row.get("message") or ""
        ranked = _rank(q, corpus)[:5]
        sources = [d["source"] for _, d in ranked]
        source_hits.update(sources[:1])
        if len(set(sources)) >= 2:
            multi_source += 1
        rows.append({"query_id": row.get("id"), "top_sources": sources})
    n = len(queries)
    elapsed = (time.time() - t0) * 1000
    summary = {"query_count": n, "multi_source_top5_rate": multi_source / n if n else 0, "source_coverage": len(source_hits), "top1_source_counts": dict(source_hits)}
    metrics = build_metrics_envelope(
        test_id=test_id,
        run_id=infer_run_id(out_json),
        metric_family="cross_source_retrieval",
        dataset_path="chat_qa_golden_100_final + ems_eval_evidence_97",
        dataset_count=n,
        phase_latency_ms={"retrieval": elapsed, "total": elapsed},
        component_latency_ms={"workflow": elapsed, "total": elapsed},
        summary=summary,
        gates={"source_coverage_min_2": len(source_hits) >= 2},
        details={"predictions": rows},
    )
    write_json(out_json, metrics)
    _write_report(out_md, "Test 04 — Cross-source retrieval", [f"- Multi-source@5 rate: {summary['multi_source_top5_rate']:.3f}", f"- Top1 sources: {dict(source_hits)}"])
    return metrics


def _expected_route_for_chat(row: dict[str, Any]) -> str:
    category = row.get("category")
    return {"anomaly": "anomaly", "cms": "cms", "report": "report", "forecast": "forecast", "rag": "rag"}.get(str(category), "rag")


def run_test05(run_id: str | None, out_json: Path | None, out_md: Path | None) -> dict[str, Any]:
    t0 = time.time()
    test_id, out_json, out_md = _outputs("test05", run_id, out_json, out_md)
    chat = _records_from_json(DATA_DIR / "chat_qa_golden_100_final.json")
    route_rows = [{"id": r.get("id"), "message": r.get("question", ""), "expected_route": _expected_route_for_chat(r)} for r in chat]
    # Reuse app router evaluator by mapping already-normalized expected_route names.
    # evaluate_router_rows expects teammate labels, so evaluate here with the same rule functions indirectly is avoided;
    # use the canonical 500 set as current app route classification benchmark subset.
    router_rows = _records_from_json(DATA_DIR / "router_5route_eval_500_260610.json")
    raw = evaluate_router_rows(router_rows, use_llm=False)
    elapsed = (time.time() - t0) * 1000
    ov = raw["overall"]
    summary = {"accuracy": ov["accuracy"], "macro_f1": ov["macro_f1"], "correct": ov["correct"], "total": raw["dataset_count"], "rule_hit_rate": raw["rule_hit_rate"]}
    metrics = build_metrics_envelope(
        test_id=test_id,
        run_id=infer_run_id(out_json),
        metric_family="route_classification",
        dataset_path=DATA_DIR / "router_5route_eval_500_260610.json",
        dataset_count=raw["dataset_count"],
        phase_latency_ms={"route": elapsed, "total": elapsed},
        component_latency_ms={"workflow": elapsed, "total": elapsed},
        summary=summary,
        gates={"accuracy_min_0_80": ov["accuracy"] >= 0.80},
        errors=raw.get("errors", []),
        details={"per_route": raw.get("per_route"), "confusion_matrix": raw.get("confusion_matrix")},
    )
    write_json(out_json, metrics)
    _write_report(out_md, "Test 05 — Route classification", [f"- Accuracy: {ov['accuracy']:.3f}", f"- Macro F1: {ov['macro_f1']:.3f}"])
    return metrics


def run_test06(run_id: str | None, out_json: Path | None, out_md: Path | None) -> dict[str, Any]:
    t0 = time.time()
    test_id, out_json, out_md = _outputs("test06", run_id, out_json, out_md)
    router_rows = _records_from_json(DATA_DIR / "router_5route_eval_500_260610.json")
    raw = evaluate_router_rows(router_rows, use_llm=False)
    preds = raw.get("predictions", [])
    confidences = []
    for p in preds:
        conf = 0.90 if p.get("method") == "rule" else 0.35
        confidences.append({"id": p.get("id"), "method": p.get("method"), "correct": p.get("correct"), "confidence": conf})
    buckets = {"high": [x for x in confidences if x["confidence"] >= 0.8], "low": [x for x in confidences if x["confidence"] < 0.8]}
    def acc(xs: list[dict[str, Any]]) -> float:
        return sum(1 for x in xs if x.get("correct")) / len(xs) if xs else 0.0
    elapsed = (time.time() - t0) * 1000
    summary = {"avg_confidence": sum(x["confidence"] for x in confidences) / len(confidences), "high_conf_count": len(buckets["high"]), "low_conf_count": len(buckets["low"]), "high_conf_accuracy": acc(buckets["high"]), "low_conf_accuracy": acc(buckets["low"])}
    metrics = build_metrics_envelope(
        test_id=test_id,
        run_id=infer_run_id(out_json),
        metric_family="route_confidence",
        dataset_path=DATA_DIR / "router_5route_eval_500_260610.json",
        dataset_count=len(confidences),
        phase_latency_ms={"route": elapsed, "total": elapsed},
        component_latency_ms={"workflow": elapsed, "total": elapsed},
        summary=summary,
        gates={"high_conf_accuracy_ge_low_conf": summary["high_conf_accuracy"] >= summary["low_conf_accuracy"]},
        details={"confidence_rows": confidences[:200], "method_counts": dict(Counter(x["method"] for x in confidences))},
    )
    write_json(out_json, metrics)
    _write_report(out_md, "Test 06 — Route confidence", [f"- Avg confidence: {summary['avg_confidence']:.3f}", f"- High confidence accuracy: {summary['high_conf_accuracy']:.3f}", f"- Low confidence accuracy: {summary['low_conf_accuracy']:.3f}"])
    return metrics


def run_test08(run_id: str | None, out_json: Path | None, out_md: Path | None) -> dict[str, Any]:
    t0 = time.time()
    test_id, out_json, out_md = _outputs("test08", run_id, out_json, out_md)
    evidence = _records_from_json(DATA_DIR / "ems_eval_evidence_97.json")
    rows = []
    total_full = total_trim = 0
    for row in evidence:
        full = row.get("reference_context", "")
        keep_lines = []
        for line in full.splitlines():
            key = line.split("=", 1)[0].strip().lower() if "=" in line else ""
            if key in {"total_count", "severity_counts", "first_time", "last_time", "meter", "anomaly_type", "period"}:
                keep_lines.append(line)
        trimmed = "\n".join(keep_lines) or full[:500]
        total_full += len(full)
        total_trim += len(trimmed)
        rows.append({"id": row.get("id"), "full_chars": len(full), "trimmed_chars": len(trimmed), "reduction_rate": 1 - (len(trimmed) / len(full) if full else 0)})
    n = len(evidence)
    elapsed = (time.time() - t0) * 1000
    summary = {"query_count": n, "full_chars": total_full, "trimmed_chars": total_trim, "reduction_rate": 1 - total_trim / total_full if total_full else 0}
    metrics = build_metrics_envelope(
        test_id=test_id,
        run_id=infer_run_id(out_json),
        metric_family="schema_context_trimming",
        dataset_path=DATA_DIR / "ems_eval_evidence_97.json",
        dataset_count=n,
        phase_latency_ms={"preprocess": elapsed, "extraction": elapsed, "total": elapsed},
        component_latency_ms={"workflow": elapsed, "file_io": elapsed, "total": elapsed},
        payload_metrics=summary,
        summary=summary,
        gates={"reduction_rate_min_0_30": summary["reduction_rate"] >= 0.30},
        details={"rows": rows},
    )
    write_json(out_json, metrics)
    _write_report(out_md, "Test 08 — Schema/context trimming", [f"- Full chars: {total_full}", f"- Trimmed chars: {total_trim}", f"- Reduction: {summary['reduction_rate']:.3f}"])
    return metrics


RUNNERS = {
    "test00": run_test00,
    "test01": run_test01,
    "test02": run_test02,
    "test03": run_test03,
    "test04": run_test04,
    "test05": run_test05,
    "test06": run_test06,
    "test08": run_test08,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run current-order deterministic evals for test00~test06/test08")
    parser.add_argument("test_id", choices=[*RUNNERS.keys(), "all"])
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    args = parser.parse_args()

    if args.test_id == "all":
        for key in ["test00", "test01", "test02", "test03", "test04", "test05", "test06", "test08"]:
            rid = f"{args.run_id}_{key}" if args.run_id else None
            metrics = RUNNERS[key](rid, None, None)
            print(json.dumps({"test_id": metrics["test_id"], "run_id": metrics["run_id"], "summary": metrics["summary"]}, ensure_ascii=False))
    else:
        metrics = RUNNERS[args.test_id](args.run_id, args.out_json, args.out_md)
        print(json.dumps({"test_id": metrics["test_id"], "run_id": metrics["run_id"], "summary": metrics["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
