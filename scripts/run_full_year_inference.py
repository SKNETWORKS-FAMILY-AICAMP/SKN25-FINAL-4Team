"""
test 기간(2023) 전체 hourly 배치 추론.

DB 쿼리를 계량기별 1회로 줄이고 메모리 슬라이딩 윈도우를 사용.
결과: artifacts/inference_results_full_year/predictions_{horizon}h_full_year.csv
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

TEST_START = pd.Timestamp("2023-01-01 00:00:00", tz="UTC")
TEST_END   = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
# 첫 timestamp의 window에 필요한 warmup
# 실질 최솟값 = 168(window) + 168(diff_lag168 prefix) = 336h
# needed 공식(168+168+horizon+4)과 별개 — len(raw) < window_size(24)만 체크하므로 340h로 충분
WARMUP_HOURS = 340
PREFETCH_START = TEST_START - pd.Timedelta(hours=WARMUP_HOURS)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def _prefetch(engine, spec, end_ts_str: str) -> pd.DataFrame | None:
    """계량기 전체 데이터를 1회 조회."""
    from energy_v84.common.db import fetch_meter_frame
    try:
        return fetch_meter_frame(
            engine, spec,
            start_ts=PREFETCH_START.strftime("%Y-%m-%d %H:%M:%S"),
            end_ts=end_ts_str,
        )
    except Exception:
        return None


def run_full_year(horizon: int, output_dir: Path) -> None:
    from energy_v84.common.config import METER_SPECS_BY_URN
    from energy_v84.common.db import build_engine
    from energy_v84.common.mapping import ARTIFACTS_DIR, METER_MAP
    import energy_v84.inference as inf

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"predictions_{horizon}h_full_year.csv"

    # 전역 상태 초기화
    inf._THRESHOLDS = inf._load_thresholds()
    inf._METER_TAGS  = inf._load_meter_tags()
    print(f"threshold: {len(inf._THRESHOLDS)}개, tags: {len(inf._METER_TAGS)}개")

    engine = build_engine()

    # 예측 대상 계량기 목록
    active_meters = [
        (urn, info["model_urn"])
        for urn, info in sorted(METER_MAP.items())
        if info["action"] != "skip"
        and (ARTIFACTS_DIR / f"{horizon}h" / info["model_urn"] / "routing.json").exists()
    ]
    print(f"예측 대상: {len(active_meters)}개 계량기")

    # ── Step 1: 계량기별 데이터 pre-fetch ──────────────────────────────────────
    print("\n[1/2] 계량기 데이터 pre-fetch 중...")
    t0_fetch = time.time()
    meter_cache: dict[str, pd.DataFrame | None] = {}
    end_ts_str = TEST_END.strftime("%Y-%m-%d %H:%M:%S")
    for i, (meter_urn, _) in enumerate(active_meters, 1):
        spec = METER_SPECS_BY_URN.get(meter_urn)
        if spec is None:
            meter_cache[meter_urn] = None
            continue
        df = _prefetch(engine, spec, end_ts_str)
        meter_cache[meter_urn] = df
        if i % 10 == 0 or i == len(active_meters):
            print(f"  {i}/{len(active_meters)} 완료", flush=True)
    print(f"  pre-fetch 완료: {time.time() - t0_fetch:.1f}초")

    # ── Step 2: 전체 timestamp 순회 ────────────────────────────────────────────
    timestamps = pd.date_range(TEST_START, TEST_END, freq="h", inclusive="left")
    n_ts = len(timestamps)
    print(f"\n[2/2] 추론 시작: {n_ts}개 timestamp × {len(active_meters)}개 계량기")

    t0_inf = time.time()
    all_rows: list[dict] = []
    report_interval = max(1, n_ts // 20)  # 5% 단위 진행 보고

    for ti, ts in enumerate(timestamps, 1):
        ts_rows: list[dict] = []
        for meter_urn, model_urn in active_meters:
            raw_data = meter_cache.get(meter_urn)
            try:
                res = inf.predict_meter(
                    engine, meter_urn, model_urn, horizon, ts,
                    raw_data=raw_data,
                )
                if res is None:
                    ts_rows.append(inf._failed_row(meter_urn, model_urn, horizon, ts, "insufficient_data"))
                else:
                    ts_rows.append(res)
            except Exception as e:
                ts_rows.append(inf._failed_row(meter_urn, model_urn, horizon, ts, "error"))

        # artifact 없는 계량기 처리
        active_urns = {urn for urn, _ in active_meters}
        for urn, info in sorted(METER_MAP.items()):
            if info["action"] == "skip":
                continue
            if urn not in active_urns:
                model_urn = info["model_urn"]
                ts_rows.append(inf._failed_row(urn, model_urn, horizon, ts, "no_artifact"))

        all_rows.extend(ts_rows)

        if ti % report_interval == 0 or ti == n_ts:
            elapsed = time.time() - t0_inf
            eta = elapsed / ti * (n_ts - ti)
            print(f"  {ti:5d}/{n_ts}  경과 {elapsed/60:.1f}분  남은시간 {eta/60:.1f}분", flush=True)

    total_inf = time.time() - t0_inf

    # ── 저장 ───────────────────────────────────────────────────────────────────
    print(f"\n저장 중... ({len(all_rows):,}행)")
    pd.DataFrame(all_rows).to_csv(out_path, index=False)
    print(f"저장 완료: {out_path}")
    print(f"\n=== 완료 ===")
    print(f"  pre-fetch:  {t0_inf - t0_fetch:.1f}초")
    print(f"  추론:       {total_inf:.1f}초 ({total_inf/60:.1f}분)")
    print(f"  총계:       {(time.time() - t0_fetch)/60:.1f}분")
    print(f"  행수:       {len(all_rows):,} ({n_ts}ts × {len(active_meters)}meters)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--horizon", type=int, choices=[1, 3], required=True)
    p.add_argument("--output-dir", type=str,
                   default="artifacts/inference_results_full_year")
    args = p.parse_args()
    out = Path(args.output_dir)
    print(f"=== full-year inference | horizon={args.horizon}h | test 2023 ===")
    run_full_year(horizon=args.horizon, output_dir=out)


if __name__ == "__main__":
    main()
