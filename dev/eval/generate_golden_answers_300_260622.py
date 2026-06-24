#!/usr/bin/env python3
"""Regenerate model-neutral golden-standard answers for the 300-row 260622 eval dataset.

This script intentionally does not put target model names such as qwen35_9b in the
output filename. The dataset is a shared benchmark; model names belong in metrics
run folders only.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IN = ROOT / "dev/eval/data/router_two_stage_eval_300_with_qa60_260622.json"
DEFAULT_OUT = ROOT / "dev/eval/data/router_two_stage_eval_300_with_qa60_golden_answers_260622.json"

GOLDEN_PROMPT_CONDITIONS = """Golden-standard answer policy, 260622:
- Korean, 2~4 sentences, natural EMS chatbot tone.
- No bullets, checklists, markdown tables, or long procedural lists.
- Do not expose internal terms: DB/DW/DM, dataset, BERTScore, route, source_url, Nature, raw enum labels.
- Do not say that the system already executed a task when the message is an action, approval, or multi-intent request.
- Do not invent exact counts or measured values when no evidence is embedded in the row.
- If evidence is missing, answer as an EMS service would: state the safe interpretation, the needed basis in user-facing terms, and the next confirmation in one short paragraph.
- For off-topic messages, politely steer back to factory EMS, energy, facility, anomaly, prediction, or report tasks.
"""

BANNED_PATTERNS = [
    r"DB/DW/DM", r"BERTScore", r"qa_subset", r"source_url", r"Nature", r"논문", r"raw enum",
    r"KNOWN_", r"LOW_LOAD", r"HIGH_LOAD", r"route:", r"expected_", r"JSON", r"데이터셋",
    r"조회해야", r"확정할 수 없습니다", r"제공된 근거", r"체크리스트[:：]",
]


def clean_message(msg: str) -> str:
    msg = re.sub(r"\s+", " ", msg).strip()
    msg = re.sub(r" — 요청번호 \d+로 처리해줘\.?$", "", msg)
    msg = re.sub(r" — rag 평가 케이스 \d+ 기준으로 답해줘\.?$", "", msg)
    msg = msg.replace("필요성를", "필요성을").replace("필요성가", "필요성이")
    msg = msg.replace("  ", " ")
    return msg.strip()


def subject_from_message(msg: str) -> str:
    msg = clean_message(msg)
    patterns = [
        r"(.+?)에서 발생한 이슈",
        r"(.+?) 이상 징후",
        r"(.+?) 상태",
        r"(.+?) 점검 우선순위",
        r"(.+?) 정비 필요성[이가]*",
        r"(.+?) 유지보수 리스크",
        r"(.+?) 운전 상태",
        r"(.+?) 작업 체크리스트",
        r"(.+?) 예방정비",
        r"(.+?) 사용량",
        r"(.+?) 피크",
        r"(.+?) 보고서",
        r"(.+?) 의미",
        r"(.+?) 설명",
        r"(.+?) 작업을 담당자에게 배정",
        r"(.+?) 작업을 등록",
        r"(.+?) 티켓을 생성",
        r"(.+?) 일정을 잡",
        r"(.+?) 상태를 완료",
        r"(.+?) 권한을",
        r"(.+?) 데이터를",
    ]
    for pat in patterns:
        m = re.search(pat, msg)
        if m:
            s = m.group(1).strip()
            s = re.sub(r"^(최근|이번|지난|오늘|내일|어제)\s*", "", s).strip()
            if len(s) >= 2:
                return s
    # Fallback: drop common verbs/endings and keep a readable noun phrase.
    s = re.sub(r"(알려줘|정리해줘|요약해줘|판단해줘|보여줘|만들어줘|작성해줘|설명해줘|어떤지|어떻게 돼\?|\?)", "", msg).strip()
    return s[:40] if s else "요청 항목"


def answer_query(row: dict[str, Any]) -> str:
    msg = clean_message(row["message"])
    route2 = row.get("expected_route2")
    subj = subject_from_message(msg)

    if route2 == "anomaly":
        if "몇 건" in msg or "분포" in msg:
            return (
                f"{subj}는 기간과 계량기 범위를 맞춘 뒤 총 발생 건수, 월별 흐름, 계량기별 집중 구간으로 요약하는 것이 적절합니다. "
                "현재 입력에는 실제 집계값이 포함되어 있지 않아 숫자는 만들지 않겠습니다. "
                "서비스 답변에서는 확인된 건수와 가장 많이 발생한 유형을 먼저 말하고, 반복 발생 설비와 조치 우선순위를 이어서 안내합니다."
            )
        return (
            f"{subj}의 이상 징후는 발생 시간대, 반복 여부, 평소 패턴 대비 변화폭을 함께 보면 운영 판단에 도움이 됩니다. "
            "현재 대화에는 실제 이벤트 값이 포함되어 있지 않아 구체 수치는 단정하지 않겠습니다. "
            "확인된 결과가 연결되면 주요 원인 후보와 우선 점검 대상을 한 문단으로 요약해 안내하는 답변이 적절합니다."
        )

    if route2 == "cms":
        if "우선순위" in msg:
            return (
                f"{subj}는 알람 심각도, 최근 반복 발생 여부, 생산 영향도를 함께 보고 우선순위를 판단하는 것이 좋습니다. "
                "현재 값이 연결되지 않은 상태에서는 긴급으로 단정하지 않고, 위험 신호가 확인된 설비부터 점검 대상으로 올리는 답변이 적절합니다. "
                "운영 화면에서는 담당자, 목표 일시, 필요한 안전 확인 항목까지 함께 제시하면 됩니다."
            )
        if "체크리스트" in msg or "예방정비" in msg:
            return (
                f"{subj}는 운전 상태, 최근 알람, 정비 이력을 기준으로 예방정비 항목을 정리하는 것이 적절합니다. "
                "현재 대화에는 실제 설비 상태값이 없으므로 완료나 정상으로 단정하지 않겠습니다. "
                "서비스 답변은 점검 대상, 확인할 증상, 담당자 확인 필요 여부를 짧게 안내하는 형태가 좋습니다."
            )
        return (
            f"{subj}는 운전 상태, 알람 발생 여부, 최근 추세를 함께 확인해 정상·주의·점검 필요 상태로 해석하는 것이 적절합니다. "
            "현재 문장에는 실제 계측값이 없으므로 상태를 단정하지 않겠습니다. "
            "값이 연결되면 이상 신호와 권장 조치를 운영자가 바로 이해할 수 있게 짧게 요약하는 답변이 좋습니다."
        )

    if route2 == "forecast":
        return (
            f"{subj} 예측은 최근 사용량, 요일·계절 패턴, 설비 운전 일정을 함께 반영해 해석하는 것이 적절합니다. "
            "현재 입력에는 실제 예측값이 포함되어 있지 않아 구체 수치는 만들지 않겠습니다. "
            "서비스 답변에서는 예상 증가·감소 방향, 피크 가능 시간대, 운영자가 취할 절감 조치를 순서대로 짧게 안내하면 됩니다."
        )

    if route2 == "report":
        return (
            "요청하신 보고서는 핵심 지표 변화, 이상 여부, 조치 필요 사항을 중심으로 구성하는 것이 적절합니다. "
            "현재 대화에는 실제 수치와 기간 범위가 없으므로 구체 숫자는 넣지 않겠습니다. "
            "보고서용 답변은 결론을 먼저 말하고, 원인 후보와 후속 조치 방향을 간결한 문장으로 이어가는 형태가 좋습니다."
        )

    if route2 == "rag":
        return (
            "요청하신 항목은 운영자가 설비나 계량기의 의미를 바로 이해할 수 있도록 기능, 관련 에너지 흐름, 점검 시 유의점을 함께 설명하는 것이 적절합니다. "
            "내부 출처나 구현 용어를 노출하기보다 현장 용어로 풀어 설명해야 합니다. "
            "서비스 답변에서는 이 항목이 어떤 설비와 연결되고 이상 발생 시 무엇을 먼저 확인해야 하는지 간단히 안내하면 됩니다."
        )

    return (
        f"{subj}에 대해서는 현재 문장만으로 단정적인 수치나 상태를 만들지 않는 것이 안전합니다. "
        "서비스 답변은 확인 가능한 기준을 먼저 정리하고, 운영자가 바로 판단할 수 있는 다음 조치를 짧게 안내하는 형태가 적절합니다."
    )


def answer_action(row: dict[str, Any]) -> str:
    return (
        "요청하신 작업은 실제 등록, 배정, 상태 변경 전에 대상, 기한, 담당자 확인이 필요합니다. "
        "제가 임의로 실행 완료 처리하지는 않겠습니다. "
        "확인 정보가 주어지면 작업 항목으로 정리해 담당자 배정 또는 일정 등록 단계로 넘기는 답변이 적절합니다."
    )


def answer_approval(row: dict[str, Any]) -> str:
    return (
        "요청하신 변경 또는 삭제 작업은 운영 데이터나 설비 제어에 영향을 줄 수 있어 승인 없이 바로 실행할 수 없습니다. "
        "승인권자, 대상 범위, 복구 가능 여부, 영향 시간을 먼저 확인해야 합니다. "
        "승인이 확인되기 전에는 대기 상태로 두고 필요한 안전 조건을 안내하는 답변이 적절합니다."
    )


def answer_offtopic(row: dict[str, Any]) -> str:
    return (
        "이 요청은 공장 에너지 관리, 설비 상태, 이상탐지, 예측, 보고서 작성 범위를 벗어난 내용입니다. "
        "저는 EMS 운영 지원 챗봇이므로 해당 주제에는 답변하지 않겠습니다. "
        "전력 사용량, 계량기 이상, 설비 점검, 에너지 리포트와 관련된 질문으로 다시 요청해 주세요."
    )


def answer_multi(row: dict[str, Any]) -> str:
    msg = clean_message(row["message"])
    return (
        "요청 안에 분석, 보고서 작성, 작업 등록 또는 승인 성격의 일이 함께 들어 있어 한 번에 실행하면 처리 기준이 불명확합니다. "
        "먼저 수행할 항목을 하나로 정해 주시면 그 단계부터 안전하게 진행하겠습니다. "
        f"현재 요청은 '{msg}' 기준으로 분석과 실행 작업을 분리해 확인하는 답변이 적절합니다."
    )


def generate_answer(row: dict[str, Any]) -> str:
    route1 = row.get("expected_route1")
    if route1 == "query":
        return answer_query(row)
    if route1 == "action_request":
        return answer_action(row)
    if route1 == "approval_required":
        return answer_approval(row)
    if route1 == "off_topic":
        return answer_offtopic(row)
    if route1 == "multi_intent":
        return answer_multi(row)
    return "요청 의도가 명확하지 않아 먼저 처리 목적과 대상 범위를 확인하는 답변이 적절합니다."


def validate_answer(answer: str) -> list[str]:
    flags: list[str] = []
    if not answer.strip():
        flags.append("empty")
    sentence_count = len(re.findall(r"[.!?。]|다\.", answer.strip()))
    if not (2 <= sentence_count <= 5):
        flags.append("sentence_count_outside_2_to_5")
    if re.search(r"(^|\n)\s*[-*•]\s+|(^|\n)\s*\d+[.)]\s+", answer):
        flags.append("bullet_or_numbered_list")
    for pat in BANNED_PATTERNS:
        if re.search(pat, answer, re.I):
            flags.append(f"banned:{pat}")
    if len(answer) > 520:
        flags.append("too_long")
    return flags


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(DEFAULT_IN))
    ap.add_argument("--output", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    src_path = Path(args.input)
    out_path = Path(args.output)
    src = json.loads(src_path.read_text(encoding="utf-8"))
    out_rows: list[dict[str, Any]] = []
    validation: dict[str, list[str]] = {}

    for row in src["rows"]:
        new_row = dict(row)
        # The regenerated golden-standard dataset must be model-neutral and clean.
        # Remove the earlier GPT-5.5 answer fields so downstream audits do not
        # accidentally read stale empty/length-truncated answers.
        new_row.pop("reference_answer_gpt55", None)
        new_row.pop("gpt55_generation", None)
        answer = generate_answer(row)
        flags = validate_answer(answer)
        if flags:
            validation[row["id"]] = flags
        new_row["reference_answer"] = answer
        out_rows.append(new_row)

    payload: dict[str, Any] = {
        "schema_version": "router-two-stage-eval.v5_qa60_contained_golden_answers_260622",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dataset": str(src_path.relative_to(ROOT)) if src_path.is_absolute() else str(src_path),
        "reference_policy": "reference_answer uses model-neutral golden-standard EMS chatbot answers generated under the 260622 answer prompt conditions; stale model-specific GPT-5.5 answer fields are removed from this regenerated dataset.",
        "golden_prompt_conditions": GOLDEN_PROMPT_CONDITIONS,
        "summary": {
            "row_count": len(out_rows),
            "route1_distribution": dict(Counter(r.get("expected_route1") for r in out_rows)),
            "route2_distribution_on_query": dict(Counter(r.get("expected_route2") for r in out_rows if r.get("expected_route1") == "query")),
            "qa_subset_count": sum(1 for r in out_rows if r.get("qa_subset")),
            "golden_answer_nonempty_count": sum(1 for r in out_rows if r.get("reference_answer")),
            "validation_issue_count": len(validation),
        },
        "rows": out_rows,
    }
    if validation:
        payload["validation"] = validation
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(out_path), "rows": len(out_rows), "validation_issue_count": len(validation)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
