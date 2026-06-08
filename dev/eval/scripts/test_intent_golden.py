"""
의도 분류 정확도 테스트 — chat_qa_golden_100_final.json 기반
langgraph 불필요 — 분류 로직 인라인, Ollama 직접 호출.
"""
import json
import os
import re
import time
from pathlib import Path
from collections import defaultdict

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "chat_qa_golden_100_final.json"
data = json.load(open(DATA_PATH, encoding="utf-8"))

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/v1").rstrip("/").removesuffix("/v1")
LLM_MODEL  = os.getenv("LLM_MODEL", "gemma4:12b")

# ── orchestrator.py와 동기화된 분류 로직 ─────────────────────────

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

- anomaly  : 이상탐지 결과·건수·원인 분석. 이상 유형명(CHPOutage/PowerSpike/COPDrop/NightConsumption/PVNightNonZero)이 나오면 무조건 anomaly. "이상 몇 건?", "이상 발생 이력", "이상 심각도" 등.
- cms      : 설비 상태 점검, 작업지시, 정비, 예지보전. "CHP 괜찮아?", "태양광 이상 확인해봐", "설비 점검해야 돼?", "냉방 설비 상태".
- report   : 실적·현황 조회. 보고서, KPI, 월간, 요약, 통계, 자급률, 의존도, 사용량, 비용, 전력 비용. "자급률 왜 떨어졌어?", "외부 전력 의존도?", "이번 달 소비량?".
- forecast : 미래 예측·전망. "앞으로", "내일", "다음 주", "~될까?", "~낮아질까?", "~떨어질까?", "추세". 이상 키워드가 있어도 미래를 묻는 것이면 forecast. 예: "COP 계속 낮아질까?" = forecast.
- rag      : 개념 설명, 계량기 값 의미, 실시간 센서값 조회, 그 외.

핵심 구분:
- "이상 있어?" / "이상 발생했어?" → anomaly (현재/과거 탐지 결과)
- "이상 계속될까?" / "COP 떨어질까?" → forecast (미래 예측)
- "태양광 이상 확인해봐" / "설비 상태 어때?" → cms (설비 점검)
- "자급률 왜 떨어졌어?" / "의존도 어때?" → report (실적 현황)

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


def _llm_classify(question: str) -> str:
    resp = httpx.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": INTENT_PROMPT.format(question=question)}],
            "stream": False,
            "think": False,
            "options": {"num_predict": 10},
        },
        timeout=60,
    )
    resp.raise_for_status()
    raw = resp.json()["message"]["content"].strip().lower()
    return raw if raw in ("anomaly", "report", "rag", "forecast", "cms") else "rag"


def classify(question: str) -> tuple[str, str]:
    intent = _rule_classify(question)
    if intent:
        return intent, "rule"
    return _llm_classify(question), "llm"


# ── 실행 ─────────────────────────────────────────────────────────

print(f"\n{'='*70}")
print(f"  의도 분류 정확도 테스트 v2 | {len(data)}개 질문 | 모델: {LLM_MODEL}")
print(f"{'='*70}\n")

correct = 0
wrong = []
by_cat: dict[str, list] = defaultdict(list)

for i, item in enumerate(data):
    q    = item["question"]
    true = item["category"]
    t0   = time.time()
    pred, method = classify(q)
    ms   = int((time.time() - t0) * 1000)
    ok   = pred == true
    correct += ok
    by_cat[true].append(ok)
    sym  = "✓" if ok else "✗"
    if not ok:
        wrong.append({"id": item["id"], "q": q, "true": true, "pred": pred, "method": method})
    print(f"[{i+1:3d}] {sym} {true:8s} → {pred:8s}  ({method:4s}) {ms:5d}ms  {q[:45]}")

total = len(data)
acc   = correct / total * 100

print(f"\n{'='*70}")
print(f"  전체 정확도: {correct}/{total} ({acc:.1f}%)")
print(f"{'='*70}")
print("\n카테고리별 정확도:")
for cat, results in sorted(by_cat.items()):
    c = sum(results)
    print(f"  {cat:10s}: {c}/{len(results)} ({c/len(results)*100:.0f}%)")

if wrong:
    print(f"\n{'='*70}")
    print(f"  오답 목록 ({len(wrong)}건)")
    print(f"{'='*70}")
    for w in wrong:
        print(f"\n  [{w['id']}] ({w['method']})")
        print(f"  질문  : {w['q']}")
        print(f"  정답  : {w['true']}  →  예측: {w['pred']}")

print()
