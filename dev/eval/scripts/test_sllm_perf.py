"""
sLLM 성능 테스트 스크립트.

측정 항목:
  1. 의도 분류 정확도 — EXAONE (fast=True)
  2. 에이전트별 응답 샘플 + 레이턴시 — Gemma4 (fast=False)
  3. 한국어 능력 — Gemma4 (fast=False)

실행:
  python dev/eval/scripts/test_sllm_perf.py
"""
from __future__ import annotations

import sys
import time
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "backend" / "src" / "agents"))
sys.path.insert(0, str(_ROOT / "backend" / "src"))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

import llm_client

# ──────────────────────────────────────────────────────────────
# 워밍업 — 첫 호출 시 모델 로딩 대기를 측정에서 제외
# ──────────────────────────────────────────────────────────────

def warmup():
    print("  워밍업 중 (EXAONE + Gemma4 로딩)...", end="", flush=True)
    t0 = time.perf_counter()
    llm_client.chat([{"role": "user", "content": "안녕"}], max_tokens=5, fast=True)
    llm_client.chat([{"role": "user", "content": "안녕"}], max_tokens=5, fast=False)
    print(f" {(time.perf_counter() - t0)*1000:.0f}ms")


# ──────────────────────────────────────────────────────────────
# 1. 의도 분류 정확도 — EXAONE (fast=True)
# ──────────────────────────────────────────────────────────────

INTENT_PROMPT = """사용자 질문을 읽고 아래 중 하나로만 답하세요. 다른 말은 하지 마세요.

- cms      : 설비 상태, 설비 헬스, 설비 진단, 작업지시, 정비, 예지보전, 특정 설비(계통/열병합/냉방/태양광) 상태
- anomaly  : 이상탐지 결과·건수·원인 분석. 이상 유형명(CHPOutage/PowerSpike/COPDrop/NightConsumption/PVNightNonZero)이 나오면 무조건 anomaly.
- report   : 보고서, 리포트, KPI, 월간, 요약, 통계, 실적, 요금, 비용, 전력 비용 관련
- forecast : 예측, 전망, 앞으로, 내일, 다음주, 장기, ~할 것 같아, 예상
- rag      : 개념 설명, 방법, 특정 계량기(V.Z84 등) 값 의미, 그 외 모든 질문

규칙: 특정 이상 유형명이 보이면 anomaly. "건수/발생/빈도/원인"이 이상과 함께 나오면 anomaly.

질문: {question}"""

INTENT_CASES = [
    ("이번 달 이상탐지 건수 알려줘",                "anomaly"),
    ("COP가 갑자기 떨어진 원인이 뭔가요?",           "anomaly"),
    ("PowerSpike 이상이 자주 발생하는 계량기는?",    "anomaly"),
    ("다음주 전력 소비 예측해줘",                    "forecast"),
    ("내일 태양광 발전량 전망은?",                   "forecast"),
    ("앞으로 에너지 소비가 늘어날까요?",             "forecast"),
    ("이번달 KPI 요약 보고서 만들어줘",              "report"),
    ("전력 비용이 얼마나 나왔어?",                   "report"),
    ("월간 에너지 실적 통계 보여줘",                 "report"),
    ("역률(PF)이 뭔가요?",                          "rag"),
    ("자급률이 낮아진 원인이 뭔가요?",               "rag"),
    ("CHP 효율이 좋다는 게 무슨 의미인가요?",        "rag"),
    ("열병합 설비 상태 어때?",                       "cms"),
    ("냉방 시스템 진단해줘",                         "cms"),
    ("작업지시 내역 알려줘",                         "cms"),
]


def test_intent_classification():
    print("\n" + "=" * 65)
    print(f"  의도 분류 정확도  |  모델: {llm_client.LLM_MODEL_FAST}  (fast)")
    print("=" * 65)
    print(f"{'질문':<38} {'정답':>8} {'예측':>8} {'결과':>5} {'ms':>6}")
    print("-" * 65)

    correct = 0
    total_ms = 0.0

    for question, expected in INTENT_CASES:
        t0 = time.perf_counter()
        raw = llm_client.chat(
            [{"role": "user", "content": INTENT_PROMPT.format(question=question)}],
            max_tokens=10,
            fast=True,
        ).strip().lower()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        total_ms += elapsed_ms

        predicted = raw if raw in ("anomaly", "report", "rag", "forecast", "cms") else f"?({raw[:6]})"
        ok = predicted == expected
        if ok:
            correct += 1

        mark = "✓" if ok else "✗"
        q_short = question[:36] + ".." if len(question) > 36 else question
        print(f"{q_short:<38} {expected:>8} {predicted:>8} {mark:>5} {elapsed_ms:>5.0f}")

    accuracy = correct / len(INTENT_CASES) * 100
    avg_ms = total_ms / len(INTENT_CASES)
    print("-" * 65)
    print(f"  정확도: {correct}/{len(INTENT_CASES)} ({accuracy:.1f}%)   평균 레이턴시: {avg_ms:.0f}ms")
    return accuracy, avg_ms


# ──────────────────────────────────────────────────────────────
# 2. 에이전트별 응답 품질 + 레이턴시 — Gemma4 (fast=False)
# ──────────────────────────────────────────────────────────────

GEN_CASES = [
    (
        "rag",
        "역률(PF)이 낮으면 어떤 문제가 생기나요? 에너지 관리 관점에서 설명해주세요.",
        """당신은 에너지 관리 시스템(EMS) 전문 AI입니다.
도메인 지식을 바탕으로 아래 질문에 한국어로 간결하게 답하세요 (3~5문장).
질문: {question}""",
    ),
    (
        "anomaly",
        "COP Drop 이상이 발생했을 때 주요 원인과 대응 방법을 설명해주세요.",
        """당신은 에너지 설비 이상탐지 전문 AI입니다.
이상 유형: COP Drop (냉방 성능 저하)
아래 질문에 한국어로 간결하게 답하세요 (3~5문장).
질문: {question}""",
    ),
    (
        "forecast",
        "전력 소비 예측 모델에서 LSTM을 사용하는 이유가 무엇인가요?",
        """당신은 에너지 예측 전문 AI입니다.
아래 질문에 한국어로 간결하게 답하세요 (3~5문장).
질문: {question}""",
    ),
    (
        "cms",
        "태양광 발전 설비의 효율이 떨어지고 있다면 어떤 점검을 해야 하나요?",
        """당신은 설비 관리 전문 AI입니다.
아래 질문에 한국어로 간결하게 답하세요 (3~5문장).
질문: {question}""",
    ),
]


def test_generation():
    print("\n" + "=" * 65)
    print(f"  응답 품질 테스트  |  모델: {llm_client.LLM_MODEL}  (quality)")
    print("=" * 65)

    latencies = []
    for agent, question, prompt_tpl in GEN_CASES:
        prompt = prompt_tpl.format(question=question)
        print(f"\n[{agent.upper()}] {question}")
        print("-" * 65)
        t0 = time.perf_counter()
        response = llm_client.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=300,
            fast=False,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)
        print(response.strip())
        print(f"\n  ⏱  {elapsed_ms:.0f}ms | {len(response)}자")

    avg_ms = sum(latencies) / len(latencies)
    print("\n" + "=" * 65)
    print(f"  응답 평균 레이턴시: {avg_ms:.0f}ms")
    return avg_ms


# ──────────────────────────────────────────────────────────────
# 3. 한국어 능력 — Gemma4 (fast=False)
# ──────────────────────────────────────────────────────────────

def test_korean():
    print("\n" + "=" * 65)
    print(f"  한국어 능력 체크  |  모델: {llm_client.LLM_MODEL}  (quality)")
    print("=" * 65)

    cases = [
        ("존댓말 일관성", "에너지 절약 방법 3가지를 공손한 존댓말로 알려주세요."),
        ("전문 용어", "역률, 무효전력, 피상전력의 관계를 수식 없이 설명해주세요."),
        ("지시 따르기", "다음 단어들을 사용해 한 문장을 만드세요: 계량기, 이상, 전력, 탐지"),
    ]

    for label, question in cases:
        print(f"\n[{label}] {question}")
        print("-" * 65)
        t0 = time.perf_counter()
        resp = llm_client.chat(
            [{"role": "user", "content": question}],
            max_tokens=200,
            fast=False,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        print(resp.strip())
        print(f"\n  ⏱  {elapsed_ms:.0f}ms")


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n{'#'*65}")
    print(f"  sLLM 성능 테스트")
    print(f"  Quality 모델 : {llm_client.LLM_MODEL}")
    print(f"  Fast 모델    : {llm_client.LLM_MODEL_FAST}")
    print(f"  URL          : {os.getenv('OLLAMA_URL', 'N/A')}")
    print(f"  num_ctx      : {os.getenv('OLLAMA_NUM_CTX', '8192')}")
    print(f"{'#'*65}")

    warmup()

    acc, cls_ms = test_intent_classification()
    gen_ms = test_generation()
    test_korean()

    print("\n" + "#" * 65)
    print("  최종 요약")
    print("#" * 65)
    print(f"  의도 분류 정확도  : {acc:.1f}%  ({llm_client.LLM_MODEL_FAST})")
    print(f"  분류 평균 레이턴시: {cls_ms:.0f}ms")
    print(f"  생성 평균 레이턴시: {gen_ms:.0f}ms  ({llm_client.LLM_MODEL})")
    print("#" * 65)
