"""
v84 풀 파이프라인 (LSTM + CatBoost + LightGBM + Ridge) AI Hub 재학습
- fetch_meter_frame 대신 AI Hub JSON 로더 사용
- train.py의 pass1/pass2 구조 그대로 활용
- 설비 1개씩 순서대로 학습 + 중간 저장
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
V84_ROOT = Path.home() / "SKN25-FINAL-4Team/share_test6_residual_v84_20260609"
OUT_PATH = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_v84_fullretrain_per_device.csv"
ARTIFACTS_OUT = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_v84_artifacts"

sys.path.insert(0, str(V84_ROOT))

HORIZON = 3
ITEM_MAP = {
    "유효전력평균": "P",
    "상전압평균":   "U1",
    "역률평균":     "PF",
}

# ── AI Hub 데이터 로더 ─────────────────────────────────────────────────────────
def load_aihub_as_meter_frame(device_id: str) -> pd.DataFrame | None:
    """AI Hub JSON → fetch_meter_frame과 동일한 형태의 DataFrame 반환"""
    fpath = AIHUB_TRAIN_BASE / f"Combined_LabelledData_{device_id}_역률평균.json"
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
    df = df.sort_values("ts").reset_index(drop=True)

    # 1시간 리샘플링
    g = df.set_index("ts")
    result = pd.DataFrame({
        "P":  g["P"].resample("1h").mean().clip(lower=0),
        "U1": g["U1"].resample("1h").mean(),
        "PF": g["PF"].resample("1h").mean(),
    }).dropna().reset_index()

    # meter_urn 컬럼 추가 (train.py 호환)
    result["meter_urn"] = device_id
    return result


# ── train.py 임포트 ────────────────────────────────────────────────────────────
from test6_residual.pipeline.common.config import (
    ARTIFACTS_DIR,
    BATCH_SIZE,
    EPOCHS,
    LSTM_VARIANTS,
    SEED,
    MeterSpec,
)
from test6_residual.pipeline.common.preprocessing import build_windows, prepare_model_frame
from test6_residual.pipeline.common.model import (
    RecurrentPredictor,
    build_prediction_frame,
    device,
    evaluate_objective_scaled,
    inverse_target,
    make_loss_fn,
    predict_scaled,
    set_seed,
    thresholds_from_validation,
    train_lstm,
    HIDDEN_SIZE,
    LEARNING_RATE,
    PATIENCE,
)
from test6_residual.pipeline.common.catboost_model import fit_catboost, predict_catboost_scaled
from test6_residual.pipeline.common.lightgbm_model import fit_lightgbm, predict_lightgbm_scaled, save_lightgbm
from test6_residual.pipeline.common.ridge import fit_ridge, predict_ridge_scaled
from test6_residual.pipeline.common.naive import predict_seasonal_naive
from test6_residual.pipeline.common.ensemble import (
    build_median_ensemble,
    fit_shrunk_hour_bias_corrections,
    apply_hour_bias_corrections,
    compute_metrics,
    compute_persistence_mae,
)
from test6_residual.pipeline.common.artifacts import (
    save_lstm_model,
    save_catboost,
    save_ridge,
    save_scalers,
    save_routing,
    save_bias_corrections,
    save_feature_columns,
)
from test6_residual.pipeline.train import (
    pass1_train_meter,
    pass2_stage1,
    pass2_finalize,
    _make_bundle,
    select_v19,
)
from test6_residual.pipeline.common.router import v63_route_group
import argparse
import torch

# ── MeterSpec 생성 헬퍼 ────────────────────────────────────────────────────────
def make_aihub_spec(device_id: str) -> MeterSpec:
    return MeterSpec(
        meter_urn=device_id,
        group="electric",
        role="singleton",
        features=("P", "U1", "PF"),
        source="aihub",
        note="AI Hub pump/motor device",
    )


# ── fetch_meter_frame 패치 ─────────────────────────────────────────────────────
import test6_residual.pipeline.common.db as _db_module

_AIHUB_CACHE: dict[str, pd.DataFrame] = {}

def _patched_fetch_meter_frame(engine, spec: MeterSpec, **kwargs) -> pd.DataFrame:
    urn = spec.meter_urn
    if urn in _AIHUB_CACHE:
        return _AIHUB_CACHE[urn]
    df = load_aihub_as_meter_frame(urn)
    if df is None or df.empty:
        raise ValueError(f"AI Hub 데이터 없음: {urn}")
    _AIHUB_CACHE[urn] = df
    return df

_db_module.fetch_meter_frame = _patched_fetch_meter_frame

# train.py 내부에서도 교체
import test6_residual.pipeline.train as _train_module
_train_module.fetch_meter_frame = _patched_fetch_meter_frame


# ── 아규먼트 ──────────────────────────────────────────────────────────────────
class Args:
    horizon    = HORIZON
    epochs     = EPOCHS
    batch_size = BATCH_SIZE
    seed       = SEED
    meters     = None
    groups     = None


# ── 메인 ──────────────────────────────────────────────────────────────────────
set_seed(SEED)
ARTIFACTS_OUT.mkdir(parents=True, exist_ok=True)
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

train_files = sorted(AIHUB_TRAIN_BASE.glob("Combined_LabelledData_*_역률평균.json"))
device_ids  = sorted({f.stem.split("_")[2] for f in train_files})
print(f"AI Hub Training 설비: {len(device_ids)}개")

if OUT_PATH.exists():
    done_ids = set(pd.read_csv(OUT_PATH)["device_id"].astype(str))
    print(f"이미 완료: {len(done_ids)}개 — 이어서 실행")
else:
    done_ids = set()

args = Args()
skipped   = 0
completed = 0

for device_id in device_ids:
    if device_id in done_ids:
        completed += 1
        continue

    print(f"\n[{device_id}] 학습 시작", flush=True)
    spec = make_aihub_spec(device_id)

    # artifact 저장 경로를 설비별로 설정
    meter_art_dir = ARTIFACTS_OUT / f"{HORIZON}h" / device_id
    meter_art_dir.mkdir(parents=True, exist_ok=True)

    # ARTIFACTS_DIR 임시 교체
    import test6_residual.pipeline.common.config as _cfg
    import test6_residual.pipeline.common.artifacts as _art
    _original_artifacts_dir = _cfg.ARTIFACTS_DIR
    _cfg.ARTIFACTS_DIR = ARTIFACTS_OUT
    _art.ARTIFACTS_DIR = ARTIFACTS_OUT

    try:
        # 패스 1: LSTM 학습
        pass1_data = pass1_train_meter(None, spec, HORIZON, args)

        # v19 결정 (singleton 단일이라 단순)
        v19 = select_v19({device_id: pass1_data}, "electric", "singleton")

        # 패스 2a
        stage1_data = pass2_stage1(pass1_data, args, v19, HORIZON)

        # v63 라우팅
        v57_preds = {device_id: stage1_data["v57"]}
        v61_preds = {device_id: stage1_data["v61"]} if stage1_data.get("v61") is not None else {}
        routing = v63_route_group(
            v57_preds,
            v61_preds if v61_preds else None,
            "electric", HORIZON, [spec],
        )
        v63_version = routing.get(device_id, "v57")

        # v63 pred 추출 (scaled 공간)
        v63_entry = stage1_data[v63_version]
        v63_vp = v63_entry["val_pred_scaled"]
        v63_tp = v63_entry["test_pred_scaled"]

        # 패스 2b: v84 앙상블 저장
        row = pass2_finalize(
            pass1_data, stage1_data,
            v63_vp, v63_tp,
            v63_version, args,
        )

        row["device_id"] = device_id
        row_df = pd.DataFrame([row])
        if not OUT_PATH.exists():
            row_df.to_csv(OUT_PATH, index=False)
        else:
            row_df.to_csv(OUT_PATH, mode="a", header=False, index=False)

        completed += 1
        improvement = row.get('rmse_improvement_pct', 'N/A')
        print(f"[{device_id}] 완료 — RMSE 개선율: {improvement}", flush=True)

    except Exception as e:
        print(f"[{device_id}] 실패: {e}", flush=True)
        traceback.print_exc()
        skipped += 1
    finally:
        _cfg.ARTIFACTS_DIR = _original_artifacts_dir
        _art.ARTIFACTS_DIR = _original_artifacts_dir
        _AIHUB_CACHE.clear()

# ── 집계 ──────────────────────────────────────────────────────────────────────
print(f"\n스킵: {skipped}, 완료: {completed}")

if OUT_PATH.exists():
    df_res = pd.read_csv(OUT_PATH)
    valid  = df_res[df_res["test_rmse"].notna()]
    print(f"\n=== 결과 요약 (유효 {len(valid)}/{len(df_res)}개) ===")
    print(f"  beats_persistence: {valid['beats_persistence'].sum()} / {len(valid)}")
    print(f"  중앙값 test_rmse:  {valid['test_rmse'].median():.1f}")
    print(f"  중앙값 test_mape:  {valid['test_mape'].median():.1f}%")
    print(f"  중앙값 val_mae:    {valid['val_mae'].median():.1f}")

print("\n완료.")