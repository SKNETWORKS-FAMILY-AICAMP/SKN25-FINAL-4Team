"""
교차 채점 스크립트.
exaone 응답을 gemma4로 재채점 → 자기 채점 편향 제거.

실행:
  backend/.venv/bin/python dev/eval/scripts/cross_grade.py
"""
from __future__ import annotations

import json, re, os, time
from pathlib import Path
import httpx
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env")

OLLAMA_BASE = os.getenv("OLLAMA_URL","http://localhost:11434/v1").rstrip("/").removesuffix("/v1")
GRADER_MODEL = "gemma4:12b"   # 독립 채점자

EXAONE_RESULT = Path(__file__).resolve().parents[1] / "data" / "evidence_eval_exaone.json"
OUT_PATH      = Path(__file__).resolve().parents[1] / "data" / "evidence_eval_exaone_crossgraded.json"

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


def grade(query, reference, response):
    resp = httpx.post(
        f"{OLLAMA_BASE}/api/chat",
        json={
            "model": GRADER_MODEL,
            "messages": [{"role":"user","content":_GRADE_PROMPT.format(
                query=query, reference=reference, response=response)}],
            "stream": False, "think": False,
            "options": {"num_predict": 5},
        }, timeout=60,
    )
    raw = resp.json()["message"]["content"].strip()
    m = re.search(r"[012]", raw)
    return int(m.group()) if m else 0


data = json.load(open(EXAONE_RESULT, encoding="utf-8"))
results = data["results"]
print(f"교차 채점 시작: {len(results)}개 항목, 채점자: {GRADER_MODEL}")
print("="*65)

from collections import defaultdict
by_cat = defaultdict(list)

for i, r in enumerate(results):
    if "error" in r or not r.get("agent_answer"):
        r["cross_grade"] = -1
        continue
    score = grade(r["query"], r["reference_answer"], r["agent_answer"])
    r["cross_grade"] = score
    by_cat[r["category"]].append(score)
    label = {2:"정답",1:"부분",0:"틀림"}.get(score,"?")
    print(f"[{i+1:3d}/{len(results)}] {r['id']} → {score}({label})")

print("="*65)
valid = [r for r in results if r.get("cross_grade", -1) >= 0]
graded = [r for r in valid if r["cross_grade"] >= 0]
avg = sum(r["cross_grade"] for r in graded)/len(graded) if graded else 0
c2 = sum(1 for r in graded if r["cross_grade"]==2)
c1 = sum(1 for r in graded if r["cross_grade"]==1)
c0 = sum(1 for r in graded if r["cross_grade"]==0)

print(f"\n[교차 채점 결과] 채점자: {GRADER_MODEL} → EXAONE 응답")
print(f"  평균점수: {avg:.2f}/2.00  |  정답(2): {c2}  부분(1): {c1}  틀림(0): {c0}")
print("\n카테고리별:")
for cat, scores in sorted(by_cat.items()):
    a = sum(scores)/len(scores)
    c = sum(1 for s in scores if s==2)
    print(f"  {cat:8s}: {len(scores)}건  평균 {a:.2f}  정답 {c}/{len(scores)}")

data["cross_grade_summary"] = {
    "grader": GRADER_MODEL,
    "avg": round(avg,3), "correct2": c2, "partial1": c1, "wrong0": c0
}
json.dump(data, open(OUT_PATH,"w",encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"\n저장: {OUT_PATH}")
