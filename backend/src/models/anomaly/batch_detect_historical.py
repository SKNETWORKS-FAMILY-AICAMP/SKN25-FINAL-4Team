"""
2022-01-01 ~ 2024-01-01 구간 이상탐지 배치 실행.
결과를 anomaly_results 테이블에 저장.

실행:
  cd <project-root>
  backend/.venv/bin/python backend/src/models/anomaly/batch_detect_historical.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

# backend/src/ 를 경로에 추가
_SRC = Path(__file__).resolve().parents[2]  # backend/src/
_BACKEND = _SRC.parent                       # backend/
sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(_BACKEND))

# .env 로드 (프로젝트 루트 기준)
from dotenv import load_dotenv
load_dotenv(str(_BACKEND.parent / ".env"))

from data.loader import load_range
from models.anomaly import ensemble


def month_ranges(start: datetime, end: datetime):
    """start~end 사이를 월 단위 (start, end) 튜플로 반환."""
    cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cur < end:
        # 다음 달 1일
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1)
        else:
            nxt = cur.replace(month=cur.month + 1)
        yield cur, min(nxt, end)
        cur = nxt


START = datetime(2022, 1, 1, tzinfo=timezone.utc)
END   = datetime(2024, 1, 1, tzinfo=timezone.utc)

print(f"배치 이상탐지 시작: {START.date()} ~ {END.date()}")
print("=" * 60)

total_detected = 0

for s, e in month_ranges(START, END):
    label = s.strftime("%Y-%m")
    s_str = s.strftime("%Y-%m-%d")
    e_str = e.strftime("%Y-%m-%d")
    print(f"\n[{label}] {s_str} ~ {e_str} 로딩 중...")

    try:
        df = load_range(s_str, e_str)
        if df.empty:
            print(f"[{label}] 데이터 없음, 스킵")
            continue

        print(f"[{label}] {len(df)}행 로드 완료 → 앙상블 탐지 중...")
        anomalies = ensemble.run(df, save_to_db=True, min_votes=1)
        n = len(anomalies)
        total_detected += n
        print(f"[{label}] 완료: {n}건 탐지 (누적 {total_detected}건)")

    except Exception as ex:
        print(f"[{label}] 오류: {ex}")
        import traceback; traceback.print_exc()

print("\n" + "=" * 60)
print(f"배치 완료. 총 탐지: {total_detected}건")
