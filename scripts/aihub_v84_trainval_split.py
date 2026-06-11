"""
v84 풀 파이프라인 AI Hub 재학습 - Training/Validation 파일 분리 방식
- AI Hub Training(27일치) 전체 = train + val
- AI Hub Validation(3~6일치) = test
- preprocessing.py 분할 함수 패치: Training 끝 시점 기준으로 분할
- Import P-Max와 동일한 검증 방식
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

# ── 경로 설정 ──────────────────────────────────────────────────────────────────
AIHUB_TRAIN_BASE = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/1.Training/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
AIHUB_VAL_BASE   = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/2.Validation/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
V84_ROOT = Path.home() / "SKN25-FINAL-4Team/share_test6_residual_v84_20260609"
OUT_PATH = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_v84_trainval_split_per_device.csv"

sys.path.insert(0, str(V84_ROOT))

HORIZON = 3
ITEM_MAP = {
    "유효전력평균": "P",
    "상전압평균":   "U1",
    "역률평균":     "PF",
}


# ── 데이터 로드 ────────────────────────────────────────────────────────────────
def load_file(fpath: Path) -> pd.DataFrame | None:
    if not fpath.exists():
        return None
    with open(fpath, "r") as f:
        data = json.load(f)
    rows = []
    for d in data["data"]:
        if d["ITEM_NAME"] not in ITEM_MAP:
            continue
        if d.get("ITEM_VALUE") is None:
            continue
        try:
            val = float(d["ITEM_VALUE"])
        except (ValueError, TypeError):
            continue
        rows.append({
            "ts":    pd.Timestamp(d["TIMESTAMP"]),
            "item":  ITEM_MAP[d["ITEM_NAME"]],
            "value": val,
        })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df = df.pivot_table(index="ts", columns="item", values="value", aggfunc="first").reset_index()
    df.columns.name = None
    return df.sort_values("ts").reset_index(drop=True)


def resample_1h(df: pd.DataFrame, device_id: str) -> pd.DataFrame:
    g = df.set_index("ts")
    result = pd.DataFrame({
        "P":  g["P"].resample("1h").mean().clip(lower=0),
        "U1": g["U1"].resample("1h").mean(),
        "PF": g["PF"].resample("1h").mean(),
    }).dropna().reset_index()
    result["meter_urn"] = device_id
    return result


# ── preprocessing.py 분할 패치 ────────────────────────────────────────────────
import test6_residual.pipeline.common.preprocessing as _prep_module

_original_determine_split_bounds = _prep_module.determine_split_bounds
_TRAIN_END_TS: dict[str, pd.Timestamp] = {}  # device_id → training 끝 시점


def _patched_determine_split_bounds(frame):
    from test6_residual.pipeline.common.config import TRAIN_START, END_TS
    min_ts = frame["ts"].min()
    max_ts = frame["ts"].max()
    # Honda 원본이면 기존 방식
    if min_ts <= TRAIN_START and max_ts >= END_TS - pd.Timedelta(hours=1):
        return _original_determine_split_bounds(frame)
    # AI Hub: _TRAIN_END_TS에 등록된 경우 해당 시점 기준으로 분할
    for device_id, train_end in _TRAIN_END_TS.items():
        if abs((frame["ts"].max() - max_ts).total_seconds()) < 3600:
            # val = train_end ~ train_end + (train_end - min_ts) * 1/5
            train_span = train_end - min_ts
            val_start  = train_end - train_span * (1/5)
            return {
                "train_start": min_ts,
                "val_start":   val_start,
                "test_start":  train_end,
                "end":         max_ts,
            }
    return _original_determine_split_bounds(frame)

_prep_module.determine_split_bounds = _patched_determine_split_bounds


# ── fetch_meter_frame 패치 ─────────────────────────────────────────────────────
import test6_residual.pipeline.common.db as _db_module

_AIHUB_DATA: dict[str, pd.DataFrame] = {}

def _patched_fetch_meter_frame(engine, spec, **kwargs):
    urn = spec.meter_urn
    if urn in _AIHUB_DATA:
        return _AIHUB_DATA[urn]
    raise ValueError(f"AI Hub 데이터 없음: {urn}")

_db_module.fetch_meter_frame = _patched_fetch_meter_frame
import test6_residual.pipeline.train as _train_module
_train_module.fetch_meter_frame = _patched_fetch_meter_frame


# ── train.py 임포트 ────────────────────────────────────────────────────────────
from test6_residual.pipeline.common.config import MeterSpec, EPOCHS, BATCH_SIZE, SEED
import test6_residual.pipeline.common.config as _cfg
from test6_residual.pipeline.common.model import set_seed
from test6_residual.pipeline.train import (
    pass1_train_meter, pass2_stage1, pass2_finalize, select_v19,
)
from test6_residual.pipeline.common.router import v63_route_group


class Args:
    horizon    = HORIZON
    epochs     = EPOCHS
    batch_size = BATCH_SIZE
    seed       = SEED


# ── 메인 ──────────────────────────────────────────────────────────────────────
set_seed(SEED)
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

train_files = sorted(AIHUB_TRAIN_BASE.glob("Combined_LabelledData_*_역률평균.json"))
val_files   = sorted(AIHUB_VAL_BASE.glob("Combined_LabelledData_*_역률평균.json"))
train_ids   = sorted({f.stem.split("_")[2] for f in train_files})
val_ids     = sorted({f.stem.split("_")[2] for f in val_files})
common_ids  = sorted(set(train_ids) & set(val_ids))
print(f"Training 설비: {len(train_ids)}, Validation 설비: {len(val_ids)}, 공통: {len(common_ids)}")

if OUT_PATH.exists():
    done_ids = set(pd.read_csv(OUT_PATH)["device_id"].astype(str))
    print(f"이미 완료: {len(done_ids)}개 — 이어서 실행")
else:
    done_ids = set()

args = Args()
skipped   = 0
completed = 0

for device_id in common_ids:
    if device_id in done_ids:
        completed += 1
        continue

    print(f"\n[{device_id}] 학습 시작", flush=True)

    # Training + Validation 데이터 로드
    train_df = load_file(AIHUB_TRAIN_BASE / f"Combined_LabelledData_{device_id}_역률평균.json")
    val_df   = load_file(AIHUB_VAL_BASE   / f"Combined_LabelledData_{device_id}_역률평균.json")
    if train_df is None or val_df is None:
        skipped += 1
        continue

    train_1h = resample_1h(train_df, device_id)
    val_1h   = resample_1h(val_df,   device_id)
    if len(train_1h) < 50 or len(val_1h) < 10:
        skipped += 1
        continue

    # Training 끝 시점 등록 (UTC aware로 변환)
    train_end = train_1h["ts"].max()
    if train_end.tzinfo is None:
        train_end = train_end.tz_localize("UTC")
    _TRAIN_END_TS[device_id] = train_end

    # Training + Validation 합치기 (test = Validation 구간)
    # ts를 UTC aware로 통일
    train_1h["ts"] = train_1h["ts"].dt.tz_localize("UTC") if train_1h["ts"].dt.tz is None else train_1h["ts"]
    val_1h["ts"]   = val_1h["ts"].dt.tz_localize("UTC")   if val_1h["ts"].dt.tz is None   else val_1h["ts"]
    combined = pd.concat([train_1h, val_1h], ignore_index=True).sort_values("ts").reset_index(drop=True)
    _AIHUB_DATA[device_id] = combined

    # MeterSpec 등록
    spec = MeterSpec(device_id, "electric", "singleton", ("P", "U1", "PF"), "aihub", "AI Hub device")
    _cfg.METER_SPECS_BY_URN[device_id] = spec

    # artifact 저장 경로
    art_root = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_v84_artifacts_v3"
    import test6_residual.pipeline.common.config as _cfg2
    import test6_residual.pipeline.common.artifacts as _art
    _orig_art = _cfg2.ARTIFACTS_DIR
    _cfg2.ARTIFACTS_DIR = art_root
    _art.ARTIFACTS_DIR  = art_root

    try:
        pass1_data = pass1_train_meter(None, spec, HORIZON, args)

        v19 = select_v19({device_id: pass1_data}, "electric", "singleton")
        stage1_data = pass2_stage1(pass1_data, args, v19, HORIZON)

        v57_preds = {device_id: stage1_data["v57"]}
        v61_preds = {device_id: stage1_data["v61"]} if stage1_data.get("v61") is not None else {}
        routing   = v63_route_group(v57_preds, v61_preds if v61_preds else None, "electric", HORIZON, [spec])
        v63_version = routing.get(device_id, "v57")

        v63_entry = stage1_data[v63_version]
        v63_vp = v63_entry["val_pred_scaled"]
        v63_tp = v63_entry["test_pred_scaled"]

        row = pass2_finalize(pass1_data, stage1_data, v63_vp, v63_tp, v63_version, args)
        row["device_id"] = device_id

        row_df = pd.DataFrame([row])
        if not OUT_PATH.exists():
            row_df.to_csv(OUT_PATH, index=False)
        else:
            row_df.to_csv(OUT_PATH, mode="a", header=False, index=False)

        completed += 1
        print(f"[{device_id}] 완료 — val_mae={row.get('val_mae', 'N/A'):.1f}", flush=True)

    except Exception as e:
        print(f"[{device_id}] 실패: {e}", flush=True)
        traceback.print_exc()
        skipped += 1
    finally:
        _cfg2.ARTIFACTS_DIR = _orig_art
        _art.ARTIFACTS_DIR  = _orig_art
        _AIHUB_DATA.pop(device_id, None)
        _TRAIN_END_TS.pop(device_id, None)

# ── 집계 ──────────────────────────────────────────────────────────────────────
print(f"\n스킵: {skipped}, 완료: {completed}")

if OUT_PATH.exists():
    df_res = pd.read_csv(OUT_PATH)
    valid  = df_res[df_res["test_rmse"].notna()]
    print(f"\n=== 결과 요약 (유효 {len(valid)}/{len(df_res)}개) ===")
    print(f"  beats_persistence: {valid['beats_persistence'].sum()} / {len(valid)}")
    print(f"  중앙값 test_rmse:  {valid['test_rmse'].median():.1f}")
    print(f"  중앙값 test_mape:  {valid['test_mape'].median():.1f}%")
    print(f"  중앙값 persistence_mae: {valid['persistence_mae'].median():.1f}")

print("\n완료.")