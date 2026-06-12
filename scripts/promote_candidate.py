"""candidate → active 승격 스크립트.

Usage:
    conda run -n skn25 python scripts/promote_candidate.py \\
        --run run_20260609 --horizon 3 [--yes]

순서 (순서 변경 금지):
    1. active → archive 백업
    2. candidate → active 승격
    3. smoke test (단일 timestamp 추론)
    4. smoke test 성공 시 candidate 삭제
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
ARTIFACTS_DIR = Path(
    os.getenv(
        "MODEL_ARTIFACTS_DIR",
        str(ROOT / "artifacts"),
    )
).resolve()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--run",     required=True, help="candidate run 이름. 예: run_20260609")
    p.add_argument("--horizon", type=int, choices=[1, 3], required=True)
    p.add_argument("--yes",     action="store_true", help="확인 프롬프트 생략")
    return p.parse_args()


def smoke_test(horizon: int) -> bool:
    """단일 timestamp 추론으로 smoke test."""
    import pandas as pd
    import tempfile
    from energy_v84.inference import run_inference

    ts = pd.Timestamp("2023-06-01T09:00:00", tz="UTC")
    out = Path(tempfile.mkdtemp())
    try:
        run_inference(horizon=horizon, timestamp=ts, output_dir=out)
        csv = out / f"predictions_{horizon}h_20230601T0900.csv"
        if not csv.exists():
            return False
        df = pd.read_csv(csv)
        success = (df["status"] == "success").sum()
        print(f"  smoke test: {success}/63 계량기 성공")
        return success >= 50
    except Exception as e:
        print(f"  smoke test 실패: {e}")
        return False
    finally:
        shutil.rmtree(out, ignore_errors=True)


def cleanup_promoted_candidate(
    candidate_dir: Path,
    cand_horizon_dir: Path,
    cand_summary: Path,
    marker_path: Path,
) -> list[Path]:
    """승격 완료된 candidate 산출물만 정리한다.

    동일 run_id 아래에 다른 horizon 산출물이 남아 있을 수 있으므로
    candidate_dir 전체를 바로 삭제하지 않는다.
    """
    failed: list[Path] = []
    cleanup_targets = [
        (cand_horizon_dir, lambda p: shutil.rmtree(p)),
        (cand_summary, lambda p: p.unlink()),
        (marker_path, lambda p: p.unlink()),
    ]

    for target, remove in cleanup_targets:
        if not target.exists():
            print(f"  이미 없음: {target}")
            continue
        try:
            remove(target)
            print(f"  삭제 완료: {target}")
        except OSError as e:
            failed.append(target)
            print(f"  [경고] 삭제 실패: {target}: {e}")

    try:
        candidate_dir.rmdir()
        print(f"  빈 candidate 디렉토리 삭제 완료: {candidate_dir}")
    except OSError as e:
        if e.errno == errno.ENOTEMPTY:
            print(f"  candidate 루트 유지: {candidate_dir} (다른 산출물 존재)")
        else:
            failed.append(candidate_dir)
            print(f"  [경고] candidate 루트 삭제 실패: {candidate_dir}: {e}")

    return failed


def main():
    args = parse_args()
    candidate_dir = ARTIFACTS_DIR / "candidate" / args.run
    active_horizon_dir  = ARTIFACTS_DIR / f"{args.horizon}h"
    archive_run_name    = f"run_prev_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    archive_horizon_dir = ARTIFACTS_DIR / "archive" / archive_run_name / f"{args.horizon}h"
    cand_horizon_dir    = candidate_dir / f"{args.horizon}h"
    cand_summary        = candidate_dir / f"train_summary_{args.horizon}h.csv"
    active_summary      = ARTIFACTS_DIR / f"train_summary_{args.horizon}h.csv"
    archive_summary     = ARTIFACTS_DIR / "archive" / archive_run_name / f"train_summary_{args.horizon}h.csv"

    print(f"=== candidate 승격: {args.run} / horizon={args.horizon}h ===\n")

    # ── 사전 체크 ────────────────────────────────────────────────────────────
    if not candidate_dir.exists():
        print(f"[오류] candidate 디렉토리 없음: {candidate_dir}")
        sys.exit(1)
    if not cand_horizon_dir.exists():
        print(f"[오류] candidate {args.horizon}h/ 디렉토리 없음")
        sys.exit(1)

    # ── validated.marker 확인 ────────────────────────────────────────────────
    marker_path = candidate_dir / "validated.marker"
    if not marker_path.exists():
        print("[오류] validated.marker 없음 — validate_candidate.py를 먼저 실행하세요.")
        sys.exit(1)

    try:
        marker = json.loads(marker_path.read_text())
    except Exception as e:
        print(f"[오류] validated.marker 파싱 실패: {e}")
        sys.exit(1)

    if marker.get("run") != args.run:
        print(f"[오류] marker run 불일치: marker={marker.get('run')} / 요청={args.run}")
        sys.exit(1)
    if marker.get("horizon") != args.horizon:
        print(f"[오류] marker horizon 불일치: marker={marker.get('horizon')} / 요청={args.horizon}")
        sys.exit(1)

    marker_result = marker.get("result", "")
    if marker_result not in ("pass", "warn"):
        print(f"[오류] marker result 값 비정상: {marker_result!r} — 재검증 필요")
        sys.exit(1)

    print(f"validated.marker: result={marker_result}, "
          f"validated_at={marker.get('validated_at', '?')}, "
          f"degraded={marker.get('degraded_count', 0)}")

    meter_cnt = len([d for d in cand_horizon_dir.iterdir() if d.is_dir()])
    print(f"candidate 계량기: {meter_cnt}개")
    print(f"archive 경로:     {archive_horizon_dir}")
    print(f"active  경로:     {active_horizon_dir}")
    print()

    if marker_result == "warn":
        degraded = marker.get("degraded_count", 0)
        print(f"[경고] MAE 악화 계량기 {degraded}개가 있는 상태입니다.")
        if not args.yes:
            ans = input("MAE 악화 계량기가 있습니다. 그래도 승격하겠습니까? (y/N): ").strip().lower()
            if ans != "y":
                print("취소됨.")
                sys.exit(0)
        else:
            print("  --yes 플래그로 warn 상태 승격을 강제 진행합니다.")
        print()
    elif not args.yes:
        ans = input("승격을 진행하겠습니까? (y/N): ").strip().lower()
        if ans != "y":
            print("취소됨.")
            sys.exit(0)

    # ── 1. active → archive 백업 ────────────────────────────────────────────
    print("── 1. active → archive 백업")
    archive_horizon_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        if active_horizon_dir.exists():
            shutil.copytree(active_horizon_dir, archive_horizon_dir)
            print(f"  백업 완료: {active_horizon_dir} → {archive_horizon_dir}")
        else:
            print(f"  [참고] active {args.horizon}h/ 없음 — 백업 생략")
        if active_summary.exists():
            archive_summary.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(active_summary, archive_summary)
    except Exception as e:
        print(f"  [오류] 백업 실패: {e}")
        print("  승격 중단. active 변경 없음.")
        sys.exit(1)

    # ── 2. candidate → active 승격 ──────────────────────────────────────────
    print("\n── 2. candidate → active 승격")
    try:
        if active_horizon_dir.exists():
            shutil.rmtree(active_horizon_dir)
        shutil.copytree(cand_horizon_dir, active_horizon_dir)
        if cand_summary.exists():
            shutil.copy2(cand_summary, active_summary)
        print(f"  승격 완료: {cand_horizon_dir} → {active_horizon_dir}")
    except Exception as e:
        print(f"  [오류] 승격 실패: {e}")
        print("  archive에서 복원 시도 중...")
        try:
            if active_horizon_dir.exists():
                shutil.rmtree(active_horizon_dir)
            shutil.copytree(archive_horizon_dir, active_horizon_dir)
            if archive_summary.exists():
                shutil.copy2(archive_summary, active_summary)
            print("  복원 완료.")
        except Exception as re:
            print(f"  [치명] 복원도 실패: {re}")
            print(f"  수동 복원 필요: {archive_horizon_dir} → {active_horizon_dir}")
        sys.exit(1)

    # ── 3. smoke test ────────────────────────────────────────────────────────
    print("\n── 3. smoke test")
    sys.path.insert(0, str(ROOT))
    ok = smoke_test(args.horizon)
    if ok:
        print("  [통과]")
    else:
        print("  [실패] smoke test 실패 — 롤백 시작")
        try:
            if active_horizon_dir.exists():
                shutil.rmtree(active_horizon_dir)
            shutil.copytree(archive_horizon_dir, active_horizon_dir)
            if archive_summary.exists():
                shutil.copy2(archive_summary, active_summary)
            print(f"  롤백 완료: {archive_horizon_dir} → {active_horizon_dir}")
        except Exception as re:
            print(f"  [치명] 롤백 실패: {re}")
            print(f"  수동 복원 필요: {archive_horizon_dir} → {active_horizon_dir}")
        sys.exit(1)

    # ── 4. candidate 정리 ───────────────────────────────────────────────────
    print("\n── 4. candidate 정리")
    failed_cleanup = cleanup_promoted_candidate(candidate_dir, cand_horizon_dir, cand_summary, marker_path)
    if failed_cleanup:
        print("  active 승격은 완료됐습니다. candidate는 수동 삭제만 필요합니다.")
        print("  수동 확인 대상:")
        for path in failed_cleanup:
            print(f"    - {path}")
        print("  재실행 방지를 위해 candidate 산출물 또는 validated.marker를 수동으로 정리하세요.")

    print(f"\n=== 승격 완료 (archive: {archive_run_name}) ===")


if __name__ == "__main__":
    main()
