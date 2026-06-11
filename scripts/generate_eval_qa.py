#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_eval_qa.py

Honda EMS / TTF-FMS evidence_answer 전용 Eval QA 생성기

목적
- Expert QA 100개, Chat QA 100개는 학습용으로 사용
- 이 스크립트는 학습에 넣지 않는 평가용 Eval QA를 생성
- 평가셋은 정답이 고정되도록 기간/설비/지표/이상유형을 명확히 포함
- 구성: anomaly 40개, cms 30개, report 30개 = 총 100개

출력 형식은 기존 공정위 공모전 eval_dataset 형식을 EMS 버전으로 단순화한 구조입니다.

예시 출력:
{
  "id": "EMS-EVAL-A-001",
  "query": "2023-12-01부터 2023-12-31까지 PowerSpike 이상 후보는 몇 건 발생했나요?",
  "category": "anomaly",
  "source_doc": "anomaly_results",
  "answer_chunks": [
    {
      "chunk_id": "anomaly_results:2023-12:PowerSpike",
      "answer_reason": "기간 내 PowerSpike 이상 후보 건수와 등급 분포 산출 근거"
    }
  ],
  "reference_context": "...",
  "reference_answer": "..."
}

사용법:
  python scripts/generate_eval_qa.py

환경변수:
  DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DB
또는
  DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

선택 환경변수:
  DB_SCHEMA=ems             # 기본값 ems, 없으면 public도 자동 탐색
  EVAL_OUTPUT_DIR=scripts/outputs/eval_qa
  EVAL_TARGET_TOTAL=100
  EVAL_ANOMALY_COUNT=40
  EVAL_CMS_COUNT=30
  EVAL_REPORT_COUNT=30
"""

from __future__ import annotations

import os
import re
import json
import math
import argparse
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from dotenv import load_dotenv

load_dotenv()
try:
    import psycopg2
    import psycopg2.extras
except ImportError as exc:
    raise SystemExit(
        "psycopg2가 필요합니다. 설치: pip install psycopg2-binary"
    ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_COUNTS = {
    "anomaly": int(os.getenv("EVAL_ANOMALY_COUNT", "40")),
    "cms": int(os.getenv("EVAL_CMS_COUNT", "30")),
    "report": int(os.getenv("EVAL_REPORT_COUNT", "30")),
}

OUTPUT_DIR = Path(os.getenv("EVAL_OUTPUT_DIR", "scripts/outputs/eval_qa"))
PREFERRED_SCHEMA = os.getenv("DB_SCHEMA", "ems")


ANOMALY_TYPE_HINTS = [
    "PowerSpike",
    "CHPOutage",
    "COPDrop",
    "PVNightNonZero",
    "NightConsumption",
    "ResidualSpike",
]

REPORT_METRICS = [
    ("self_sufficiency_pct", "자급률", "%", "2023년 {period} 자급률은 얼마인가요?"),
    ("grid_dependency_pct", "외부 계통 전력 의존도", "%", "2023년 {period} 외부 계통 전력 의존도는 몇 %인가요?"),
    ("total_consumption_kwh", "총 전력 소비량", "kWh", "2023년 {period} 총 전력 소비량은 몇 kWh인가요?"),
    ("avg_cop", "평균 COP", "", "2023년 {period} 평균 COP는 얼마인가요?"),
    ("chp_kwh", "CHP 전력 생산량", "kWh", "2023년 {period} CHP 전력 생산량은 몇 kWh인가요?"),
    ("pv_kwh", "태양광 발전량", "kWh", "2023년 {period} 태양광 발전량은 몇 kWh인가요?"),
]


# ─────────────────────────────────────────────────────────────────────────────
# DB 유틸
# ─────────────────────────────────────────────────────────────────────────────

def get_conn():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)

    required = ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise SystemExit(
            "DB 접속 정보가 없습니다. DATABASE_URL 또는 DB_HOST/DB_USER/DB_PASSWORD/DB_NAME을 설정하세요. "
            f"누락: {missing}"
        )

    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        dbname=os.getenv("DB_NAME"),
    )


def fetch_all(conn, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def table_exists(conn, schema: str, table: str) -> bool:
    rows = fetch_all(
        conn,
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
        LIMIT 1
        """,
        (schema, table),
    )
    return bool(rows)


def find_table(conn, table: str) -> Tuple[str, str]:
    candidates = [PREFERRED_SCHEMA, "public"]
    seen = set()
    for schema in candidates:
        if not schema or schema in seen:
            continue
        seen.add(schema)
        if table_exists(conn, schema, table):
            return schema, table
    raise RuntimeError(f"테이블을 찾지 못했습니다: {table} (schema 후보: {candidates})")


def get_columns(conn, schema: str, table: str) -> List[str]:
    rows = fetch_all(
        conn,
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    return [r["column_name"] for r in rows]


def pick_col(cols: Sequence[str], candidates: Sequence[str], required: bool = True) -> Optional[str]:
    lower_map = {c.lower(): c for c in cols}
    for c in candidates:
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    if required:
        raise RuntimeError(f"필수 컬럼을 찾지 못했습니다. candidates={candidates}, cols={list(cols)}")
    return None


def qname(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'


# ─────────────────────────────────────────────────────────────────────────────
# 포맷 유틸
# ─────────────────────────────────────────────────────────────────────────────

def fmt_num(v: Any, digits: int = 2) -> str:
    if v is None:
        return "확인 불가"
    try:
        x = float(v)
        if math.isnan(x):
            return "확인 불가"
        if abs(x - round(x)) < 0.005:
            return f"{int(round(x)):,}"
        return f"{x:,.{digits}f}".rstrip("0").rstrip(".")
    except Exception:
        return str(v)


def fmt_pct(v: Any) -> str:
    return f"{fmt_num(v, 2)}%"


def month_label(month_value: Any) -> str:
    s = str(month_value)
    # 2023-12-01, 2023-12, datetime 등 대응
    m = re.search(r"(\d{4})[-./년\s]+(\d{1,2})", s)
    if m:
        return f"{m.group(1)}년 {int(m.group(2))}월"
    return s


def month_start_end(month_value: Any) -> Tuple[str, str, str]:
    s = str(month_value)
    m = re.search(r"(\d{4})[-./년\s]+(\d{1,2})", s)
    if not m:
        return s, s, s
    y, mo = int(m.group(1)), int(m.group(2))
    if mo == 12:
        next_y, next_m = y + 1, 1
    else:
        next_y, next_m = y, mo + 1
    start = date(y, mo, 1)
    next_start = date(next_y, next_m, 1)
    end = date.fromordinal(next_start.toordinal() - 1)
    return start.isoformat(), end.isoformat(), f"{y}년 {mo}월"


def normalize_month_expr(col: str) -> str:
    return f"date_trunc('month', {col})::date"


def safe_text(v: Any) -> str:
    return "" if v is None else str(v)


def severity_counts_text(row: Dict[str, Any]) -> str:
    parts = []
    for sev in ["HIGH", "MEDIUM", "LOW"]:
        key = f"{sev.lower()}_cnt"
        if row.get(key) is not None and int(row.get(key, 0)) > 0:
            parts.append(f"{sev} {int(row[key])}건")
    return ", ".join(parts) if parts else "등급 정보 없음"


def make_item(
    item_id: str,
    query: str,
    category: str,
    source_doc: str,
    answer_chunks: List[Dict[str, str]],
    reference_context: str,
    reference_answer: str,
    eval_type: str = "evidence_answer",
    difficulty: str = "medium",
) -> Dict[str, Any]:
    return {
        "id": item_id,
        "query": query,
        "category": category,
        "eval_type": eval_type,
        "difficulty": difficulty,
        "source_doc": source_doc,
        "answer_chunks": answer_chunks,
        "reference_context": reference_context,
        "reference_answer": reference_answer,
    }


# ─────────────────────────────────────────────────────────────────────────────
# anomaly eval 생성
# ─────────────────────────────────────────────────────────────────────────────

def generate_anomaly_eval(conn, target_count: int) -> List[Dict[str, Any]]:
    schema, table = find_table(conn, "anomaly_results")
    cols = get_columns(conn, schema, table)

    ts_col = pick_col(cols, ["timestamp", "ts", "time", "datetime", "detected_at", "created_at", "event_time", "date"])
    type_col = pick_col(cols, ["anomaly_type", "type", "error_type", "label", "category"])
    sev_col = pick_col(cols, ["severity", "level", "grade"], required=False)
    residual_col = pick_col(cols, ["residual_w", "residual", "score", "anomaly_score"], required=False)

    table_name = qname(schema, table)
    month_expr = normalize_month_expr(ts_col)

    sev_select = f"""
        SUM(CASE WHEN UPPER({sev_col})='HIGH' THEN 1 ELSE 0 END)::int AS high_cnt,
        SUM(CASE WHEN UPPER({sev_col})='MEDIUM' THEN 1 ELSE 0 END)::int AS medium_cnt,
        SUM(CASE WHEN UPPER({sev_col})='LOW' THEN 1 ELSE 0 END)::int AS low_cnt
    """ if sev_col else """
        NULL::int AS high_cnt,
        NULL::int AS medium_cnt,
        NULL::int AS low_cnt
    """

    residual_select = f"MAX(ABS({residual_col})) AS max_residual" if residual_col else "NULL::float AS max_residual"

    by_type_month = fetch_all(
        conn,
        f"""
        SELECT
            {month_expr} AS month,
            {type_col} AS anomaly_type,
            COUNT(*)::int AS total_cnt,
            {sev_select},
            MIN({ts_col}) AS first_time,
            MAX({ts_col}) AS last_time,
            {residual_select}
        FROM {table_name}
        WHERE {ts_col} IS NOT NULL AND {type_col} IS NOT NULL
        GROUP BY 1, 2
        ORDER BY total_cnt DESC, month DESC
        LIMIT 200
        """
    )

    by_month = fetch_all(
        conn,
        f"""
        SELECT
            {month_expr} AS month,
            COUNT(*)::int AS total_cnt,
            {sev_select},
            MIN({ts_col}) AS first_time,
            MAX({ts_col}) AS last_time
        FROM {table_name}
        WHERE {ts_col} IS NOT NULL
        GROUP BY 1
        ORDER BY total_cnt DESC, month DESC
        LIMIT 80
        """
    )

    by_high = []
    if sev_col:
        by_high = fetch_all(
            conn,
            f"""
            SELECT
                {month_expr} AS month,
                COUNT(*)::int AS total_cnt,
                SUM(CASE WHEN UPPER({sev_col})='HIGH' THEN 1 ELSE 0 END)::int AS high_cnt,
                MIN({ts_col}) AS first_time,
                MAX({ts_col}) AS last_time
            FROM {table_name}
            WHERE {ts_col} IS NOT NULL
            GROUP BY 1
            HAVING SUM(CASE WHEN UPPER({sev_col})='HIGH' THEN 1 ELSE 0 END) > 0
            ORDER BY high_cnt DESC, month DESC
            LIMIT 80
            """
        )

    items: List[Dict[str, Any]] = []

    def add_type_count(row: Dict[str, Any]):
        if len(items) >= target_count:
            return
        start, end, label = month_start_end(row["month"])
        atype = row["anomaly_type"]
        total = int(row["total_cnt"])
        sev_text = severity_counts_text(row)
        max_res = row.get("max_residual")

        q = f"{start}부터 {end}까지 {atype} 이상 후보는 몇 건 발생했나요?"
        ctx = (
            f"source_table=anomaly_results\n"
            f"period={start}~{end}\n"
            f"anomaly_type={atype}\n"
            f"total_count={total}\n"
            f"severity_counts={sev_text}\n"
            f"first_time={row.get('first_time')}\n"
            f"last_time={row.get('last_time')}\n"
            f"max_abs_residual={fmt_num(max_res) if max_res is not None else 'N/A'}"
        )
        ans = f"{label} 동안 {atype} 이상 후보는 총 {total}건 발생했습니다."
        if sev_text != "등급 정보 없음":
            ans += f" 등급 분포는 {sev_text}입니다."
        ans += " 해당 기간의 설비 운전 상태와 관련 이벤트 확인이 권장됩니다."

        items.append(
            make_item(
                f"EMS-EVAL-A-{len(items)+1:03d}",
                q,
                "anomaly",
                "anomaly_results",
                [{"chunk_id": f"anomaly_results:{start}:{end}:{atype}", "answer_reason": f"{atype} 기간별 이상 후보 건수 및 등급 분포"}],
                ctx,
                ans,
                difficulty="medium",
            )
        )

    def add_month_summary(row: Dict[str, Any]):
        if len(items) >= target_count:
            return
        start, end, label = month_start_end(row["month"])
        total = int(row["total_cnt"])
        sev_text = severity_counts_text(row)

        q = f"{start}부터 {end}까지 전체 이상 후보는 몇 건 발생했나요?"
        ctx = (
            f"source_table=anomaly_results\n"
            f"period={start}~{end}\n"
            f"total_count={total}\n"
            f"severity_counts={sev_text}\n"
            f"first_time={row.get('first_time')}\n"
            f"last_time={row.get('last_time')}"
        )
        ans = f"{label} 동안 전체 이상 후보는 총 {total}건 발생했습니다."
        if sev_text != "등급 정보 없음":
            ans += f" 등급 분포는 {sev_text}입니다."
        ans += " 상세 유형별 원인은 anomaly 상세 결과에서 확인하는 것이 좋습니다."

        items.append(
            make_item(
                f"EMS-EVAL-A-{len(items)+1:03d}",
                q,
                "anomaly",
                "anomaly_results",
                [{"chunk_id": f"anomaly_results:{start}:{end}:all", "answer_reason": "기간별 전체 이상 후보 건수와 등급 분포"}],
                ctx,
                ans,
                difficulty="easy",
            )
        )

    def add_high_count(row: Dict[str, Any]):
        if len(items) >= target_count:
            return
        start, end, label = month_start_end(row["month"])
        high_cnt = int(row["high_cnt"])

        q = f"{start}부터 {end}까지 HIGH 등급 이상 후보는 몇 건 발생했나요?"
        ctx = (
            f"source_table=anomaly_results\n"
            f"period={start}~{end}\n"
            f"severity=HIGH\n"
            f"high_count={high_cnt}\n"
            f"first_time={row.get('first_time')}\n"
            f"last_time={row.get('last_time')}"
        )
        ans = f"{label} 동안 HIGH 등급 이상 후보는 총 {high_cnt}건 발생했습니다. HIGH 등급은 우선 확인 대상이므로 관련 설비의 운전 상태 점검이 권장됩니다."

        items.append(
            make_item(
                f"EMS-EVAL-A-{len(items)+1:03d}",
                q,
                "anomaly",
                "anomaly_results",
                [{"chunk_id": f"anomaly_results:{start}:{end}:HIGH", "answer_reason": "기간별 HIGH 등급 이상 후보 건수"}],
                ctx,
                ans,
                difficulty="hard",
            )
        )

    # 1순위: 유형+월별 건수
    for row in by_type_month:
        add_type_count(row)
        if len(items) >= target_count:
            break

    # 2순위: 월별 전체 건수
    for row in by_month:
        add_month_summary(row)
        if len(items) >= target_count:
            break

    # 3순위: HIGH 건수
    for row in by_high:
        add_high_count(row)
        if len(items) >= target_count:
            break

    return items[:target_count]


# ─────────────────────────────────────────────────────────────────────────────
# cms eval 생성
# ─────────────────────────────────────────────────────────────────────────────

def parse_health_from_text(text: str) -> Optional[int]:
    m = re.search(r"헬스\s*([0-9]{1,3})", text)
    if m:
        return int(m.group(1))
    m = re.search(r"health[_\s-]*score[=: ]+([0-9]{1,3})", text, re.I)
    if m:
        return int(m.group(1))
    return None


def parse_recent_anomaly_count(text: str) -> Optional[int]:
    m = re.search(r"최근\s*이상\s*([0-9,]+)\s*건", text)
    if m:
        return int(m.group(1).replace(",", ""))
    m = re.search(r"([0-9,]+)\s*건", text)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def generate_cms_eval(conn, target_count: int) -> List[Dict[str, Any]]:
    schema, table = find_table(conn, "work_orders")
    cols = get_columns(conn, schema, table)
    table_name = qname(schema, table)

    equip_col = pick_col(cols, ["equipment_id", "equipment", "asset_id", "asset", "facility_id"], required=False)
    title_col = pick_col(cols, ["title", "summary", "name", "subject"], required=False)
    status_col = pick_col(cols, ["status", "state"], required=False)
    created_col = pick_col(cols, ["created_at", "created", "timestamp", "date"], required=False)
    health_col = pick_col(cols, ["health_score", "score"], required=False)

    # work_orders는 health_score 컬럼이 없고,
    # "헬스 97 · 최근 이상 5건" 같은 정보가 title이 아닌 다른 텍스트 컬럼에 들어갈 수 있습니다.
    # 따라서 SELECT *로 가져온 뒤 모든 문자열 컬럼을 합쳐서 헬스 스코어/최근 이상 건수를 파싱합니다.
    order_expr = created_col if created_col else "1"
    rows = fetch_all(
        conn,
        f"""
        SELECT *
        FROM {table_name}
        ORDER BY {order_expr} DESC
        LIMIT 300
        """
    )

    normalized = []
    for r in rows:
        equip = safe_text(r.get(equip_col)) if equip_col else ""
        status = safe_text(r.get(status_col)) if status_col else ""
        created = safe_text(r.get(created_col)) if created_col else ""

        # 화면에 보여줄 제목은 title 컬럼을 우선 사용합니다.
        title = safe_text(r.get(title_col)) if title_col else ""

        # 헬스/최근 이상은 특정 컬럼 하나에 고정하지 않고 모든 문자열 컬럼에서 탐색합니다.
        text_values = []
        for v in r.values():
            if isinstance(v, str) and v.strip():
                text_values.append(v.strip())
        combined_text = " | ".join(text_values)

        health = parse_health_from_text(combined_text)
        recent_cnt = parse_recent_anomaly_count(combined_text)

        normalized.append({
            "equipment": equip or "unknown",
            "title": title or combined_text[:80],
            "status": status,
            "created_at": created,
            "health_score": health,
            "recent_anomaly_count": recent_cnt,
        })

    # equipment별 최신 하나 우선
    seen = set()
    latest_by_equipment = []
    for r in normalized:
        key = r["equipment"]
        if key not in seen:
            latest_by_equipment.append(r)
            seen.add(key)

    # health 낮은 순 우선
    risk_sorted = sorted(
        normalized,
        key=lambda x: (999 if x["health_score"] is None else int(x["health_score"]), -(x["recent_anomaly_count"] or 0)),
    )

    items: List[Dict[str, Any]] = []

    def equipment_label(e: str) -> str:
        mapping = {
            "chp": "CHP",
            "pv": "태양광",
            "cooling": "냉방",
            "grid": "계통/수전",
        }
        return mapping.get(e.lower(), e)

    def equip_phrase(equip: str) -> str:
        # label이 이미 "설비"로 끝나는 경우 중복 방지
        return equip if equip.endswith("설비") else f"{equip} 설비"

    def add_health(row: Dict[str, Any]):
        if len(items) >= target_count:
            return
        equip = equipment_label(row["equipment"])
        score = row["health_score"]
        recent = row["recent_anomaly_count"]
        subject = equip_phrase(equip)
        q = f"{subject}의 헬스 스코어와 점검 필요성을 알려주세요."
        ctx = (
            f"source_table=work_orders\n"
            f"equipment_id={row['equipment']}\n"
            f"title={row['title']}\n"
            f"status={row['status']}\n"
            f"created_at={row['created_at']}\n"
            f"health_score={score}\n"
            f"recent_anomaly_count={recent}"
        )
        if score is None:
            ans = f"{subject}의 헬스 스코어는 제공된 근거만으로 확인하기 어렵습니다."
        else:
            ans = f"{subject}의 헬스 스코어는 {int(score)}입니다."
            if int(score) < 70:
                ans += " 주의가 필요한 수준이므로 운전 상태 점검이 권장됩니다."
            else:
                ans += " 다만 최근 이상 이력이 있다면 관련 항목을 함께 확인하는 것이 좋습니다."
        if recent is not None:
            ans += f" 최근 이상은 {recent}건으로 기록되어 있습니다."

        items.append(
            make_item(
                f"EMS-EVAL-C-{len(items)+1:03d}",
                q,
                "cms",
                "work_orders",
                [{"chunk_id": f"work_orders:{row['equipment']}", "answer_reason": "설비별 헬스 스코어와 최근 이상 건수"}],
                ctx,
                ans,
                difficulty="medium",
            )
        )

    def add_priority(row: Dict[str, Any]):
        if len(items) >= target_count:
            return
        equip = equipment_label(row["equipment"])
        score = row["health_score"]
        recent = row["recent_anomaly_count"]
        q = f"현재 점검 우선순위가 높은 설비는 무엇인가요?"
        ctx = (
            f"source_table=work_orders\n"
            f"selected_equipment={row['equipment']}\n"
            f"title={row['title']}\n"
            f"health_score={score}\n"
            f"recent_anomaly_count={recent}"
        )
        subject = equip_phrase(equip)
        ans = f"제공된 작업지시 기준으로는 {subject}를 우선 확인하는 것이 좋습니다."
        if score is not None:
            ans += f" 헬스 스코어는 {int(score)}입니다."
        if recent is not None:
            ans += f" 최근 이상은 {recent}건입니다."
        ans += " 실제 우선순위는 현장 운전 상태와 함께 확인하는 것이 좋습니다."

        items.append(
            make_item(
                f"EMS-EVAL-C-{len(items)+1:03d}",
                q,
                "cms",
                "work_orders",
                [{"chunk_id": f"work_orders:priority:{row['equipment']}", "answer_reason": "헬스 스코어와 최근 이상 건수 기반 점검 우선순위"}],
                ctx,
                ans,
                difficulty="hard",
            )
        )

    def add_work_order(row: Dict[str, Any]):
        if len(items) >= target_count:
            return
        equip = equipment_label(row["equipment"])
        subject = equip_phrase(equip)
        q = f"{subject}에 작업지시가 생성된 이유는 무엇인가요?"
        ctx = (
            f"source_table=work_orders\n"
            f"equipment_id={row['equipment']}\n"
            f"title={row['title']}\n"
            f"status={row['status']}\n"
            f"created_at={row['created_at']}\n"
            f"health_score={row['health_score']}\n"
            f"recent_anomaly_count={row['recent_anomaly_count']}"
        )
        ans = f"{subject}의 작업지시는 제공된 작업지시 정보에 근거합니다."
        if row["health_score"] is not None:
            ans += f" 헬스 스코어는 {int(row['health_score'])}입니다."
        if row["recent_anomaly_count"] is not None:
            ans += f" 최근 이상 {row['recent_anomaly_count']}건이 기록되어 점검 검토가 필요합니다."
        else:
            ans += " 상세 원인은 작업지시 제목과 관련 이상 이력을 함께 확인해야 합니다."

        items.append(
            make_item(
                f"EMS-EVAL-C-{len(items)+1:03d}",
                q,
                "cms",
                "work_orders",
                [{"chunk_id": f"work_orders:reason:{row['equipment']}", "answer_reason": "작업지시 생성 사유와 관련 설비 상태"}],
                ctx,
                ans,
                difficulty="medium",
            )
        )

    for row in latest_by_equipment:
        add_health(row)
        if len(items) >= target_count:
            return items[:target_count]

    for row in risk_sorted:
        add_priority(row)
        if len(items) >= target_count:
            return items[:target_count]

    for row in normalized:
        add_work_order(row)
        if len(items) >= target_count:
            return items[:target_count]

    # work_orders 원천 row가 적은 경우, 같은 설비를 다른 평가 질문 유형으로 변형해 CMS 30개를 채웁니다.
    # Eval 목적은 "DB 근거 기반으로 정확히 답하는지" 확인하는 것이므로,
    # 같은 근거에서 헬스 스코어/최근 이상 건수/점검 필요성/상태 요약/작업지시 상태를 분리 평가합니다.
    if len(items) < target_count and normalized:
        base_rows = normalized
        variant_idx = 0

        while len(items) < target_count:
            row = base_rows[variant_idx % len(base_rows)]
            equip = equipment_label(row["equipment"])
            score = row["health_score"]
            recent = row["recent_anomaly_count"]
            status = row.get("status") or "확인 불가"

            variant_type = variant_idx % 5

            if variant_type == 0:
                subject = equip_phrase(equip)
                q = f"{subject}의 최근 이상 건수는 몇 건인가요?"
                if recent is not None:
                    ans = f"{subject}의 최근 이상 건수는 {recent}건입니다."
                else:
                    ans = f"{subject}의 최근 이상 건수는 제공된 근거만으로 확인하기 어렵습니다."
                reason = "설비별 최근 이상 건수"

            elif variant_type == 1:
                subject = equip_phrase(equip)
                q = f"{subject}는 점검이 필요한가요?"
                ans = f"{subject}는 제공된 작업지시 정보를 기준으로 점검 검토가 필요합니다."
                if score is not None:
                    ans += f" 헬스 스코어는 {int(score)}입니다."
                if recent is not None:
                    ans += f" 최근 이상은 {recent}건입니다."
                reason = "헬스 스코어와 최근 이상 건수 기반 점검 필요성"

            elif variant_type == 2:
                subject = equip_phrase(equip)
                q = f"{subject} 상태를 요약해 주세요."
                ans = f"{subject} 상태는 작업지시 정보를 기준으로 확인할 수 있습니다."
                if score is not None:
                    ans += f" 헬스 스코어는 {int(score)}입니다."
                if recent is not None:
                    ans += f" 최근 이상은 {recent}건입니다."
                reason = "설비 상태 요약"

            elif variant_type == 3:
                q = f"{equip} 작업지시 상태는 무엇인가요?"
                ans = f"{equip} 작업지시 상태는 {status}입니다."
                reason = "작업지시 상태"

            else:
                subject = equip_phrase(equip)
                q = f"{subject}의 헬스 스코어는 얼마인가요?"
                if score is not None:
                    ans = f"{subject}의 헬스 스코어는 {int(score)}입니다."
                else:
                    ans = f"{subject}의 헬스 스코어는 제공된 근거만으로 확인하기 어렵습니다."
                reason = "설비별 헬스 스코어"

            ctx = (
                f"source_table=work_orders\n"
                f"question_type=cms_variant\n"
                f"equipment_id={row['equipment']}\n"
                f"title={row['title']}\n"
                f"status={row['status']}\n"
                f"created_at={row['created_at']}\n"
                f"health_score={score}\n"
                f"recent_anomaly_count={recent}"
            )

            items.append(
                make_item(
                    f"EMS-EVAL-C-{len(items)+1:03d}",
                    q,
                    "cms",
                    "work_orders",
                    [
                        {
                            "chunk_id": f"work_orders:cms_variant:{row['equipment']}:{variant_idx}",
                            "answer_reason": reason,
                        }
                    ],
                    ctx,
                    ans,
                    difficulty="medium",
                )
            )

            variant_idx += 1

    return items[:target_count]


# ─────────────────────────────────────────────────────────────────────────────
# report eval 생성
# ─────────────────────────────────────────────────────────────────────────────

def generate_report_eval(conn, target_count: int) -> List[Dict[str, Any]]:
    """
    Report Eval 30개 생성

    구성:
    - 단일 KPI 조회형 15개
    - KPI 조합 요약형 10개
    - 전월 비교 판단형 5개

    목적:
    - 단순 DB 조회 정확도만 보지 않고,
      여러 KPI를 함께 요약하거나 전월 비교를 계산하는 능력까지 평가합니다.
    """
    schema, table = find_table(conn, "monthly_report")
    cols = get_columns(conn, schema, table)
    table_name = qname(schema, table)

    month_col = pick_col(cols, ["month", "report_month", "period", "date", "ym", "year_month"])
    available_metrics = [(c, label, unit, tmpl) for c, label, unit, tmpl in REPORT_METRICS if c in cols]

    if not available_metrics:
        raise RuntimeError(f"monthly_report에서 평가 가능한 KPI 컬럼을 찾지 못했습니다. cols={cols}")

    select_cols = [month_col] + [m[0] for m in available_metrics]
    rows = fetch_all(
        conn,
        f"""
        SELECT {", ".join(select_cols)}
        FROM {table_name}
        ORDER BY {month_col} DESC
        LIMIT 120
        """
    )

    if not rows:
        raise RuntimeError("monthly_report에 데이터가 없습니다.")

    items: List[Dict[str, Any]] = []

    # report 30 기준 기본 비율. target_count가 바뀌어도 비율 유지.
    single_target = min(target_count, max(1, round(target_count * 0.50)))
    multi_target = min(target_count - single_target, max(1, round(target_count * 0.33)))
    compare_target = target_count - single_target - multi_target

    # target_count=30일 때 15/10/5로 강제 보정
    if target_count == 30:
        single_target, multi_target, compare_target = 15, 10, 5

    single_items: List[Dict[str, Any]] = []
    multi_items: List[Dict[str, Any]] = []
    compare_items: List[Dict[str, Any]] = []

    def value_text(value: Any, unit: str) -> str:
        if unit == "%":
            return fmt_pct(value)
        if unit:
            return f"{fmt_num(value, 2)} {unit}"
        return fmt_num(value, 2)

    def add_single_metric(row: Dict[str, Any], metric_col: str, label: str, unit: str, template: str):
        if len(single_items) >= single_target:
            return
        label_month = month_label(row[month_col])
        q = template.replace("2023년 {period}", "{period}").format(period=label_month)
        value = row.get(metric_col)
        if value is None:
            return

        vtxt = value_text(value, unit)
        ctx = (
            f"source_table=monthly_report\n"
            f"question_type=single_metric\n"
            f"period={label_month}\n"
            f"metric={metric_col}\n"
            f"value={value}\n"
            f"unit={unit}"
        )
        ans = f"{label_month}의 {label}은 {vtxt}입니다."

        single_items.append(
            make_item(
                f"EMS-EVAL-R-{len(single_items)+1:03d}",
                q,
                "report",
                "monthly_report",
                [{"chunk_id": f"monthly_report:{label_month}:{metric_col}", "answer_reason": f"{label_month} {label} KPI 값"}],
                ctx,
                ans,
                difficulty="easy",
            )
        )

    def get_metric_def(metric_col: str):
        for c, label, unit, tmpl in available_metrics:
            if c == metric_col:
                return c, label, unit, tmpl
        return None

    def add_multi_metric(row: Dict[str, Any], metric_cols: Sequence[str], q_template: str):
        if len(multi_items) >= multi_target:
            return

        defs = [get_metric_def(c) for c in metric_cols]
        defs = [d for d in defs if d is not None and row.get(d[0]) is not None]
        if len(defs) < 2:
            return

        label_month = month_label(row[month_col])
        metrics_context = []
        answer_parts = []
        chunk_items = []

        for metric_col, label, unit, _ in defs:
            value = row.get(metric_col)
            vtxt = value_text(value, unit)
            metrics_context.append(f"{metric_col}={value} ({label}, unit={unit})")
            answer_parts.append(f"{label}은 {vtxt}")
            chunk_items.append({
                "chunk_id": f"monthly_report:{label_month}:{metric_col}",
                "answer_reason": f"{label_month} {label} KPI 값"
            })

        q = q_template.format(period=label_month)
        ctx = (
            f"source_table=monthly_report\n"
            f"question_type=multi_metric_summary\n"
            f"period={label_month}\n"
            + "\n".join(metrics_context)
        )
        ans = f"{label_month} 기준으로 " + ", ".join(answer_parts) + "입니다."

        multi_items.append(
            make_item(
                f"EMS-EVAL-R-{single_target + len(multi_items)+1:03d}",
                q,
                "report",
                "monthly_report",
                chunk_items,
                ctx,
                ans,
                difficulty="medium",
            )
        )

    def as_month_key(row: Dict[str, Any]) -> Tuple[int, int]:
        s = str(row[month_col])
        m = re.search(r"(\d{4})[-./년\s]+(\d{1,2})", s)
        if m:
            return int(m.group(1)), int(m.group(2))
        # 파싱 실패 시 뒤로 보냄
        return 0, 0

    sorted_rows = sorted(rows, key=as_month_key, reverse=True)
    row_by_key = {as_month_key(r): r for r in sorted_rows if as_month_key(r) != (0, 0)}

    def prev_key(y: int, m: int) -> Tuple[int, int]:
        return (y - 1, 12) if m == 1 else (y, m - 1)

    def add_comparison(row: Dict[str, Any], metric_col: str):
        if len(compare_items) >= compare_target:
            return

        key = as_month_key(row)
        if key == (0, 0):
            return
        prev = row_by_key.get(prev_key(*key))
        if not prev:
            return

        metric_def = get_metric_def(metric_col)
        if not metric_def:
            return
        _, label, unit, _ = metric_def

        cur_val = row.get(metric_col)
        prev_val = prev.get(metric_col)
        if cur_val is None or prev_val is None:
            return

        try:
            cur_f = float(cur_val)
            prev_f = float(prev_val)
        except Exception:
            return

        label_month = month_label(row[month_col])
        prev_month = month_label(prev[month_col])
        diff = cur_f - prev_f
        direction = "증가" if diff > 0 else "감소" if diff < 0 else "동일"
        diff_txt = value_text(abs(diff), unit)

        q = f"{label_month}의 {label}은 전월보다 증가했나요, 감소했나요?"
        ctx = (
            f"source_table=monthly_report\n"
            f"question_type=month_over_month_comparison\n"
            f"metric={metric_col}\n"
            f"current_period={label_month}\n"
            f"current_value={cur_val}\n"
            f"previous_period={prev_month}\n"
            f"previous_value={prev_val}\n"
            f"diff={diff}\n"
            f"unit={unit}"
        )

        cur_txt = value_text(cur_val, unit)
        prev_txt = value_text(prev_val, unit)

        if direction == "동일":
            ans = f"{label_month}의 {label}은 {cur_txt}이고, {prev_month}도 {prev_txt}로 전월과 동일합니다."
        else:
            ans = f"{label_month}의 {label}은 {cur_txt}이고, {prev_month}의 {prev_txt}보다 {diff_txt} {direction}했습니다."

        compare_items.append(
            make_item(
                f"EMS-EVAL-R-{single_target + multi_target + len(compare_items)+1:03d}",
                q,
                "report",
                "monthly_report",
                [
                    {"chunk_id": f"monthly_report:{label_month}:{metric_col}", "answer_reason": f"현재 월 {label} KPI 값"},
                    {"chunk_id": f"monthly_report:{prev_month}:{metric_col}", "answer_reason": f"전월 {label} KPI 값"},
                ],
                ctx,
                ans,
                difficulty="hard",
            )
        )

    # 1) 단일 KPI 조회형 15개
    for row in sorted_rows:
        for metric_col, label, unit, template in available_metrics:
            add_single_metric(row, metric_col, label, unit, template)
            if len(single_items) >= single_target:
                break
        if len(single_items) >= single_target:
            break

    # 2) KPI 조합 요약형 10개
    multi_templates = [
        (["self_sufficiency_pct", "grid_dependency_pct"], "{period} 자급률과 외부 계통 전력 의존도를 함께 요약해 주세요."),
        (["total_consumption_kwh", "chp_kwh", "pv_kwh"], "{period} 전력 소비량과 자체 발전량을 함께 요약해 주세요."),
        (["avg_cop", "total_consumption_kwh"], "{period} 평균 COP와 총 전력 소비량을 함께 알려주세요."),
        (["chp_kwh", "pv_kwh"], "{period} CHP와 태양광 발전량을 비교해서 알려주세요."),
        (["self_sufficiency_pct", "total_consumption_kwh"], "{period} 자급률과 총 전력 소비량을 같이 알려주세요."),
    ]

    for row in sorted_rows:
        for metric_cols, q_template in multi_templates:
            add_multi_metric(row, metric_cols, q_template)
            if len(multi_items) >= multi_target:
                break
        if len(multi_items) >= multi_target:
            break

    # 3) 전월 비교 판단형 5개
    comparison_metric_priority = [
        "total_consumption_kwh",
        "self_sufficiency_pct",
        "grid_dependency_pct",
        "avg_cop",
        "chp_kwh",
        "pv_kwh",
    ]

    for row in sorted_rows:
        for metric_col in comparison_metric_priority:
            add_comparison(row, metric_col)
            if len(compare_items) >= compare_target:
                break
        if len(compare_items) >= compare_target:
            break

    # 데이터 부족 시 단일/조합으로 보충
    result = single_items + multi_items + compare_items

    if len(result) < target_count:
        for row in sorted_rows:
            for metric_col, label, unit, template in available_metrics:
                before = len(single_items)
                add_single_metric(row, metric_col, label, unit, template)
                if len(single_items) > before:
                    result = single_items + multi_items + compare_items
                if len(result) >= target_count:
                    break
            if len(result) >= target_count:
                break

    # ID 재정렬
    result = (single_items + multi_items + compare_items)[:target_count]
    for idx, item in enumerate(result, start=1):
        item["id"] = f"EMS-EVAL-R-{idx:03d}"

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 저장 및 검증
# ─────────────────────────────────────────────────────────────────────────────

def write_jsonl(path: Path, data: List[Dict[str, Any]]):
    with path.open("w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def validate_counts(data: List[Dict[str, Any]], expected: Dict[str, int]):
    counts = {}
    for row in data:
        counts[row["category"]] = counts.get(row["category"], 0) + 1

    errors = []
    for cat, exp in expected.items():
        act = counts.get(cat, 0)
        if act != exp:
            errors.append(f"{cat}: expected={exp}, actual={act}")

    if errors:
        raise RuntimeError("Eval QA 개수 검증 실패: " + "; ".join(errors))


def make_summary(data: List[Dict[str, Any]]) -> str:
    counts: Dict[str, int] = {}
    for row in data:
        counts[row["category"]] = counts.get(row["category"], 0) + 1

    lines = [
        "EMS Evidence Eval Dataset Summary",
        "=" * 40,
        f"total: {len(data)}",
        "",
        "category counts:",
    ]
    for cat in ["anomaly", "cms", "report"]:
        lines.append(f"- {cat}: {counts.get(cat, 0)}")
    lines += [
        "",
        "purpose:",
        "- SFT 학습에 넣지 않는 평가 전용 데이터셋",
        "- 모든 질문은 기간/설비/지표/이상유형이 명확한 정답 고정형 evidence_answer",
        "- Expert QA 100 + Chat QA 100 학습 후 별도 평가에 사용",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--anomaly", type=int, default=DEFAULT_COUNTS["anomaly"])
    parser.add_argument("--cms", type=int, default=DEFAULT_COUNTS["cms"])
    parser.add_argument("--report", type=int, default=DEFAULT_COUNTS["report"])
    parser.add_argument("--allow-partial", action="store_true", help="개수가 부족해도 partial 저장")
    args = parser.parse_args()

    expected = {
        "anomaly": args.anomaly,
        "cms": args.cms,
        "report": args.report,
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir) / ts
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"출력 폴더: {output_dir}")
    print("DB 연결 중...")

    with get_conn() as conn:
        print("anomaly Eval 생성 중...")
        anomaly_items = generate_anomaly_eval(conn, args.anomaly)
        print(f"  anomaly: {len(anomaly_items)}개")

        print("cms Eval 생성 중...")
        cms_items = generate_cms_eval(conn, args.cms)
        print(f"  cms: {len(cms_items)}개")

        print("report Eval 생성 중...")
        report_items = generate_report_eval(conn, args.report)
        print(f"  report: {len(report_items)}개")

    data = anomaly_items + cms_items + report_items

    if not args.allow_partial:
        validate_counts(data, expected)

    json_path = output_dir / "ems_eval_evidence_100.json"
    jsonl_path = output_dir / "ems_eval_evidence_100.jsonl"
    summary_path = output_dir / "summary.txt"

    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    write_jsonl(jsonl_path, data)
    summary_path.write_text(make_summary(data), encoding="utf-8")

    print("\n저장 완료:")
    print(f"- JSON : {json_path}")
    print(f"- JSONL: {jsonl_path}")
    print(f"- SUMMARY: {summary_path}")
    print("\n카테고리 구성:")
    print(make_summary(data))


if __name__ == "__main__":
    main()
