"""
시스템 레벨 end-to-end 평가 — eval/qa_dataset.json 기반

harness.py와의 차이:
  harness.py   — LLM을 직접 호출 (프롬프트 품질 테스트)
  system_eval  — 실제 백엔드 API(/chat)를 호출 (전체 파이프라인 테스트)
                 orchestrator → agent → DB 조회 → LLM → 응답

실행:
  python eval/system_eval.py                  # 전체 100개
  python eval/system_eval.py --n 20           # 랜덤 20개
  python eval/system_eval.py --cat anomaly    # 특정 카테고리만
  python eval/system_eval.py --diff hard      # 특정 난이도만

결과:
  eval/results/syseval_YYYYMMDD_HHMMSS.json
  카테고리·난이도별 점수 요약 출력
"""

import argparse
import json
import os
import random
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from openai import OpenAI

from common_metrics import build_metrics_envelope, default_output_paths, infer_run_id, write_json

load_dotenv(Path(__file__).parent.parent / ".env")

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
JUDGE_MODEL  = "gpt-4o"
DATASET_PATH = Path(__file__).parent / "qa_dataset.json"
TEST_ID = "test07_llm_answer_gate"
METRIC_FAMILY = "system_answer_quality"

JUDGE_SYSTEM = """당신은 CMS 에너지 관리 시스템 응답 품질 평가 전문가입니다.
[정답]과 [증거 데이터]를 기준으로 [시스템 응답]을 3개 축으로 1~10점 채점하고 JSON만 출력하세요.

채점 기준:
- correctness : 핵심 사실·수치가 정답과 일치하는가
    10=모든 핵심 사실 정확 일치
    7~9=대부분 맞으나 경미한 오류·누락
    4~6=일부 사실 불일치 또는 중요 정보 누락
    1~3=핵심 사실 오류 또는 완전히 다른 답변
- completeness: 질문의 모든 요소에 답했는가 (분석+원인+조치 등)
    10=모든 요소 충실히 다룸
    7~9=대부분 다뤘으나 일부 누락
    4~6=절반 이하 다룸
    1~3=질문을 제대로 이해하지 못한 답변
- grounding  : 증거 데이터(시각·수치·유형)를 실제로 인용했는가
    10=증거 데이터 정확히 인용
    7~9=대부분 인용, 일부 누락
    4~6=일부만 인용
    1~3=증거 무시·일반론·할루시네이션
    증거 없는 질문(rag)=10점 부여
- comment    : 가장 큰 문제점 또는 칭찬 한 줄 (한국어, 40자 이내)

출력 형식 (JSON만):
{"correctness": X, "completeness": X, "grounding": X, "comment": "..."}"""


def ask_backend(question: str, timeout: int = 90) -> tuple[str, float]:
    t0 = time.time()
    try:
        resp = requests.post(
            f"{BACKEND_URL}/chat",
            json={"question": question, "history": []},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        answer = data.get("answer") or data.get("response") or str(data)
        return answer, round(time.time() - t0, 2)
    except Exception as e:
        return f"[백엔드 오류] {e}", round(time.time() - t0, 2)


def judge(client: OpenAI, item: dict, response: str) -> dict:
    evidence_str = "\n".join(item.get("evidence", [])) or "없음"
    user_msg = (
        f"[질문]\n{item['question']}\n\n"
        f"[정답]\n{item['answer'][:800]}\n\n"
        f"[증거 데이터]\n{evidence_str[:400]}\n\n"
        f"[시스템 응답]\n{response[:1000]}"
    )
    try:
        resp = client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            max_tokens=150,
        )
        raw = resp.choices[0].message.content or ""
        start, end = raw.find("{"), raw.rfind("}") + 1
        return json.loads(raw[start:end])
    except Exception as e:
        return {"correctness": 0, "completeness": 0, "grounding": 0, "comment": f"판정 실패: {e}"}


def _avg(r: dict) -> float:
    return round((r["correctness"] + r["completeness"] + r["grounding"]) / 3, 1)


def run(items: list[dict], out_json: Path | None = None, out_md: Path | None = None, run_id: str | None = None, dataset_path: Path = DATASET_PATH):
    judge_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    results = []

    print(f"\n{'='*70}")
    print(f"  시스템 평가  |  백엔드: {BACKEND_URL}  |  문항: {len(items)}개")
    print(f"  심사위원: {JUDGE_MODEL}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    for i, item in enumerate(items, 1):
        cat   = item.get("category", "?")
        diff  = item.get("difficulty", "?")
        q_short = item["question"][:40] + "..." if len(item["question"]) > 40 else item["question"]
        print(f"[{i:>3}/{len(items)}] [{cat}/{diff}] {q_short}", end=" ... ", flush=True)

        response, elapsed = ask_backend(item["question"])
        scores = judge(judge_client, item, response)
        avg = _avg(scores)

        results.append({
            "id":           i,
            "category":     cat,
            "difficulty":   diff,
            "question":     item["question"],
            "gold_answer":  item["answer"],
            "evidence":     item.get("evidence", []),
            "response":     response,
            "elapsed_s":    elapsed,
            **scores,
            "avg":          avg,
        })
        print(f"완료 ({elapsed}s) — {avg}/10  💬 {scores['comment']}")

    # ── 결과 요약 ─────────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"  전체 평균: {sum(r['avg'] for r in results)/len(results):.1f}/10  "
          f"({len(results)}개, 응답시간 평균 {sum(r['elapsed_s'] for r in results)/len(results):.1f}s)")

    print(f"\n{'─'*70}")
    print("  카테고리별:")
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r["avg"])
    for cat in sorted(by_cat):
        scores_list = by_cat[cat]
        print(f"    {cat:<12}: {sum(scores_list)/len(scores_list):.1f}/10  ({len(scores_list)}개)")

    print(f"\n{'─'*70}")
    print("  난이도별:")
    by_diff = defaultdict(list)
    for r in results:
        by_diff[r["difficulty"]].append(r["avg"])
    for diff in ["easy", "medium", "hard"]:
        if diff in by_diff:
            scores_list = by_diff[diff]
            print(f"    {diff:<8}: {sum(scores_list)/len(scores_list):.1f}/10  ({len(scores_list)}개)")

    print(f"\n{'─'*70}")
    weak = sorted(results, key=lambda r: r["avg"])[:5]
    print("  하위 5개:")
    for r in weak:
        print(f"    [{r['avg']}/10] [{r['category']}/{r['difficulty']}] {r['question'][:45]}...")
        print(f"          💬 {r['comment']}")

    # ── 공통 metrics schema 저장 ───────────────────────────────────────
    if out_json is None or out_md is None:
        default_json, default_md = default_output_paths(TEST_ID, run_id, suffix="system")
        out_json = out_json or default_json
        out_md = out_md or default_md

    total_score = round(sum(r["avg"] for r in results) / len(results), 1)
    avg_elapsed_s = round(sum(r["elapsed_s"] for r in results) / len(results), 2)
    total_elapsed_ms = round(sum(float(r["elapsed_s"]) for r in results) * 1000, 2)
    by_cat_summary = {k: round(sum(v) / len(v), 1) for k, v in by_cat.items()}
    by_diff_summary = {k: round(sum(v) / len(v), 1) for k, v in by_diff.items()}
    metrics = build_metrics_envelope(
        test_id=TEST_ID,
        run_id=infer_run_id(out_json),
        metric_family=METRIC_FAMILY,
        dataset_path=dataset_path,
        dataset_count=len(results),
        phase_latency_ms={"answer": total_elapsed_ms, "qa": total_elapsed_ms, "total": total_elapsed_ms},
        component_latency_ms={"api": total_elapsed_ms, "llm": total_elapsed_ms, "total": total_elapsed_ms},
        summary={
            "backend": BACKEND_URL,
            "judge": JUDGE_MODEL,
            "total": total_score,
            "avg_elapsed_s": avg_elapsed_s,
            "by_category": by_cat_summary,
            "by_difficulty": by_diff_summary,
        },
        gates={"total_score_min_8_0": total_score >= 8.0},
        errors=[r for r in results if r["avg"] <= 0],
        details={"results": results},
    )
    write_json(out_json, metrics)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        "\n".join([
            "# Test 07 — System answer quality",
            "",
            f"> backend: `{BACKEND_URL}` | judge: `{JUDGE_MODEL}` | n: {len(results)}",
            "",
            "## Summary",
            "",
            "| metric | value |",
            "|---|---:|",
            f"| total | {total_score} |",
            f"| avg_elapsed_s | {avg_elapsed_s} |",
            "",
            "## By category",
            "",
            *[f"- `{k}`: {v}" for k, v in sorted(by_cat_summary.items())],
            "",
            "## By difficulty",
            "",
            *[f"- `{k}`: {v}" for k, v in sorted(by_diff_summary.items())],
            "",
        ]) + "\n",
        encoding="utf-8",
    )
    print(f"\n💾 metrics 저장: {out_json}")
    print(f"💾 report 저장: {out_md}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n",    type=int, default=0,  help="랜덤 샘플 수 (0=전체)")
    parser.add_argument("--cat",  type=str, default="", help="카테고리 필터 (anomaly/cms/report/rag/forecast)")
    parser.add_argument("--diff", type=str, default="", help="난이도 필터 (easy/medium/hard)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-json", type=Path, default=None,
                        help="공통 metrics.json 출력 경로. 기본값: reports/experiments/test07_llm_answer_gate/run_*/metrics.json")
    parser.add_argument("--out-md", type=Path, default=None,
                        help="Markdown report 출력 경로. 기본값: metrics.json과 같은 run 폴더/report.md")
    parser.add_argument("--run-id", type=str, default=None)
    args = parser.parse_args()

    items = json.loads(DATASET_PATH.read_text())

    if args.cat:
        items = [x for x in items if x.get("category") == args.cat]
    if args.diff:
        items = [x for x in items if x.get("difficulty") == args.diff]
    if args.n and args.n < len(items):
        random.seed(args.seed)
        items = random.sample(items, args.n)

    if not items:
        print("조건에 맞는 항목이 없습니다.")
        return

    run(items, out_json=args.out_json, out_md=args.out_md, run_id=args.run_id, dataset_path=DATASET_PATH)


if __name__ == "__main__":
    main()
