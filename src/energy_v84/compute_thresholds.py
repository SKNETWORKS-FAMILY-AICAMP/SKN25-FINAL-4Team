"""
val 기간 actual P 기준 시간대별 사전 경보 threshold 계산.

방식: 시간대별(0~23h) 2nd~98th percentile → 양방향 정상 범위
대상: METER_MAP의 63개 계량기 (meter_urn 기준 actual P 사용)
출력: artifacts/thresholds/val_thresholds.csv

CLI:
  conda run -n skn25 python -m energy_v84.compute_thresholds
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from energy_v84.common.config import (
    ARTIFACTS_DIR,
    METER_SPECS_BY_URN,
    VAL_START,
    TEST_START,
)
from energy_v84.common.db import build_engine, fetch_meter_frame
from energy_v84.common.mapping import METER_MAP
from energy_v84.common.preprocessing import normalize_ts

LOWER_PERCENTILE  = 2
UPPER_PERCENTILE  = 98
LOW_SAMPLE_CUTOFF = 100  # n_samples < 이 값이면 threshold 불안정 태그
OUTPUT_DIR = ARTIFACTS_DIR / "thresholds"
OUTPUT_PATH = OUTPUT_DIR / "val_thresholds.csv"


def compute_thresholds(engine) -> pd.DataFrame:
    rows = []

    for meter_urn, info in sorted(METER_MAP.items()):
        if info["action"] == "skip":
            continue

        spec = METER_SPECS_BY_URN.get(meter_urn)
        if spec is None:
            print(f"  [SKIP] {meter_urn}: spec 없음", flush=True)
            continue

        try:
            df = fetch_meter_frame(
                engine, spec,
                start_ts=VAL_START.strftime("%Y-%m-%d %H:%M:%S"),
                end_ts=TEST_START.strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception as e:
            print(f"  [SKIP] {meter_urn}: DB 오류 — {e}", flush=True)
            continue

        df["ts"] = normalize_ts(df["ts"])
        df["hour"] = df["ts"].dt.hour
        p = pd.to_numeric(df["P"], errors="coerce")
        df = df.assign(P=p).dropna(subset=["P"])

        if df.empty:
            print(f"  [SKIP] {meter_urn}: P 데이터 없음", flush=True)
            continue

        for hour in range(24):
            hour_p = df.loc[df["hour"] == hour, "P"].to_numpy(dtype=np.float32)
            if len(hour_p) == 0:
                # 해당 시간대 데이터 없으면 전체 분포로 대체
                hour_p = df["P"].to_numpy(dtype=np.float32)

            p_lower = float(np.percentile(hour_p, LOWER_PERCENTILE))
            p_upper = float(np.percentile(hour_p, UPPER_PERCENTILE))
            # floor: p_lower가 0 근처(abs<10)인 row만 -50W로 내림
            if abs(p_lower) < 10 and p_lower > -50.0:
                p_lower = -50.0
            rows.append({
                "meter_urn":  meter_urn,
                "hour":       hour,
                "p_lower":    p_lower,
                "p_upper":    p_upper,
                "n_samples":  len(hour_p),
                "low_sample": len(hour_p) < LOW_SAMPLE_CUTOFF,
            })

        print(f"  {meter_urn}: {len(df)}개 val 행 처리 완료", flush=True)

    return pd.DataFrame(rows)


def main():
    engine = build_engine()
    print("=== val threshold 계산 시작 ===", flush=True)
    df = compute_thresholds(engine)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\n저장: {OUTPUT_PATH}")
    print(f"계량기 수: {df['meter_urn'].nunique()}, 총 행수: {len(df)} (계량기 × 24h)")

    # 샘플 확인
    sample = df[df["meter_urn"] == df["meter_urn"].iloc[0]]
    print(f"\n[샘플: {sample['meter_urn'].iloc[0]}]")
    print(sample[["hour", "p_lower", "p_upper", "n_samples"]].to_string(index=False))


if __name__ == "__main__":
    main()
