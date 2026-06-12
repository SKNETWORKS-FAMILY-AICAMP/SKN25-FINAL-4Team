"""candidate artifact 검증 스크립트.

Usage:
    conda run -n skn25 python scripts/validate_candidate.py \\
        --run run_20260609 --horizon 3

검증 항목:
    1. 기대 계량기 51개 디렉토리 존재 여부
    2. 공통 필수 파일 + routing 기반 모델 파일 누락 여부
    3. train_summary 필수 컬럼(meter_urn, test_mae) 존재 + 학습 실패 계량기 확인
    4. candidate vs active MAE 비교 (5% 이상 악화 시 경고)

결과:
    exit(0) → validated.marker 생성 (result="pass")
    exit(2) → validated.marker 생성 (result="warn", degraded_count > 0)
    exit(1) → marker 생성 없음
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from energy_v84.common.mapping import METER_MAP

ARTIFACTS_DIR = Path(
    os.getenv(
        "MODEL_ARTIFACTS_DIR",
        str(ROOT / "artifacts"),
    )
).resolve()

EXPECTED_MODEL_URNS = sorted(set(v["model_urn"] for v in METER_MAP.values()))

BASE_REQUIRED = [
    "routing.json",
    "input_scaler.joblib",
    "target_scaler.joblib",
    "feature_columns.json",
    "hour_bias_corrections.csv",
    "ridge.joblib",
]

SUMMARY_REQUIRED_COLS = {"meter_urn", "test_mae"}
MAE_DEGRADATION_THRESHOLD = 0.05


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--run",     required=True, help="candidate run 이름. 예: run_20260609")
    p.add_argument("--horizon", type=int, choices=[1, 3], required=True)
    return p.parse_args()


def _routing_required_files(routing: dict, horizon: int) -> list[str]:
    files = []
    v57 = routing.get("v57", "v52")
    v63 = routing.get("v63", "v57")

    if v57 == "v53":
        files.append("catboost.cbm")
    if v63 == "v61":
        for k in range(1, horizon + 1):
            files.append(f"lightgbm_t_plus_{k}.txt")

    lstm_versions: set[str] = set(routing.get("lstm_top2_versions", []))
    if routing.get("v52_source") == "v3":
        lstm_versions.add("v3")
    for step_vers in routing.get("v12_step_versions", []):
        lstm_versions.update(step_vers)
    for step_vers in routing.get("v15_step_versions", []):
        lstm_versions.update(step_vers)
    for ver in lstm_versions:
        files.append(f"lstm_{ver}.pt")

    return files


def check_meters(candidate_dir: Path, horizon: int) -> tuple[list[str], list[str]]:
    """기대 계량기 51개 기준 디렉토리 및 필수 파일 검사.
    반환: (missing_dirs, missing_files)
    """
    horizon_dir = candidate_dir / f"{horizon}h"
    if not horizon_dir.exists():
        return EXPECTED_MODEL_URNS[:], [f"{horizon}h/ 디렉토리 없음"]

    existing = {d.name for d in horizon_dir.iterdir() if d.is_dir()}
    missing_dirs = [u for u in EXPECTED_MODEL_URNS if u not in existing]

    missing_files = []
    for urn in EXPECTED_MODEL_URNS:
        if urn not in existing:
            continue
        meter_dir = horizon_dir / urn
        for fname in BASE_REQUIRED:
            if not (meter_dir / fname).exists():
                missing_files.append(f"{urn}/{fname}")
        routing_path = meter_dir / "routing.json"
        if routing_path.exists():
            try:
                routing = json.loads(routing_path.read_text())
                for fname in _routing_required_files(routing, horizon):
                    if not (meter_dir / fname).exists():
                        missing_files.append(f"{urn}/{fname}")
            except Exception:
                missing_files.append(f"{urn}/routing.json (파싱 실패)")

    return missing_dirs, missing_files


def check_summary(candidate_dir: Path, active_dir: Path, horizon: int) -> dict:
    """train_summary 컬럼 검증 + MAE 비교."""
    cand_csv = candidate_dir / f"train_summary_{horizon}h.csv"
    if not cand_csv.exists():
        return {"fatal": f"train_summary_{horizon}h.csv 없음"}

    try:
        cand = pd.read_csv(cand_csv)
    except Exception as e:
        return {"fatal": f"train_summary 읽기 실패: {e}"}

    if cand.empty:
        return {"fatal": "train_summary가 빈 파일"}

    missing_cols = SUMMARY_REQUIRED_COLS - set(cand.columns)
    if missing_cols:
        return {"fatal": f"train_summary 필수 컬럼 없음: {missing_cols}"}

    failed = list(cand[cand["test_mae"].isna()]["meter_urn"])

    summary_urns = set(cand["meter_urn"])
    missing_from_summary = [u for u in EXPECTED_MODEL_URNS if u not in summary_urns]

    degraded = []
    active_csv = active_dir / f"train_summary_{horizon}h.csv"
    if active_csv.exists():
        try:
            active = pd.read_csv(active_csv)
            merged = cand.merge(active[["meter_urn", "test_mae"]], on="meter_urn",
                                suffixes=("_cand", "_active"), how="left")
            for _, row in merged.iterrows():
                if pd.isna(row.get("test_mae_active")) or pd.isna(row.get("test_mae_cand")):
                    continue
                active_mae, cand_mae = row["test_mae_active"], row["test_mae_cand"]
                if active_mae > 0 and (cand_mae - active_mae) / active_mae > MAE_DEGRADATION_THRESHOLD:
                    degraded.append({
                        "meter_urn":  row["meter_urn"],
                        "active_mae": round(active_mae, 1),
                        "cand_mae":   round(cand_mae, 1),
                        "change_pct": round((cand_mae - active_mae) / active_mae * 100, 1),
                    })
        except Exception as e:
            print(f"  [참고] active MAE 비교 실패: {e}")
    else:
        print("  [참고] active train_summary 없음 — MAE 비교 생략")

    return {
        "total_meters":         len(cand),
        "failed_meters":        failed,
        "missing_from_summary": missing_from_summary,
        "degraded_meters":      degraded,
    }


def write_marker(candidate_dir: Path, run: str, horizon: int,
                 result: str, degraded_count: int, failed_count: int) -> None:
    marker = {
        "run":            run,
        "horizon":        horizon,
        "validated_at":   datetime.now(timezone.utc).isoformat(),
        "result":         result,
        "degraded_count": degraded_count,
        "failed_count":   failed_count,
    }
    (candidate_dir / "validated.marker").write_text(json.dumps(marker, indent=2))


def main():
    args = parse_args()
    candidate_dir = ARTIFACTS_DIR / "candidate" / args.run
    active_dir    = ARTIFACTS_DIR

    print(f"=== candidate 검증: {args.run} / horizon={args.horizon}h ===")
    print(f"    기대 계량기: {len(EXPECTED_MODEL_URNS)}개\n")

    if not candidate_dir.exists():
        print(f"[오류] candidate 디렉토리 없음: {candidate_dir}")
        sys.exit(1)

    has_error = False
    has_warn  = False

    # ── 1. 계량기 디렉토리 및 파일 검사 ────────────────────────────────────
    print("── 1. 계량기 디렉토리 및 파일 검사")
    missing_dirs, missing_files = check_meters(candidate_dir, args.horizon)

    if missing_dirs:
        print(f"  [실패] artifact 디렉토리 누락 {len(missing_dirs)}개:")
        for u in missing_dirs:
            print(f"    - {u}")
        has_error = True
    else:
        print(f"  [통과] {len(EXPECTED_MODEL_URNS)}개 계량기 디렉토리 확인")

    if missing_files:
        print(f"  [실패] 필수 파일 누락 {len(missing_files)}개:")
        for f in missing_files:
            print(f"    - {f}")
        has_error = True
    else:
        print(f"  [통과] 필수 파일 전체 확인 완료")

    # ── 2. train_summary 검사 ─────────────────────────────────────────────
    print("\n── 2. train_summary 검사")
    result = check_summary(candidate_dir, active_dir, args.horizon)

    if "fatal" in result:
        print(f"  [실패] {result['fatal']}")
        has_error = True
    else:
        print(f"  총 계량기: {result['total_meters']}개 (기대: {len(EXPECTED_MODEL_URNS)}개)")

        if result["missing_from_summary"]:
            print(f"  [실패] summary 누락 계량기 {len(result['missing_from_summary'])}개:")
            for u in result["missing_from_summary"]:
                print(f"    - {u}")
            has_error = True
        else:
            print(f"  [통과] 기대 계량기 전원 summary 포함")

        if result["failed_meters"]:
            print(f"  [실패] 학습 실패(test_mae=NaN) {len(result['failed_meters'])}개:")
            for m in result["failed_meters"]:
                print(f"    - {m}")
            has_error = True
        else:
            print(f"  [통과] 학습 실패 계량기 없음")

        if result["degraded_meters"]:
            print(f"  [경고] MAE {MAE_DEGRADATION_THRESHOLD*100:.0f}% 이상 악화 {len(result['degraded_meters'])}개:")
            for d in result["degraded_meters"]:
                print(f"    - {d['meter_urn']}: active={d['active_mae']} → cand={d['cand_mae']} ({d['change_pct']:+.1f}%)")
            has_warn = True
        else:
            print(f"  [통과] MAE 악화 계량기 없음")

    # ── 최종 판정 + marker 생성 ───────────────────────────────────────────
    print()
    degraded_count = len(result.get("degraded_meters", []))
    failed_count   = len(result.get("failed_meters", []))

    if has_error:
        print("=== 결과: 실패 — 승격 불가. validated.marker 생성 안 함 ===")
        sys.exit(1)
    elif has_warn:
        write_marker(candidate_dir, args.run, args.horizon,
                     "warn", degraded_count, failed_count)
        print(f"=== 결과: 경고 (degraded={degraded_count}) — validated.marker 생성 (result=warn) ===")
        print("    promote_candidate.py 실행 시 추가 확인 프롬프트가 표시됩니다.")
        sys.exit(2)
    else:
        write_marker(candidate_dir, args.run, args.horizon,
                     "pass", 0, 0)
        print("=== 결과: 통과 — validated.marker 생성 (result=pass) ===")
        sys.exit(0)


if __name__ == "__main__":
    main()
