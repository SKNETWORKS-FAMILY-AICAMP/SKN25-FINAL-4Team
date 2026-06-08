"""
EMS Agent 응답 품질 평가 — ems_eval_evidence_97.json

평가 지표:
  1. Number Hit Rate   — reference_context 핵심 수치가 응답에 포함되는 비율
  2. LLM Grade         — sLLM이 0/1/2 점수로 채점 (0=틀림, 1=부분, 2=정답)
  3. 카테고리별 분류    — anomaly / cms / report

실행:
  # 전체 평가 (LLM 채점 포함)
  python dev/eval/scripts/test_evidence_eval.py

  # 처음 10개만 빠르게 확인 (LLM 채점 없이)
  python dev/eval/scripts/test_evidence_eval.py --limit 10 --no-llm-grade

  # 특정 카테고리만
  python dev/eval/scripts/test_evidence_eval.py --category anomaly

  # 백엔드 URL 변경
  python dev/eval/scripts/test_evidence_eval.py --backend http://localhost:8000

사전 조건:
  백엔드가 실행 중이어야 합니다 (uvicorn 또는 docker compose up)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path

import httpx
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env")

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "ems_eval_evidence_97.json"
RESULT_PATH = Path(__file__).resolve().parents[1] / "data" / "evidence_eval_result.json"

_OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/v1").rstrip("/").removesuffix("/v1")
_LLM_MODEL  = os.getenv("LLM_MODEL", "gemma4:12b")


# ── 수치 추출 ──────────────────────────────────────────────────────

def _extract_numbers(text: str) -> set[str]:
    """소수점 포함 숫자 추출 (예: '372', '63.11', '32')"""
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def number_hit_rate(reference_context: str, agent_response: str) -> float:
    """reference_context value들의 수치 중 응답에 등장하는 비율."""
    ref_nums: set[str] = set()
    for line in reference_context.strip().splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip().lower()
            # source_table, eval_type 등 메타 필드 제외 (created_at 타임스탬프도 제외)
            if key in ("source_table", "eval_type", "question_type", "created_at"):
                continue
            ref_nums.update(_extract_numbers(val))

    if not ref_nums:
        return 1.0

    resp_nums = _extract_numbers(agent_response)
    hit = sum(1 for n in ref_nums if n in resp_nums)
    return hit / len(ref_nums)


# ── 백엔드 호출 ───────────────────────────────────────────────────

def call_chat(backend_url: str, query: str, item_id: str, timeout: int = 120) -> tuple[str, float, str]:
    """POST /chat → (answer, elapsed_ms, intent)"""
    t0 = time.time()
    resp = httpx.post(
        f"{backend_url}/chat",
        json={"question": query, "session_id": f"eval-{item_id}", "is_first": True},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    elapsed = (time.time() - t0) * 1000
    return body.get("answer", ""), elapsed, body.get("intent", "")


# ── LLM 채점 ──────────────────────────────────────────────────────

_GRADE_PROMPT = """아래는 EMS 에이전트 응답 평가입니다.

[질문]
{query}

[정답]
{reference}

[에이전트 응답]
{response}

에이전트 응답이 정답과 얼마나 일치하는지 아래 기준으로 숫자 하나만 답하세요. 다른 말은 하지 마세요.

2 = 핵심 수치와 내용이 일치 (정답). 수치가 정답 대비 1% 이내 오차라면 정답으로 인정.
1 = 일부 수치 누락 또는 1~10% 불일치 (부분 정답)
0 = 수치 오류 10% 초과, 관련 없는 응답, 답변 불가 (틀림)

점수:"""


def llm_grade(query: str, reference: str, response: str) -> int:
    """Ollama sLLM으로 0/1/2 채점."""
    try:
        resp = httpx.post(
            f"{_OLLAMA_URL}/api/chat",
            json={
                "model": _LLM_MODEL,
                "messages": [{"role": "user", "content": _GRADE_PROMPT.format(
                    query=query, reference=reference, response=response
                )}],
                "stream": False,
                "think": False,
                "options": {"num_predict": 5},
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json()["message"]["content"].strip()
        m = re.search(r"[012]", raw)
        return int(m.group()) if m else 0
    except Exception as e:
        print(f"  [LLM grade 오류] {e}")
        return -1  # 채점 실패


# ── 메인 ──────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--backend", default="http://localhost:8000", help="백엔드 URL")
    p.add_argument("--limit", type=int, default=0, help="평가 문항 수 제한 (0=전체)")
    p.add_argument("--category", choices=["anomaly", "cms", "report"], help="특정 카테고리만")
    p.add_argument("--no-llm-grade", action="store_true", help="LLM 채점 생략 (빠른 실행)")
    p.add_argument("--timeout", type=int, default=120, help="에이전트 호출 타임아웃(초)")
    return p.parse_args()


def main():
    args = parse_args()

    all_data: list[dict] = json.load(open(DATA_PATH, encoding="utf-8"))

    if args.category:
        all_data = [d for d in all_data if d["category"] == args.category]
    if args.limit:
        all_data = all_data[: args.limit]

    use_llm = not args.no_llm_grade

    print(f"\n{'='*70}")
    print(f"  EMS Agent 응답 품질 평가")
    print(f"  총 문항: {len(all_data)}개  |  LLM 채점: {'ON' if use_llm else 'OFF'}")
    print(f"  백엔드: {args.backend}  |  sLLM: {_LLM_MODEL}")
    print(f"{'='*70}\n")

    results = []
    by_cat: dict[str, list[dict]] = defaultdict(list)

    for i, item in enumerate(all_data):
        item_id    = item["id"]
        query      = item["query"]
        category   = item["category"]
        difficulty = item["difficulty"]
        ref_ans    = item["reference_answer"]
        ref_ctx    = item["reference_context"]

        print(f"[{i+1:3d}/{len(all_data)}] {item_id}  ({category}/{difficulty})")
        print(f"  질문: {query[:60]}")

        # 1. 에이전트 호출
        try:
            agent_ans, ms, intent = call_chat(args.backend, query, item_id, args.timeout)
        except Exception as e:
            print(f"  [에이전트 오류] {e}\n")
            results.append({**item, "agent_answer": "", "intent": "", "elapsed_ms": 0,
                            "num_hit_rate": 0.0, "llm_score": -1, "error": str(e)})
            continue

        # 2. Number Hit Rate
        nhr = number_hit_rate(ref_ctx, agent_ans)

        # 3. LLM 채점
        score = llm_grade(query, ref_ans, agent_ans) if use_llm else -1

        # 4. 출력
        score_label = {2: "정답", 1: "부분", 0: "틀림", -1: "—"}.get(score, "?")
        print(f"  의도: {intent}  |  {ms:.0f}ms  |  수치히트율: {nhr:.0%}  |  LLM: {score}({score_label})")
        print(f"  정답: {ref_ans[:80]}")
        print(f"  응답: {agent_ans[:80]}")
        print()

        row = {
            **item,
            "agent_answer": agent_ans,
            "intent":       intent,
            "elapsed_ms":   round(ms),
            "num_hit_rate": round(nhr, 4),
            "llm_score":    score,
        }
        results.append(row)
        by_cat[category].append(row)

    # ── 요약 ──────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  최종 요약")
    print(f"{'='*70}")

    total = len(results)
    valid = [r for r in results if "error" not in r]

    avg_nhr   = sum(r["num_hit_rate"] for r in valid) / len(valid) if valid else 0
    graded    = [r for r in valid if r["llm_score"] >= 0]
    avg_score = sum(r["llm_score"] for r in graded) / len(graded) if graded else -1
    correct2  = sum(1 for r in graded if r["llm_score"] == 2)
    partial1  = sum(1 for r in graded if r["llm_score"] == 1)
    wrong0    = sum(1 for r in graded if r["llm_score"] == 0)

    print(f"  전체 문항:    {total}개")
    print(f"  수치 히트율:  {avg_nhr:.1%}  (reference_context 수치 포함 비율)")
    if graded:
        print(f"  LLM 평균점수: {avg_score:.2f} / 2.00")
        print(f"    정답(2): {correct2}건  부분(1): {partial1}건  틀림(0): {wrong0}건")

    print(f"\n  카테고리별:")
    for cat, rows in sorted(by_cat.items()):
        cat_nhr   = sum(r["num_hit_rate"] for r in rows) / len(rows)
        cat_graded = [r for r in rows if r["llm_score"] >= 0]
        cat_score  = sum(r["llm_score"] for r in cat_graded) / len(cat_graded) if cat_graded else -1
        cat_c2    = sum(1 for r in cat_graded if r["llm_score"] == 2)
        score_str = f"LLM {cat_score:.2f} ({cat_c2}/{len(cat_graded)} 정답)" if cat_graded else "LLM —"
        print(f"    {cat:8s}: {len(rows):3d}건  수치히트율 {cat_nhr:.0%}  {score_str}")

    # ── 오답 목록 ─────────────────────────────────────────────────
    wrong = [r for r in results if r.get("llm_score") == 0]
    if wrong:
        print(f"\n  오답 ({len(wrong)}건):")
        for r in wrong:
            print(f"    [{r['id']}] {r['query'][:50]}")
            print(f"      정답: {r['reference_answer'][:60]}")
            print(f"      응답: {r.get('agent_answer','')[:60]}")

    # ── 결과 저장 ─────────────────────────────────────────────────
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total": total,
                "avg_num_hit_rate": round(avg_nhr, 4),
                "avg_llm_score": round(avg_score, 4) if avg_score >= 0 else None,
                "correct2": correct2,
                "partial1": partial1,
                "wrong0":   wrong0,
            },
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  결과 저장: {RESULT_PATH}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
