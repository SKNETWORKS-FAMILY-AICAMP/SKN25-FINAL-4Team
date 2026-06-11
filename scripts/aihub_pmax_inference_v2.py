"""
Import P-Max inference.py 기반 AI Hub Training 데이터 추론
- load_artifacts + predict_ensemble 직접 사용
- v84 inference.py 방식과 동일 조건
- 설비별 중간 저장 + 이어서 돌리기 지원
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_squared_error
from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")

# ── 경로 설정 ──────────────────────────────────────────────────────────────────
AIHUB_TRAIN_BASE = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/1.Training/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
PMAX_ROOT = Path.home() / "SKN25-FINAL-4Team/import_pmax_production_release_20260608"
OUT_PATH  = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_pmax_inference_v2_per_device.csv"

sys.path.insert(0, str(PMAX_ROOT / "src"))

PROXY_METER = "V.Z81"
HORIZON     = 4
WINDOW_SIZE = 96
MIN_HISTORY = 200
MIN_TEST_WINDOWS = 5

ITEM_MAP = {
    "유효전력평균": "P",
    "상전압평균":   "U1",
    "역률평균":     "PF",
}

# ── Import P-Max 모듈 임포트 ──────────────────────────────────────────────────
from forecasting.import_pmax import training
from forecasting.import_pmax.inference import (
    InferenceArtifacts,
    InferenceWindow,
    load_artifacts,
    predict_ensemble,
)

MODEL_ROOT = PMAX_ROOT / "artifacts" / "import_pmax_v29_60min"


# ── 데이터 로드 ────────────────────────────────────────────────────────────────
def load_device(fpath: Path) -> pd.DataFrame:
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
            "label": d.get("LABEL_NAME", None),
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.pivot_table(index=["ts", "label"], columns="item", values="value", aggfunc="first").reset_index()
    df.columns.name = None
    return df.sort_values("ts").reset_index(drop=True)


def resample_15min(df: pd.DataFrame) -> pd.DataFrame:
    g = df.set_index("ts")
    return pd.DataFrame({
        "P_mean":  g["P"].resample("15min").mean().clip(lower=0),
        "P_max":   g["P"].resample("15min").max().clip(lower=0),
        "P_std":   g["P"].resample("15min").std().fillna(0),
        "U1_mean": g["U1"].resample("15min").mean(),
        "PF_mean": g["PF"].resample("15min").mean(),
        "label":   g["label"].resample("15min").last(),
    }).dropna().reset_index()


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.sort_values("ts").copy().reset_index(drop=True)
    p = d["P_max"]
    minute_of_day = d["ts"].dt.hour * 60 + d["ts"].dt.minute
    d["hour_sin"]       = np.sin(2 * np.pi * minute_of_day / (24 * 60))
    d["hour_cos"]       = np.cos(2 * np.pi * minute_of_day / (24 * 60))
    d["dayofweek_sin"]  = np.sin(2 * np.pi * d["ts"].dt.dayofweek / 7)
    d["P_max_lag_1"]    = p.shift(1)
    d["P_max_lag_96"]   = p.shift(96)
    d["P_max_lag_192"]  = p.shift(192)
    d["P_max_diff_1"]   = p.diff(1)
    d["P_max_diff_4"]   = p.diff(4)
    d["P_mean_diff_1"]  = d["P_mean"].diff(1)
    d["U1_mean_diff_1"] = d["U1_mean"].diff(1)
    d["PF_mean_diff_1"] = d["PF_mean"].diff(1)
    d["P_max_roll_1h_mean"] = p.rolling(4,  min_periods=1).mean()
    d["P_max_roll_1h_max"]  = p.rolling(4,  min_periods=1).max()
    d["P_max_roll_1h_std"]  = p.rolling(4,  min_periods=2).std().fillna(0)
    d["P_max_roll_3h_mean"] = p.rolling(12, min_periods=1).mean()
    d["P_max_roll_3h_max"]  = p.rolling(12, min_periods=1).max()
    d["P_max_roll_6h_mean"] = p.rolling(24, min_periods=1).mean()
    # inference.py용 컬럼 추가
    d["source_meter_urn"] = PROXY_METER
    d["segment_id"] = 1
    d[training.INTERPOLATED_COLUMN]    = False
    d[training.FORWARD_FILLED_COLUMN]  = False
    d[training.INPUT_OBSERVED_COLUMN]  = True
    d[training.TARGET_OBSERVED_COLUMN] = True
    return d


def calc_mape(actual, pred):
    mask = np.abs(actual) > 1e-6
    if mask.sum() == 0:
        return np.nan
    return float(np.mean(np.abs((actual[mask] - pred[mask]) / actual[mask])) * 100)


# ── 메인 ──────────────────────────────────────────────────────────────────────
print(f"artifact 로드: {PROXY_METER}")
artifacts = load_artifacts(MODEL_ROOT, PROXY_METER)
print(f"weights: {artifacts.weights}")

train_files = sorted(AIHUB_TRAIN_BASE.glob("Combined_LabelledData_*_역률평균.json"))
device_ids  = sorted({f.stem.split("_")[2] for f in train_files})
print(f"AI Hub Training 설비: {len(device_ids)}개")

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
if OUT_PATH.exists():
    done_ids = set(pd.read_csv(OUT_PATH)["device_id"].astype(str))
    print(f"이미 완료: {len(done_ids)}개 — 이어서 실행")
else:
    done_ids = set()

skipped   = 0
completed = 0

for device_id in tqdm(device_ids, desc="설비별 추론(inference.py)"):
    if device_id in done_ids:
        completed += 1
        continue

    fpath = AIHUB_TRAIN_BASE / f"Combined_LabelledData_{device_id}_역률평균.json"

    try:
        df_raw = load_device(fpath)
        if df_raw.empty:
            skipped += 1
            continue
        df = add_features(resample_15min(df_raw))
    except Exception as e:
        print(f"[SKIP] {device_id} 전처리 실패: {e}")
        skipped += 1
        continue

    df_c = df.dropna(subset=training.FEATURE_COLUMNS + [training.TARGET_COLUMN]).reset_index(drop=True)
    if len(df_c) < MIN_HISTORY + HORIZON:
        skipped += 1
        continue

    # 전체 윈도우 배치 생성
    X_list, actual_list, persist_list2, meta_list = [], [], [], []

    for i in range(MIN_HISTORY, len(df_c) - HORIZON):
        window = df_c.iloc[i - WINDOW_SIZE:i]
        if len(window) < WINDOW_SIZE:
            continue
        actual      = df_c[training.TARGET_COLUMN].iloc[i:i + HORIZON].to_numpy(dtype=np.float32)
        persistence = float(df_c[training.TARGET_COLUMN].iloc[i - 1])
        if np.isnan(actual).any():
            continue
        x = window[training.FEATURE_COLUMNS].to_numpy(dtype=np.float32)
        if np.isnan(x).any():
            continue
        X_list.append(x.reshape(1, -1))
        actual_list.append(actual)
        persist_list2.append(np.full(HORIZON, persistence, dtype=np.float32))
        meta_list.append({
            "ts":          df_c["ts"].iloc[i],
            "label":       df_c["label"].iloc[i],
            "persistence": persistence,
        })

    if len(X_list) < MIN_TEST_WINDOWS:
        skipped += 1
        continue

    # 배치 앙상블 예측
    X_batch = np.vstack(X_list).astype(np.float32)
    try:
        ensemble_batch = np.zeros((len(X_batch), HORIZON), dtype=np.float64)
        for version, weight in artifacts.weights.items():
            model = artifacts.models[version]
            pred = training.predict_model(model, X_batch).astype(np.float64)
            if pred.ndim == 1:
                pred = pred.reshape(-1, HORIZON)
            ensemble_batch += weight * pred
        preds   = ensemble_batch.astype(np.float32)
        actuals  = np.array(actual_list,   dtype=np.float32)
        persists = np.array(persist_list2, dtype=np.float32)
        meta_df  = pd.DataFrame(meta_list)
    except Exception as e:
        print(f"[SKIP] {device_id} 배치 추론 실패: {e}")
        skipped += 1
        continue

    ensemble_rmse    = float(np.sqrt(mean_squared_error(actuals, preds)))
    persistence_rmse = float(np.sqrt(mean_squared_error(actuals, persists)))
    improvement      = (persistence_rmse - ensemble_rmse) / persistence_rmse * 100 if persistence_rmse > 0 else float("-inf")
    ensemble_mape    = calc_mape(actuals.flatten(), preds.flatten())
    persistence_mape = calc_mape(actuals.flatten(), persists.flatten())

    meta_df["mae"]             = np.mean(np.abs(preds   - actuals), axis=1)
    meta_df["persistence_mae"] = np.mean(np.abs(persists - actuals), axis=1)

    row = {
        "device_id":        device_id,
        "n_test_windows":   len(X_list),
        "ensemble_rmse":    ensemble_rmse,
        "persistence_rmse": persistence_rmse,
        "improvement_pct":  improvement,
        "ensemble_mape":    ensemble_mape,
        "persistence_mape": persistence_mape,
    }
    for label in ["정상", "주의", "경고"]:
        mask = meta_df["label"] == label
        if mask.sum() > 0:
            row[f"mae_{label}"]             = float(meta_df.loc[mask, "mae"].mean())
            row[f"persistence_mae_{label}"] = float(meta_df.loc[mask, "persistence_mae"].mean())
            row[f"n_{label}"]               = int(mask.sum())

    row_df = pd.DataFrame([row])
    if not OUT_PATH.exists():
        row_df.to_csv(OUT_PATH, index=False)
    else:
        row_df.to_csv(OUT_PATH, mode="a", header=False, index=False)

    completed += 1

# ── 집계 ──────────────────────────────────────────────────────────────────────
print(f"\n스킵: {skipped}, 완료: {completed}")

if OUT_PATH.exists():
    df_res = pd.read_csv(OUT_PATH)
    valid  = df_res[(df_res["persistence_rmse"] > 0) & (df_res["improvement_pct"] != float("-inf"))]
    print(f"\n=== 결과 요약 (유효 {len(valid)}/{len(df_res)}개) ===")
    print(f"  Persistence 대비 개선 설비: {(valid['improvement_pct'] > 0).sum()} / {len(valid)}")
    print(f"  중앙값 RMSE 개선율:         {valid['improvement_pct'].median():.1f}%")
    print(f"  중앙값 MAPE:                {valid['ensemble_mape'].median():.1f}%")
    print(f"  중앙값 Persistence MAPE:    {valid['persistence_mape'].median():.1f}%")
    print(f"\n결과 저장: {OUT_PATH}")

print("\n완료.")