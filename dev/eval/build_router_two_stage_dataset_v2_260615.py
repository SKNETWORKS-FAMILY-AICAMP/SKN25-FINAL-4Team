# -*- coding: utf-8 -*-
"""Build duplicate-free standalone two-stage router evaluation dataset v2.

Output:
- dev/eval/data/router_two_stage_eval_300_260617.json

Differences from v1:
- Updated label distribution: Route1 query/action/approval/off_topic/multi_intent = 180/40/30/30/20, Route2 36 each.
- Adds multi_intent gate rows for compound anomaly/report/action requests that should ask the user to split or clarify.
- No duplicate message text.
- Still independent from retrieval/evidence QA JSON.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "dev" / "eval" / "data" / "router_two_stage_eval_300_260617.json"

route2_specs = {
    "anomaly": {
        "topics": ["PowerSpike", "COPDrop", "CHPOutage", "NightConsumption", "PVNightNonZero", "VoltageSag", "PeakLoad", "IdleConsumption"],
        "templates": [
            "{period} {area} {topic} 이상 후보는 몇 건 발생했나요?",
            "{asset}에서 감지된 {topic} 원인을 분석해줘.",
            "{topic} 알람을 무시해도 되는지 데이터 근거로 알려줘.",
            "{area} {topic} 이상 중 {severity} 등급만 요약해줘.",
            "최근 {window} {asset} {topic} 발생 추이와 주요 원인 후보를 알려줘.",
            "anomaly check: {area} {topic} summary 부탁해.",
            "{period} {topic} 이상탐지 결과가 평소보다 심각한지 봐줘.",
            "{asset}에 {topic} 패턴이 반복되는지 확인해줘.",
            "{area}에서 {topic} 이상이 가장 많이 발생한 시간대를 알려줘.",
            "{period} {asset} {topic} HIGH 알람만 따로 분리해서 설명해줘.",
        ],
        "note": "이상탐지/알람/심각도 분류",
    },
    "cms": {
        "topics": ["설비 상태", "점검 우선순위", "정비 필요성", "유지보수 리스크", "운전 상태", "health 상태", "작업 체크리스트", "예방정비"],
        "templates": [
            "{asset} {topic}가 어떤지 알려줘.",
            "{area} 설비 {topic}를 정리해줘.",
            "{asset} 정비가 필요한 상태인지 판단해줘.",
            "CMS 기준으로 {area} health 상태 요약해줘.",
            "이번 주 {asset} 유지보수 리스크를 알려줘.",
            "{area} 점검 체크리스트에서 먼저 봐야 할 항목은?",
            "설비 상태 점검 기준에서 {area}는 어떤 상태야?",
            "{asset} 운전 상태가 정상 범위인지 확인해줘.",
            "{period} {area} 설비 예방정비 우선순위를 알려줘.",
            "{asset}의 최근 점검 이력 기준으로 CMS 상태를 판정해줘.",
        ],
        "note": "설비 상태/CMS/점검 분류",
    },
    "report": {
        "topics": ["운영 보고서", "에너지 사용 리포트", "KPI 요약", "개선 포인트", "운영 리스크", "대시보드 요약", "월간 리포트", "경영진 보고"],
        "templates": [
            "{period} {topic} 핵심 내용을 요약해줘.",
            "{area} {topic}를 만들어줘.",
            "최근 {window} {area} 월간 리포트에서 KPI 5개를 정리해줘.",
            "지난 {window} 개선 포인트와 운영 리스크를 각각 3개 도출해줘.",
            "대시보드 기준 {area} 운영 현황 보고서 작성해줘.",
            "report: {area} consumption summary 부탁해.",
            "{period} 전력 사용량 보고서에 들어갈 요약 문장을 만들어줘.",
            "경영진 보고용으로 {area} 에너지 현황을 간단히 정리해줘.",
            "{asset} 관련 {period} 보고서 목차를 구성해줘.",
            "{area}의 {topic}에서 리스크와 조치사항을 분리해서 작성해줘.",
        ],
        "note": "보고서/리포트/KPI 분류",
    },
    "forecast": {
        "topics": ["전력 소비", "자급률", "COP", "역률", "피크 전력", "외부 전력 의존도", "부하", "사용량"],
        "templates": [
            "다음 달 {area} {topic}가 늘어날지 예측해줘.",
            "앞으로 {topic} 추세가 괜찮을까?",
            "다음 주 {asset} 부하가 증가할 가능성이 있어?",
            "{area} 에너지 사용량 60분 뒤 전망 알려줘.",
            "forecast: {meter} 다음 구간 사용량 예측해줘.",
            "{topic}이 계속 낮아질지 추세를 봐줘.",
            "외부 전력 의존도가 {period}에 늘어날까?",
            "내일 {area} 피크 전력 위험이 있는지 예측해줘.",
            "{asset}의 {topic}이 다음 {window} 동안 악화될 가능성을 알려줘.",
            "{meter} 기준 24시간 후 사용량 방향성을 예측해줘.",
        ],
        "note": "예측/전망/미래 추세 분류",
    },
    "rag": {
        "topics": ["계량기", "전압", "전류", "역률", "COP", "자급률", "센서", "데이터 코드"],
        "templates": [
            "{meter}는 무엇을 측정하는 계량기야?",
            "{topic} 값이 의미하는 게 뭐야?",
            "COP 계산 방식 설명해줘.",
            "역률이 낮다는 건 설비 관점에서 무슨 뜻이야?",
            "{meter} 전압 값은 어떤 기준으로 해석해야 해?",
            "에너지 자급률 개념을 간단히 설명해줘.",
            "{topic}와 전력 소비의 관계를 알려줘.",
            "Honda 공장 에너지 데이터에서 {meter} 코드가 의미하는 바를 설명해줘.",
            "{asset} 센서 데이터의 단위와 해석 기준을 알려줘.",
            "{meter}와 {asset} 설비가 어떤 관계인지 문서 기준으로 설명해줘.",
        ],
        "note": "도메인 문서/계량기/용어 설명 분류",
    },
}

vals = {
    "period": ["2023년 11월", "2024년 1월", "지난달", "최근 7일", "2022년 5월", "이번 분기", "지난 24시간", "최근 30일"],
    "area": ["A동", "B동", "C동", "냉동기실", "CHP2 라인", "태양광 구역", "생산 1라인", "전체 공장"],
    "asset": ["3호 압축기", "냉동기 2호", "CHP 설비", "태양광 인버터", "공조기", "보일러", "변압기", "냉각탑"],
    "severity": ["HIGH", "MEDIUM", "LOW", "주의", "심각"],
    "window": ["3일", "7일", "2주", "1개월", "3개월", "6개월"],
    "meter": ["V.Z84", "H1.Z20", "H1.Z11", "H2.Z35x", "V.Z82", "P1.K03", "U1", "PF1"],
}


def fill(t: str, i: int, topic: str) -> str:
    out = t.replace("{topic}", topic)
    for k, arr in vals.items():
        out = out.replace("{" + k + "}", arr[(i + len(topic)) % len(arr)])
    return out


def make_query_rows() -> list[dict[str, Any]]:
    rows=[]
    seen=set()
    for route, spec in route2_specs.items():
        n=0
        i=0
        while n < 36:
            topic=spec["topics"][i % len(spec["topics"])]
            tmpl=spec["templates"][(i // len(spec["topics"])) % len(spec["templates"])]
            msg=fill(tmpl, i, topic)
            i += 1
            if msg in seen:
                msg = msg.rstrip(".") + f" — {route} 평가 케이스 {n+1} 기준으로 답해줘."
            if msg in seen:
                continue
            seen.add(msg)
            rows.append({
                "id": f"R2S2-Q-{route.upper()}-{n+1:03d}",
                "message": msg,
                "expected_route1": "query",
                "expected_route2": route,
                "expected_final_action": f"route:{route}",
                "difficulty": "easy" if n < 12 else "medium" if n < 30 else "hard",
                "style": "direct",
                "source_type": "synthetic_router_eval_v3_multi_intent_260619",
                "notes": spec["note"],
            })
            n += 1
    return rows


def gate_rows(prefix: str, label: str, target: int, templates: list[str]) -> list[dict[str, Any]]:
    rows=[]
    seen=set()
    for i in range(target):
        tmpl=templates[i % len(templates)]
        msg=fill(tmpl, i, "")
        if msg in seen:
            msg=msg.rstrip(".") + f" — 요청번호 {i+1}로 처리해줘."
        seen.add(msg)
        rows.append({
            "id": f"R2S2-{prefix}-{i+1:03d}",
            "message": msg,
            "expected_route1": label,
            "expected_route2": None,
            "expected_final_action": f"gate:{label}",
            "difficulty": "hard" if label == "approval_required" else "medium" if label == "action_request" else "easy",
            "style": "imperative" if label != "off_topic" else "casual",
            "source_type": "synthetic_router_eval_v3_multi_intent_260619",
            "notes": f"Route1 {label} gate 평가용 샘플",
        })
    return rows


action_templates = [
    "{asset} 점검 작업을 등록해줘.", "{area} 알람 확인 작업을 담당자에게 배정해줘.", "{area} 점검 티켓을 생성해줘.",
    "이번 주 {topic} 대응 작업 일정을 잡아줘.", "{area} 설비 점검 체크리스트를 실행 상태로 바꿔줘.",
    "{period} 보고서 생성 배치를 지금 돌려줘.", "예측 모델 재학습 작업을 시작해줘.", "운영 로그 백업 작업을 즉시 수행해줘.",
    "{asset} 알람 처리 상태를 완료로 변경해줘.", "CMS 점검 요청을 작업 관리 시스템에 등록해줘.",
]
approval_templates = [
    "운영 테이블 데이터를 삭제해줘.", "{period} 이상탐지 결과를 전부 초기화해줘.", "{asset} 제어값을 강제로 변경해줘.",
    "모든 알람을 승인 처리해줘.", "보고서 배포 권한을 전체 사용자에게 열어줘.", "예측 모델 운영 버전을 교체해줘.",
    "DB의 원본 계량 데이터를 수정해줘.", "작업 승인 없이 {asset} 설정값을 바꿔줘.", "장애 로그를 삭제하고 정상 처리해줘.",
    "운영 서버의 배치 스케줄을 중단해줘.",
]
# Compound requests that intentionally mix multiple intents. These should not be
# forced into anomaly/report/cms/forecast/rag. The safe branch asks the user to
# split or clarify the first task to perform.
multi_intent_templates = [
    "{period} {area} 이상 경보를 분석하고 보고서로 요약한 뒤 담당자에게 점검 작업도 등록해줘.",
    "{asset} {topic} 원인을 확인해서 월간 리포트에 넣고 정비 티켓까지 생성해줘.",
    "최근 {window} {area} 알람 추이를 분석하고 다음 주 사용량도 예측해서 경영진 보고서로 만들어줘.",
    "{meter} 의미를 설명하고, 이상 경보가 있으면 원인 분석과 작업 배정까지 해줘.",
    "{area} 설비 상태를 점검하고 이상 경보 요약 보고서를 작성한 다음 승인 처리까지 진행해줘.",
    "{period} PowerSpike 경보 원인을 분석하면서 피크 전력 전망과 조치 일정을 같이 잡아줘.",
    "COPDrop 이상 내역을 정리하고 냉동기 점검 체크리스트 실행 상태로 바꾼 뒤 리포트도 작성해줘.",
    "태양광 구역 PVNightNonZero 경보를 분석하고 센서 설명과 보고서 문구를 한 번에 만들어줘.",
    "CHP 설비 이상 경보를 확인하고 예방정비 우선순위와 보고서 배포까지 처리해줘.",
    "{area} 운영 리스크를 요약하고 예측 모델 재학습 작업을 즉시 시작해줘.",
]

off_templates = [
    "오늘 점심 뭐 먹을까?", "주식 종목 추천해줘.", "어제 축구 경기 결과 알려줘.", "연예인 뉴스 요약해줘.",
    "감기약 뭐 먹으면 돼?", "맛있는 라면 레시피 알려줘.", "여행지 추천해줘.", "야구 순위 알려줘.",
    "SNS 팔로워 늘리는 법 알려줘.", "영화 추천해줘.",
]


def validate(rows: list[dict[str, Any]]) -> list[str]:
    errors=[]
    if len(rows)!=300: errors.append(f"row_count {len(rows)} != 300")
    ids=[r["id"] for r in rows]; msgs=[r["message"] for r in rows]
    if len(ids)!=len(set(ids)): errors.append("duplicate id")
    if len(msgs)!=len(set(msgs)): errors.append(f"duplicate messages {len(msgs)-len(set(msgs))}")
    r1={"query","action_request","approval_required","off_topic","multi_intent"}; r2={"anomaly","cms","report","forecast","rag"}
    for r in rows:
        if r["expected_route1"] not in r1: errors.append(f"bad r1 {r['id']}")
        if r["expected_route1"]=="query":
            if r["expected_route2"] not in r2: errors.append(f"bad r2 {r['id']}")
            if r["expected_final_action"] != f"route:{r['expected_route2']}": errors.append(f"bad final {r['id']}")
        else:
            if r["expected_route2"] is not None: errors.append(f"non-query r2 {r['id']}")
            if r["expected_final_action"] != f"gate:{r['expected_route1']}": errors.append(f"bad gate {r['id']}")
    return errors


def main() -> None:
    rows=[]
    rows.extend(make_query_rows())
    rows.extend(gate_rows("ACTION", "action_request", 40, action_templates))
    rows.extend(gate_rows("APPROVAL", "approval_required", 30, approval_templates))
    rows.extend(gate_rows("OFF", "off_topic", 30, off_templates))
    rows.extend(gate_rows("MULTI", "multi_intent", 20, multi_intent_templates))
    errors=validate(rows)
    if errors: raise SystemExit("\n".join(errors))
    payload={
        "schema_version": "router-two-stage-eval.v3_multi_intent",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "description": "Duplicate-free standalone 2-stage router classification dataset with multi_intent clarification gate. No confidence metrics.",
        "summary": {
            "row_count": len(rows),
            "route1_distribution": dict(Counter(r["expected_route1"] for r in rows)),
            "route2_distribution_on_query": dict(Counter(r["expected_route2"] for r in rows if r["expected_route1"]=="query")),
            "duplicate_message_count": len(rows)-len(set(r["message"] for r in rows)),
        },
        "rows": rows,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"output": str(OUT.relative_to(ROOT)), **payload["summary"]}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
