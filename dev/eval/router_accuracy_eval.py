# -*- coding: utf-8 -*-
"""
router_accuracy_eval.py — 팀원 제공 500문항으로 의도 분류기 정확도 평가

팀원 라우트 → 우리 라우트 매핑:
  quick_answer      → rag
  evidence_answer   → anomaly
  needs_job         → report
  approval_required → off_topic
  report_shell      → report

실행:
  # 규칙 기반만 (LLM 없이, 빠름)
  python dev/eval/router_accuracy_eval.py

  # 규칙 + LLM 폴백 (Ollama 필요)
  python dev/eval/router_accuracy_eval.py --llm

  # 데이터셋 경로 직접 지정
  python dev/eval/router_accuracy_eval.py --dataset router_5route_eval_500_260610.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common_metrics import build_metrics_envelope, default_output_paths, infer_run_id, write_json
from router_dataset_preflight import run_preflight as run_router_preflight

# ── 팀원 라우트 → 우리 라우트 매핑 ───────────────────────────────

ROUTE_MAP: dict[str, str] = {
    "quick_answer":      "rag",
    "evidence_answer":   "anomaly",
    "needs_job":         "report",
    "approval_required": "off_topic",
    "report_shell":      "report",
    # Stage-1/2 새 스키마와의 호환성
    "cms": "cms",
    "forecast": "forecast",
    "anomaly": "anomaly",
    "rag": "rag",
    "report": "report",
    # Stage-1 request_type 호환성
    "query": "query",
    "action_request": "action_request",
    "approval_required": "approval_required",
    "off_topic": "off_topic",
}

OUR_ROUTES = ["anomaly", "cms", "forecast", "off_topic", "rag", "report", "query", "action_request", "approval_required"]

# 기본 파일 경로
_ROOT = Path(__file__).parent.parent.parent
DEFAULT_DATASET = _ROOT / "dev/eval/data/router_5route_eval_500_260610.json"
TEST_ID = "test09_router_5route_eval"
METRIC_FAMILY = "router_classification"
DEFAULT_OUT_JSON: Path | None = None
DEFAULT_OUT_MD: Path | None = None

# ── 규칙 기반 분류기 (orchestrator.py 동일 로직) ─────────────────

_KW_ANOMALY = re.compile(
    r"이상\s*탐지|이상\s*발생|이상\s*이력|이상\s*건수|이상\s*원인"
    r"|이상\s*분석|이상\s*추이|이상\s*현황|이상\s*분포|이상\s*비교"
    r"|비정상|스파이크|급등|급락|급증|오류|탐지|경보|알람|fault|anomal"
    r"|chpoutage|powerspike|copdrop|nightconsumption|pvnightnonzero"
    r"|냉매\s*누설|진동\s*과다|cop\s*저하|COP\s*저하|압력\s*변동"
    r"|전압\s*불균형|유량\s*감소|전력\s*급증|소음\s*증가"
    r"|사건|빈도|심각도|발생\s*건수|몇\s*건|잔차|급등\s*이벤트|게이트웨이\s*장애"
)
_KW_REPORT = re.compile(
    r"보고서|리포트|report|kpi|월간|통계|실적|집계|월별\s*현황"
    r"|요금|비용|cost|전력\s*비용|얼마나\s*나"
    r"|의존도|자급률|출력\s*얼마|사용량\s*어때|사용량\s*얼마"
    r"|그리드\s*의존|외부\s*전력\s*의존|계통\s*의존"
    r"|보고서.*요약|요약.*보고서|월간.*요약|요약.*월간|실적.*요약|요약.*실적"
)
_KW_FORECAST = re.compile(
    r"예측|전망|앞으로|내일|다음\s*주|장기|예상|forecast|미래|될\s*것"
    r"|계속\s*될까|계속될까|낮아질까|높아질까|늘어날까|줄어들까"
    r"|떨어질까|올라갈까|늘\s*까|줄\s*까|추세|앞으로.*될"
)
_KW_CMS = re.compile(
    r"작업\s*지시|정비|수리|예지보전|상태\s*감시|헬스|진단"
    r"|설비\s*상태|설비\s*이상|설비\s*점검|설비\s*확인|설비\s*문제"
    r"|설비.*요약|요약.*설비|설비\s*상태\s*요약|설비\s*요약"
    r"|냉방\s*설비|계통.*설비|수전.*설비"
    r"|열병합|시뮬|시뮬레이터|시연"
    r"|설비.*이상\s*건수|설비.*최근\s*이상|태양광.*이상\s*건수|태양광.*최근\s*이상"
)
_KW_OFFTOPIC = re.compile(
    r"주식|코인|비트코인|가상\s*화폐|암호\s*화폐"
    r"|요리|레시피|음식|맛집|식당|배달|점심\s*메뉴|저녁\s*메뉴|아침\s*메뉴|구내식당"
    r"|연예인|드라마|영화|음악|스포츠|야구|축구|농구|골프|올림픽"
    r"|정치|선거|국회|대통령|국정|법안"
    r"|오늘\s*날씨|내일\s*날씨|날씨\s*예보|기상\s*예보"
    r"|의료|병원|질병|증상|처방전"
    r"|연애|결혼|이혼|육아|임신"
    r"|게임|유튜브|틱톡|sns|인스타|트위터"
    r"|운영\s*테이블.*변경|테이블.*변경|테이블.*삭제|테이블.*drop|테이블.*truncate"
    r"|서버\s*파일.*덮어쓰기|파일.*덮어쓰기|원격.*서버.*파일"
    r"|승인\s*요청|결재\s*올려|허가\s*받아|force\s*처리|강제.*진행"
    r"|db.*insert|db.*update|db.*delete|db.*drop"
)
_KW_FUTURE = re.compile(
    r"계속\s*될까|계속될까|낮아질까|높아질까|늘어날까|줄어들까"
    r"|떨어질까|올라갈까|계속\s*낮아|계속\s*떨어|계속\s*높아"
    r"|앞으로.*될|~할\s*것|추세.*앞|앞.*추세"
)
_KW_EQ_ANOMALY_COUNT = re.compile(
    r"(태양광|계통|수전|냉방|chp|열병합).*(이상.*건수|최근.*이상|이상.*몇\s*건)"
)
_METER_URN_PAT = re.compile(r"[A-Z]\d?\.(?:[A-Z]\.)?Z\d+", re.IGNORECASE)


def rule_classify(question: str) -> str | None:
    """규칙 기반 분류. 명확히 분류 불가하면 None 반환."""
    if _METER_URN_PAT.search(question):
        return "rag"
    q = question.lower()
    if _KW_OFFTOPIC.search(q):
        return "off_topic"
    if _KW_FUTURE.search(q):
        return "forecast"
    if _KW_EQ_ANOMALY_COUNT.search(q):
        return "cms"
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


def llm_classify(question: str) -> str:
    """LLM 폴백 (orchestrator.py 동일 프롬프트). Ollama 필요."""
    # orchestrator.py와 동일한 INTENT_PROMPT 사용
    INTENT_PROMPT = """사용자 질문을 읽고 아래 중 하나로만 답하세요. 다른 말은 하지 마세요.

- anomaly   : 이상탐지 결과·건수·원인 분석.
- cms       : 설비 상태 점검, 작업지시, 정비, 예지보전.
- report    : 실적·현황 조회. 보고서, KPI, 월간, 요약, 통계, 자급률, 의존도, 사용량, 비용.
- forecast  : 미래 예측·전망. "앞으로", "내일", "~될까?".
- rag       : 개념 설명, 계량기 값 의미, 실시간 센서값 조회, 그 외 에너지·설비 관련 질문.
- off_topic : 에너지 관리, 설비 모니터링과 전혀 무관한 질문.

질문: {question}"""

    try:
        backend_src = _ROOT / "backend" / "src" / "agents"
        sys.path.insert(0, str(backend_src))
        from llm_client import chat as llm_chat  # type: ignore
        raw = llm_chat(
            [{"role": "user", "content": INTENT_PROMPT.format(question=question)}],
            max_tokens=10,
            fast=True,
        ).strip().lower()
        valid = ("anomaly", "report", "rag", "forecast", "cms", "off_topic",
                 "query", "action_request", "approval_required")
        return raw if raw in valid else "rag"
    except Exception as e:
        print(f"[LLM 폴백 실패] {e}", file=sys.stderr)
        return "rag"


# ── 평가 핵심 로직 ────────────────────────────────────────────────

def evaluate(rows: list[dict], use_llm: bool = False) -> dict:
    confusion: dict[str, dict[str, int]] = {
        r: {p: 0 for p in OUR_ROUTES} for r in OUR_ROUTES
    }
    support: Counter = Counter()
    correct_by_route: Counter = Counter()
    pred_counts: Counter = Counter()
    predictions = []
    rule_hit = 0

    for row in rows:
        teammate_route = row.get("expected_route")
        if teammate_route is None:
            teammate_route = row.get("expected_request_type")
        if teammate_route is None:
            raise KeyError("expected_route 또는 expected_request_type이 없습니다")
        expected = ROUTE_MAP[teammate_route]
        question = row["message"]

        predicted = rule_classify(question)
        method = "rule"
        if predicted is None:
            if use_llm:
                predicted = llm_classify(question)
                method = "llm"
            else:
                predicted = "rag"   # 규칙 미분류 → 기본값
                method = "default"
        else:
            rule_hit += 1

        is_correct = expected == predicted
        support[expected] += 1
        pred_counts[predicted] += 1
        confusion[expected][predicted] += 1
        if is_correct:
            correct_by_route[expected] += 1
        predictions.append({
            "id": row.get("id"),
            "message": question,
            "teammate_route": teammate_route,
            "expected_route": expected,
            "predicted_route": predicted,
            "method": method,
            "correct": is_correct,
        })

    per_route: dict[str, dict] = {}
    for route in OUR_ROUTES:
        tp = confusion[route][route]
        fp = sum(confusion[other][route] for other in OUR_ROUTES if other != route)
        fn = sum(confusion[route][other] for other in OUR_ROUTES if other != route)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall    = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_route[route] = {
            "support": support[route],
            "predicted_count": pred_counts[route],
            "correct": correct_by_route[route],
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    total   = len(rows)
    correct = sum(1 for p in predictions if p["correct"])
    active_routes = [r for r in OUR_ROUTES if support[r] > 0]
    macro_f1 = sum(per_route[r]["f1"] for r in active_routes) / len(active_routes)

    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_count": total,
        "rule_hit_rate": round(rule_hit / total, 4) if total else 0,
        "use_llm_fallback": use_llm,
        "route_map": ROUTE_MAP,
        "overall": {
            "correct": correct,
            "accuracy": round(correct / total, 4) if total else 0,
            "macro_f1": round(macro_f1, 4),
        },
        "per_route": per_route,
        "confusion_matrix": confusion,
        "errors": [p for p in predictions if not p["correct"]],
        "predictions": predictions,
    }


def to_common_metrics(raw: dict[str, Any], *, dataset_path: Path, out_json: Path) -> dict[str, Any]:
    """Wrap router eval output in the shared experiment metrics schema."""
    overall = raw.get("overall", {})
    summary = {
        "accuracy": overall.get("accuracy", 0.0),
        "macro_f1": overall.get("macro_f1", 0.0),
        "correct": overall.get("correct", 0),
        "total": raw.get("dataset_count", 0),
        "rule_hit_rate": raw.get("rule_hit_rate", 0.0),
        "use_llm_fallback": raw.get("use_llm_fallback", False),
    }
    return build_metrics_envelope(
        test_id=TEST_ID,
        run_id=infer_run_id(out_json),
        metric_family=METRIC_FAMILY,
        dataset_path=dataset_path,
        dataset_count=raw.get("dataset_count", 0),
        phase_latency_ms={"route": 0.0, "total": 0.0},
        component_latency_ms={"workflow": 0.0, "total": 0.0},
        summary=summary,
        gates={
            "accuracy_min_0_80": summary["accuracy"] >= 0.80,
            "macro_f1_min_0_80": summary["macro_f1"] >= 0.80,
        },
        errors=raw.get("errors", []),
        details={
            "evaluated_at_raw": raw.get("evaluated_at"),
            "route_map": raw.get("route_map", {}),
            "per_route": raw.get("per_route", {}),
            "confusion_matrix": raw.get("confusion_matrix", {}),
            "predictions": raw.get("predictions", []),
        },
        evaluated_at=raw.get("evaluated_at"),
    )


# ── 마크다운 리포트 ────────────────────────────────────────────────

def write_markdown(result: dict, out_md: Path) -> None:
    ov = result["overall"]
    lines = [
        "# Router Accuracy Eval — 팀원 500문항 기반 의도 분류기 정확도",
        "",
        f"> 평가 시각(UTC): `{result['evaluated_at']}`  "
        f"| 문항 수: {result['dataset_count']}  "
        f"| 규칙 히트율: {result['rule_hit_rate']:.1%}  "
        f"| LLM 폴백: {'ON' if result['use_llm_fallback'] else 'OFF (기본값=rag)'}",
        "",
        "## 라우트 매핑",
        "",
        "| 팀원 라우트 | 우리 라우트 |",
        "|---|---|",
    ]
    for k, v in result["route_map"].items():
        lines.append(f"| `{k}` | `{v}` |")

    lines += [
        "",
        "## Overall",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| accuracy | **{ov['accuracy']:.1%}** |",
        f"| macro_f1 | **{ov['macro_f1']:.1%}** |",
        f"| correct  | {ov['correct']} / {result['dataset_count']} |",
        "",
        "## Per-route",
        "",
        "| route | support | predicted | correct | precision | recall | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for route, m in result["per_route"].items():
        if m["support"] == 0:
            continue
        lines.append(
            f"| {route} | {m['support']} | {m['predicted_count']} | {m['correct']} | "
            f"{m['precision']:.1%} | {m['recall']:.1%} | {m['f1']:.1%} |"
        )

    lines += ["", "## Confusion matrix", "", "rows=expected, columns=predicted", ""]
    active = [r for r in OUR_ROUTES if result["per_route"][r]["support"] > 0]
    lines.append("| expected \\ predicted | " + " | ".join(active) + " |")
    lines.append("|---" + "|---:" * len(active) + "|")
    for expected in active:
        row = result["confusion_matrix"][expected]
        lines.append("| " + expected + " | " + " | ".join(str(row[p]) for p in active) + " |")

    lines += ["", "## 오분류 샘플 (최대 30건)", ""]
    errors = result["errors"][:30]
    if not errors:
        lines.append("오류 없음.")
    else:
        lines += [
            "| # | 질문 | teammate_route | expected | predicted | method |",
            "|---|---|---|---|---|---|",
        ]
        for i, e in enumerate(errors, 1):
            msg = str(e["message"]).replace("|", "\\|")[:60]
            lines.append(
                f"| {i} | {msg} | {e['teammate_route']} | {e['expected_route']} "
                f"| {e['predicted_route']} | {e['method']} |"
            )

    lines += [
        "",
        "## 해석 노트",
        "",
        "- `cms` 라우트는 데이터셋에 없음 — support=0이므로 매크로 F1 계산에서 제외.",
        "- `report_shell`(100건)은 `context.qa_blocked=True` 의존. 메시지 텍스트만으로 분류 시 어려움.",
        "- `approval_required`(100건)은 '운영 테이블 변경·서버 파일 덮어쓰기' → `off_topic` 매핑.",
        "- LLM 폴백 OFF 시 규칙 미분류 항목은 `rag` 기본값 처리.",
    ]

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── 엔트리포인트 ──────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="의도 분류기 정확도 평가")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET,
                        help="팀원 JSON 파일 경로 (기본: 프로젝트 루트)")
    parser.add_argument("--llm", action="store_true",
                        help="규칙 미분류 시 EXAONE LLM 폴백 사용 (Ollama 필요)")
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON,
                        help="공통 metrics.json 출력 경로. 기본값: reports/experiments/test09_router_5route_eval/run_*/metrics.json")
    parser.add_argument("--out-md",   type=Path, default=DEFAULT_OUT_MD,
                        help="Markdown report 출력 경로. 기본값: metrics.json과 같은 run 폴더/report.md")
    parser.add_argument("--run-id", type=str, default=None,
                        help="reports/experiments/<test_id>/<run_id>/ 아래에 저장할 run id")
    parser.add_argument("--preflight", action="store_true",
                        help="실행 전 dataset preflight(schema+id unique+label 분포) 수행")
    parser.add_argument("--preflight-only", action="store_true",
                        help="preflight만 수행하고 점수 계산은 생략")
    parser.add_argument("--dataset-schema", type=Path, default=None,
                        help="Preflight에서 사용할 JSON schema 경로(기본: stage1/stage2 파일명 추론)")
    args = parser.parse_args()

    if args.out_json is None or args.out_md is None:
        default_json, default_md = default_output_paths(
            TEST_ID,
            args.run_id,
            suffix="llm" if args.llm else "rule",
        )
        if args.out_json is None:
            args.out_json = default_json
        if args.out_md is None:
            args.out_md = default_md

    if not args.dataset.exists():
        print(f"[오류] 데이터셋 없음: {args.dataset}", file=sys.stderr)
        sys.exit(1)

    if args.preflight:
        stem = args.dataset.name.lower()
        if args.dataset_schema is not None or "stage1" in stem or "stage2" in stem:
            run_router_preflight(args.dataset, schema_path=args.dataset_schema)
        else:
            print("[알림] preflight은 stage1/stage2 데이터셋 또는 --dataset-schema 지정 시 동작합니다.", file=sys.stderr)

    if args.preflight_only:
        if not args.preflight and ("stage1" in args.dataset.name.lower() or "stage2" in args.dataset.name.lower() or args.dataset_schema is not None):
            run_router_preflight(args.dataset, schema_path=args.dataset_schema)
        elif not args.preflight:
            print("[오류] preflight-only는 stage1/stage2 데이터셋 또는 --dataset-schema 지정 시에만 사용 가능합니다.", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    rows = json.loads(args.dataset.read_text(encoding="utf-8"))
    print(f"[평가 시작] {len(rows)}문항 | LLM 폴백: {'ON' if args.llm else 'OFF'}")

    raw_result = evaluate(rows, use_llm=args.llm)
    metrics = to_common_metrics(raw_result, dataset_path=args.dataset, out_json=args.out_json)

    write_json(args.out_json, metrics)
    write_markdown(raw_result, args.out_md)

    ov = raw_result["overall"]
    print(json.dumps({
        "schema_version": metrics["schema_version"],
        "test_id": metrics["test_id"],
        "accuracy":       f"{ov['accuracy']:.1%}",
        "macro_f1":       f"{ov['macro_f1']:.1%}",
        "correct":        f"{ov['correct']}/{raw_result['dataset_count']}",
        "rule_hit_rate":  f"{raw_result['rule_hit_rate']:.1%}",
        "out_json":       str(args.out_json),
        "out_md":         str(args.out_md),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
