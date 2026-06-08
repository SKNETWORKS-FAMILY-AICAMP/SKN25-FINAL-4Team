"""
anomaly 카테고리 eval 기준 데이터를 현재 DB 값으로 갱신.

기존 reference_context / reference_answer의 수치를 현재 anomaly_results DB와 일치시킨다.
anomaly 카테고리 항목만 수정, 나머지(cms/report)는 그대로 유지.

실행:
  backend/.venv/bin/python dev/eval/scripts/regen_anomaly_eval.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend" / "src"))

from dotenv import load_dotenv
load_dotenv(str(Path(__file__).resolve().parents[3] / ".env"))

import psycopg2

DB_URL = os.getenv("DATABASE_URL")
DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "ems_eval_evidence_97.json"
OUT_PATH  = DATA_PATH  # 원본 덮어쓰기 (backup은 아래서 생성)


def _parse_period(context: str) -> tuple[str, str] | None:
    m = re.search(r"period=(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})", context)
    return (m.group(1), m.group(2)) if m else None


def _parse_anomaly_type(context: str) -> str | None:
    m = re.search(r"anomaly_type=(\S+)", context)
    return m.group(1) if m else None


def fetch_stats(atype: str, start: str, end: str) -> dict | None:
    """anomaly_results에서 현재 통계 조회."""
    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()
            # severity별 건수
            cur.execute("""
                SELECT severity, COUNT(*)
                FROM anomaly_results
                WHERE anomaly_type = %s
                  AND timestamp >= %s AND timestamp < %s
                GROUP BY severity ORDER BY severity
            """, (atype, start, end + "T23:59:59"))
            rows = cur.fetchall()
            if not rows:
                return None
            counts = {sev: cnt for sev, cnt in rows}
            total = sum(counts.values())

            # 첫/마지막 시각, 최대 잔차
            cur.execute("""
                SELECT MIN(timestamp), MAX(timestamp),
                       MAX(ABS(COALESCE(residual_w, 0)))
                FROM anomaly_results
                WHERE anomaly_type = %s
                  AND timestamp >= %s AND timestamp < %s
            """, (atype, start, end + "T23:59:59"))
            r = cur.fetchone()
            first_time = str(r[0]) if r[0] else "N/A"
            last_time  = str(r[1]) if r[1] else "N/A"
            max_res    = f"{r[2]:,.2f}" if r[2] else "N/A"

            return {
                "total": total,
                "counts": counts,
                "first_time": first_time,
                "last_time":  last_time,
                "max_res":    max_res,
            }
        finally:
            conn.close()
    except Exception as e:
        print(f"  [DB 오류] {e}")
        return None


def build_context(original: str, stats: dict) -> str:
    """reference_context 수치 부분을 DB 값으로 교체."""
    counts = stats["counts"]
    sev_parts = " ".join(f"{s} {c}건," for s, c in sorted(counts.items())).rstrip(",")

    lines = []
    for line in original.splitlines():
        if line.startswith("total_count="):
            lines.append(f"total_count={stats['total']}")
        elif line.startswith("severity_counts="):
            lines.append(f"severity_counts={sev_parts}")
        elif line.startswith("first_time="):
            lines.append(f"first_time={stats['first_time']}")
        elif line.startswith("last_time="):
            lines.append(f"last_time={stats['last_time']}")
        elif line.startswith("max_abs_residual="):
            lines.append(f"max_abs_residual={stats['max_res']}")
        else:
            lines.append(line)
    return "\n".join(lines)


def build_answer(original: str, atype: str, period_start: str, stats: dict) -> str:
    """reference_answer 수치를 DB 값으로 교체."""
    counts = stats["counts"]
    total  = stats["total"]

    # 연도/월 추출
    m = re.match(r"(\d{4})-(\d{2})", period_start)
    year, mon = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    mon_str = f"{year}년 {mon}월"

    # severity 분포 문자열
    sev_parts = ", ".join(f"{s} {c}건" for s, c in sorted(counts.items()))

    if total == 0:
        return (
            f"{mon_str} 동안 {atype} 이상 후보는 현재 데이터베이스에서 확인되지 않습니다. "
            "해당 기간 탐지 결과가 없거나 데이터 갱신이 필요합니다."
        )

    return (
        f"{mon_str} 동안 {atype} 이상 후보는 총 {total}건 발생했습니다. "
        f"등급 분포는 {sev_parts}입니다. "
        "해당 기간의 설비 운전 상태와 관련 이벤트 확인이 권장됩니다."
    )


def main():
    data: list[dict] = json.load(open(DATA_PATH, encoding="utf-8"))

    # 원본 백업
    backup_path = DATA_PATH.with_name("ems_eval_evidence_97_backup.json")
    if not backup_path.exists():
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"백업 저장: {backup_path.name}")

    updated = changed = 0
    for item in data:
        if item["category"] != "anomaly":
            continue

        ctx   = item.get("reference_context", "")
        period = _parse_period(ctx)
        atype  = _parse_anomaly_type(ctx)

        if not period or not atype:
            print(f"[{item['id']}] 파싱 실패, 스킵")
            continue

        start, end = period
        stats = fetch_stats(atype, start, end)
        updated += 1

        if stats is None:
            print(f"[{item['id']}] DB 없음 → total=0 처리")
            stats = {"total": 0, "counts": {}, "first_time": "N/A", "last_time": "N/A", "max_res": "N/A"}

        old_total = int(re.search(r"total_count=(\d+)", ctx).group(1))
        new_total = stats["total"]

        new_ctx = build_context(ctx, stats)
        new_ans = build_answer(item["reference_answer"], atype, start, stats)

        if old_total != new_total:
            print(f"[{item['id']}] {atype} {start[:7]}: {old_total}건 → {new_total}건")
            changed += 1

        item["reference_context"] = new_ctx
        item["reference_answer"]  = new_ans

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n완료: anomaly {updated}건 처리, {changed}건 수치 변경")
    print(f"저장: {OUT_PATH.name}")


if __name__ == "__main__":
    main()
