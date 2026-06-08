"""
두 sLLM 모델 성능 비교 평가.

비교 대상:
  - gemma4:12b   (Google, 기본 모델)
  - exaone3.5:7.8b (LG AI Research, 한국어 특화)

평가 항목:
  1. 의도 분류 정확도  — chat_qa_golden_100_final.json (100개)
     - rule 분류 vs LLM 분류 구분
  2. 응답 품질 (생성)  — 4개 도메인별 케이스
  3. 한국어 능력 체크  — 3개 케이스
  4. 레이턴시 비교

실행:
  cd SKN25-FINAL-4Team
  backend/.venv/bin/python dev/eval/scripts/test_model_compare.py
"""
from __future__ import annotations

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

OLLAMA_BASE = os.getenv("OLLAMA_URL", "http://localhost:11434/v1").rstrip("/").removesuffix("/v1")
RESULT_PATH = Path(__file__).resolve().parents[1] / "data" / "model_compare_result.json"
DATA_PATH   = Path(__file__).resolve().parents[1] / "data" / "chat_qa_golden_100_final.json"

MODELS = [
    {"name": "gemma4:12b",      "label": "Gemma4 12B",        "org": "Google"},
    {"name": "exaone3.5:7.8b",  "label": "EXAONE 3.5 7.8B",  "org": "LG AI Research"},
]

# ── Ollama 직접 호출 ──────────────────────────────────────────────────

def _call_ollama(model: str, messages: list[dict], max_tokens: int = 10, timeout: int = 60) -> tuple[str, float]:
    t0 = time.perf_counter()
    resp = httpx.post(
        f"{OLLAMA_BASE}/api/chat",
        json={
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"num_predict": max_tokens},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return resp.json()["message"]["content"].strip(), elapsed_ms


# ── 의도 분류 ─────────────────────────────────────────────────────────

_KW_ANOMALY  = re.compile(r"이상\s*탐지|이상\s*발생|이상\s*이력|이상\s*건수|이상\s*원인"
                          r"|비정상|스파이크|급등|급락|오류|탐지|경보|알람|fault|anomal"
                          r"|chpoutage|powerspike|copdrop|nightconsumption|pvnightnonzero"
                          r"|사건|빈도|심각도|발생\s*건수|몇\s*건|잔차|급등\s*이벤트|게이트웨이\s*장애")
_KW_REPORT   = re.compile(r"보고서|리포트|report|kpi|월간|요약|통계|실적|집계|월별\s*현황"
                          r"|요금|비용|cost|전력\s*비용|얼마나\s*나"
                          r"|의존도|자급률|출력\s*얼마|사용량\s*어때|사용량\s*얼마"
                          r"|그리드\s*의존|외부\s*전력\s*의존|계통\s*의존")
_KW_FORECAST = re.compile(r"예측|전망|앞으로|내일|다음\s*주|장기|예상|forecast|미래|될\s*것"
                          r"|계속\s*될까|계속될까|낮아질까|높아질까|늘어날까|줄어들까"
                          r"|떨어질까|올라갈까|늘\s*까|줄\s*까|추세|앞으로.*될")
_KW_CMS      = re.compile(r"작업\s*지시|정비|수리|예지보전|상태\s*감시|헬스|진단"
                          r"|설비\s*상태|설비\s*이상|설비\s*점검|설비\s*확인|설비\s*문제"
                          r"|열병합|시뮬|시뮬레이터|시연")
_KW_FUTURE   = re.compile(r"계속\s*될까|계속될까|낮아질까|높아질까|늘어날까|줄어들까"
                          r"|떨어질까|올라갈까|계속\s*낮아|계속\s*떨어|계속\s*높아"
                          r"|앞으로.*될|~할\s*것|추세.*앞|앞.*추세")
_METER_URN   = re.compile(r"[A-Z]\d?\.(?:[A-Z]\.)?Z\d+", re.IGNORECASE)

INTENT_PROMPT = """사용자 질문을 읽고 아래 중 하나로만 답하세요. 다른 말은 하지 마세요.

- anomaly  : 이상탐지 결과·건수·원인 분석. 이상 유형명(CHPOutage/PowerSpike/COPDrop/NightConsumption/PVNightNonZero)이 나오면 무조건 anomaly.
- cms      : 설비 상태 점검, 작업지시, 정비, 예지보전.
- report   : 실적·현황 조회. 보고서, KPI, 월간, 요약, 통계, 자급률, 의존도, 사용량, 비용.
- forecast : 미래 예측·전망. "앞으로", "내일", "다음 주", "~될까?", "~낮아질까?", 추세.
- rag      : 개념 설명, 계량기 값 의미, 실시간 센서값 조회, 그 외.

핵심 구분:
- "이상 있어?" → anomaly (과거/현재 탐지)
- "이상 계속될까?" → forecast (미래)
- "설비 상태 어때?" → cms
- "자급률 왜 떨어졌어?" → report

질문: {question}"""


def _rule_classify(question: str) -> str | None:
    if _METER_URN.search(question):
        return "rag"
    q = question.lower()
    if _KW_FUTURE.search(q):
        return "forecast"
    scores = {
        "anomaly":  len(_KW_ANOMALY.findall(q)),
        "report":   len(_KW_REPORT.findall(q)),
        "forecast": len(_KW_FORECAST.findall(q)),
        "cms":      len(_KW_CMS.findall(q)),
    }
    best, count = max(scores.items(), key=lambda x: x[1])
    if count >= 1:
        second = sorted(scores.values(), reverse=True)[1]
        if count > second:
            return best
    return None


VALID_INTENTS = ("anomaly", "report", "rag", "forecast", "cms")


# ── 1. 의도 분류 테스트 ───────────────────────────────────────────────

def run_intent_classification(model_name: str) -> dict:
    data = json.load(open(DATA_PATH, encoding="utf-8"))
    correct = wrong = rule_correct = rule_total = llm_correct = llm_total = 0
    total_ms = 0.0
    llm_only_ms = 0.0
    errors = []

    for item in data:
        q, true = item["question"], item["category"]
        rule = _rule_classify(q)
        ms = 0.0

        if rule:
            pred, method = rule, "rule"
            rule_total += 1
            if pred == true:
                rule_correct += 1
        else:
            try:
                raw, ms = _call_ollama(
                    model_name,
                    [{"role": "user", "content": INTENT_PROMPT.format(question=q)}],
                    max_tokens=10,
                )
                raw = raw.strip().lower()
                pred = raw if raw in VALID_INTENTS else "rag"
                method = "llm"
                llm_total += 1
                llm_only_ms += ms
                if pred == true:
                    llm_correct += 1
            except Exception as e:
                pred, method = "rag", "error"
                errors.append(str(e))

        total_ms += ms
        ok = pred == true
        if ok:
            correct += 1
        else:
            errors.append(f"[{item.get('id','?')}] q={q[:40]} true={true} pred={pred} ({method})")

    total = len(data)
    return {
        "accuracy":       round(correct / total * 100, 1),
        "correct":        correct,
        "total":          total,
        "rule_accuracy":  round(rule_correct / rule_total * 100, 1) if rule_total else None,
        "rule_total":     rule_total,
        "llm_accuracy":   round(llm_correct / llm_total * 100, 1) if llm_total else None,
        "llm_total":      llm_total,
        "avg_llm_ms":     round(llm_only_ms / llm_total) if llm_total else 0,
        "wrong_examples": errors[:5],
    }


# ── 2. 응답 품질 테스트 ───────────────────────────────────────────────

GEN_CASES = [
    ("rag",      "역률(PF)이 낮으면 에너지 관리 관점에서 어떤 문제가 생기나요?",
     "당신은 에너지 관리 시스템(EMS) 전문 AI입니다.\n도메인 지식을 바탕으로 아래 질문에 한국어로 간결하게 답하세요 (3~5문장).\n질문: {q}"),
    ("anomaly",  "COP Drop 이상 발생 시 주요 원인과 대응 방법을 설명해주세요.",
     "당신은 에너지 설비 이상탐지 전문 AI입니다.\n이상 유형: COP Drop (냉방 성능 저하)\n아래 질문에 한국어로 간결하게 답하세요 (3~5문장).\n질문: {q}"),
    ("forecast", "전력 소비 예측 모델에서 LSTM을 사용하는 이유는 무엇인가요?",
     "당신은 에너지 예측 전문 AI입니다.\n아래 질문에 한국어로 간결하게 답하세요 (3~5문장).\n질문: {q}"),
    ("cms",      "태양광 발전 설비 효율이 떨어지고 있다면 어떤 점검이 필요한가요?",
     "당신은 설비 관리 전문 AI입니다.\n아래 질문에 한국어로 간결하게 답하세요 (3~5문장).\n질문: {q}"),
]

KOREAN_CASES = [
    ("존댓말 일관성",  "에너지 절약 방법 3가지를 공손한 존댓말로 알려주세요."),
    ("전문 용어",     "역률, 무효전력, 피상전력의 관계를 수식 없이 설명해주세요."),
    ("지시 따르기",   "다음 단어들을 사용해 한 문장을 만드세요: 계량기, 이상, 전력, 탐지"),
]


def run_generation(model_name: str) -> dict:
    results = []
    for domain, question, tpl in GEN_CASES:
        prompt = tpl.format(q=question)
        try:
            answer, ms = _call_ollama(
                model_name,
                [{"role": "user", "content": prompt}],
                max_tokens=300,
                timeout=120,
            )
        except Exception as e:
            answer, ms = f"[오류: {e}]", 0.0
        results.append({"domain": domain, "question": question, "answer": answer, "ms": round(ms)})

    return {"cases": results, "avg_ms": round(sum(r["ms"] for r in results) / len(results))}


def run_korean(model_name: str) -> dict:
    results = []
    for label, question in KOREAN_CASES:
        try:
            answer, ms = _call_ollama(
                model_name,
                [{"role": "user", "content": question}],
                max_tokens=200,
                timeout=60,
            )
        except Exception as e:
            answer, ms = f"[오류: {e}]", 0.0
        results.append({"label": label, "question": question, "answer": answer, "ms": round(ms)})
    return {"cases": results}


# ── 메인 ─────────────────────────────────────────────────────────────

def print_comparison(model_results: list[dict]) -> None:
    a, b = model_results

    W = 68
    print(f"\n{'#'*W}")
    print(f"  sLLM 두 모델 비교 평가 결과")
    print(f"{'#'*W}")

    # 의도 분류 비교
    print(f"\n{'─'*W}")
    print("  [1] 의도 분류 정확도 (100개 골든셋)")
    print(f"{'─'*W}")
    print(f"  {'항목':<28} {'Gemma4 12B':>16} {'EXAONE 3.5 7.8B':>16}")
    print(f"  {'─'*28} {'─'*16} {'─'*16}")

    for key, label in [
        ("accuracy",     "전체 정확도 (%)"),
        ("rule_total",   "Rule 분류 건수"),
        ("rule_accuracy","Rule 정확도 (%)"),
        ("llm_total",    "LLM 분류 건수"),
        ("llm_accuracy", "LLM 정확도 (%)"),
        ("avg_llm_ms",   "LLM 분류 평균 ms"),
    ]:
        va = a["intent"].get(key)
        vb = b["intent"].get(key)
        sa = f"{va}" if va is not None else "—"
        sb = f"{vb}" if vb is not None else "—"
        print(f"  {label:<28} {sa:>16} {sb:>16}")

    # 오답 예시
    for m in model_results:
        wrongs = [x for x in m["intent"]["wrong_examples"] if x.startswith("[")]
        if wrongs:
            print(f"\n  [{m['label']} 오답 예시]")
            for w in wrongs[:3]:
                print(f"    {w}")

    # 응답 품질 비교
    print(f"\n{'─'*W}")
    print("  [2] 응답 품질 (도메인별 생성)")
    print(f"{'─'*W}")
    gen_a = a["generation"]["cases"]
    gen_b = b["generation"]["cases"]
    for i, (ca, cb) in enumerate(zip(gen_a, gen_b)):
        domain = ca["domain"].upper()
        print(f"\n  [{domain}] {ca['question']}")
        print(f"  ┌─ {a['label']} ({ca['ms']}ms) {'─'*40}")
        for line in ca["answer"].strip().split("\n")[:4]:
            print(f"  │  {line}")
        print(f"  └─ {b['label']} ({cb['ms']}ms) {'─'*40}")
        for line in cb["answer"].strip().split("\n")[:4]:
            print(f"     {line}")

    print(f"\n  생성 평균 레이턴시: {a['label']} {a['generation']['avg_ms']}ms  |  {b['label']} {b['generation']['avg_ms']}ms")

    # 한국어 능력 비교
    print(f"\n{'─'*W}")
    print("  [3] 한국어 능력")
    print(f"{'─'*W}")
    ko_a = a["korean"]["cases"]
    ko_b = b["korean"]["cases"]
    for ca, cb in zip(ko_a, ko_b):
        print(f"\n  [{ca['label']}] {ca['question']}")
        print(f"  ┌─ {a['label']}")
        for line in ca["answer"].strip().split("\n")[:3]:
            print(f"  │  {line}")
        print(f"  └─ {b['label']}")
        for line in cb["answer"].strip().split("\n")[:3]:
            print(f"     {line}")

    # 최종 요약
    print(f"\n{'#'*W}")
    print("  종합 요약")
    print(f"{'#'*W}")
    print(f"  {'모델':<28} {'의도분류 정확도':>14} {'LLM만 정확도':>14} {'생성 평균ms':>12}")
    print(f"  {'─'*28} {'─'*14} {'─'*14} {'─'*12}")
    for m in model_results:
        acc  = m["intent"]["accuracy"]
        lacc = m["intent"].get("llm_accuracy") or "—"
        gms  = m["generation"]["avg_ms"]
        print(f"  {m['label']:<28} {str(acc)+'%':>14} {str(lacc)+'%' if lacc != '—' else '—':>14} {str(gms)+'ms':>12}")
    print(f"{'#'*W}\n")


def main():
    all_results = []

    for minfo in MODELS:
        model_name = minfo["name"]
        label = f"{minfo['label']} ({minfo['org']})"
        print(f"\n{'='*68}")
        print(f"  모델 평가 시작: {label}")
        print(f"{'='*68}")

        # 1. 의도 분류
        print(f"\n  [1/3] 의도 분류 (100개 골든셋)...")
        intent_res = run_intent_classification(model_name)
        print(f"    → 전체 정확도: {intent_res['accuracy']}%  "
              f"(Rule {intent_res['rule_total']}건 {intent_res['rule_accuracy']}%  "
              f"LLM {intent_res['llm_total']}건 {intent_res['llm_accuracy']}%  "
              f"avg {intent_res['avg_llm_ms']}ms)")

        # 2. 응답 품질
        print(f"  [2/3] 응답 품질 (4개 케이스)...")
        gen_res = run_generation(model_name)
        print(f"    → 평균 레이턴시: {gen_res['avg_ms']}ms")

        # 3. 한국어 능력
        print(f"  [3/3] 한국어 능력 (3개 케이스)...")
        ko_res = run_korean(model_name)
        print(f"    → 완료")

        all_results.append({
            "model":      model_name,
            "label":      minfo["label"],
            "org":        minfo["org"],
            "intent":     intent_res,
            "generation": gen_res,
            "korean":     ko_res,
        })

    # 비교 출력
    print_comparison(all_results)

    # 저장
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"  결과 저장: {RESULT_PATH}\n")


if __name__ == "__main__":
    main()
