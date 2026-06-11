"""
v84 구조 (Ridge + Seasonal Naive) AI Hub Training 데이터로 재학습
- Honda artifact의 input_scaler/target_scaler 구조 그대로 사용
- Ridge만 AI Hub 데이터로 새로 학습
- 설비별 중간 저장 + 이어서 돌리기 지원
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error
from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")

# ── 경로 설정 ──────────────────────────────────────────────────────────────────
AIHUB_TRAIN_BASE = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/1.Training/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
OUT_PATH = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_v84_retrain_per_device.csv"

HORIZON = 3
WINDOW_SIZE = 24
MIN_TRAIN_WINDOWS = 20
MIN_TEST_WINDOWS = 5
TRAIN_RATIO = 0.8  # 앞 80% 학습, 뒤 20% 테스트

ITEM_MAP = {
    "유효전력평균": "P",
    "상전압평균":   "U1",
    "역률평균":     "PF",
}

FEATURE_COLS = [
    "P", "U1", "PF",
    "hour_sin", "hour_cos", "day_of_week_sin", "day_of_week_cos",
    "month_sin", "month_cos",
    "diff_lag24", "diff_lag168", "is_workday", "rolling_mean_24h",
]
SCALE_COLS = ["P", "U1", "PF", "diff_lag24", "diff_lag168", "is_workday", "rolling_mean_24h"]


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


def resample_1h(df: pd.DataFrame) -> pd.DataFrame:
    g = df.set_index("ts")
    return pd.DataFrame({
        "P":     g["P"].resample("1h").mean().clip(lower=0),
        "U1":    g["U1"].resample("1h").mean(),
        "PF":    g["PF"].resample("1h").mean(),
        "label": g["label"].resample("1h").last(),
    }).dropna(subset=["P", "U1", "PF"]).reset_index()


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.sort_values("ts").copy().reset_index(drop=True)
    p = d["P"]
    hour = d["ts"].dt.hour
    dow  = d["ts"].dt.dayofweek
    mon  = d["ts"].dt.month
    d["hour_sin"]         = np.sin(2 * np.pi * hour / 24)
    d["hour_cos"]         = np.cos(2 * np.pi * hour / 24)
    d["day_of_week_sin"]  = np.sin(2 * np.pi * dow / 7)
    d["day_of_week_cos"]  = np.cos(2 * np.pi * dow / 7)
    d["month_sin"]        = np.sin(2 * np.pi * (mon - 1) / 12)
    d["month_cos"]        = np.cos(2 * np.pi * (mon - 1) / 12)
    d["is_workday"]       = (dow < 5).astype(float)
    d["diff_lag24"]       = p.diff(24)
    d["diff_lag168"]      = p.diff(168)
    d["rolling_mean_24h"] = p.rolling(24, min_periods=1).mean()
    return d


def build_windows(df: pd.DataFrame):
    X_list, y_list, meta_list = [], [], []
    df_c = df.dropna(subset=FEATURE_COLS + ["P"]).reset_index(drop=True)
    for i in range(WINDOW_SIZE, len(df_c) - HORIZON):
        x = df_c[FEATURE_COLS].iloc[i - WINDOW_SIZE:i].values
        y = df_c["P"].iloc[i:i + HORIZON].values.astype(np.float32)
        if np.isnan(x).any() or np.isnan(y).any():
            continue
        X_list.append(x)
        y_list.append(y)
        meta_list.append({
            "ts":          df_c["ts"].iloc[i],
            "label":       df_c["label"].iloc[i],
            "persistence": float(df_c["P"].iloc[i - 1]),
            "anchor_p":    float(df_c["P"].iloc[i - 1]),
        })
    if not X_list:
        return None, None, None
    return np.array(X_list, dtype=np.float32), np.array(y_list, dtype=np.float32), pd.DataFrame(meta_list)


def calc_mape(actual, pred):
    mask = np.abs(actual) > 1e-6
    if mask.sum() == 0:
        return np.nan
    return float(np.mean(np.abs((actual[mask] - pred[mask]) / actual[mask])) * 100)


# ── 메인 ──────────────────────────────────────────────────────────────────────
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

for device_id in tqdm(device_ids, desc="설비별 재학습(v84 간이)"):
    if device_id in done_ids:
        completed += 1
        continue

    fpath = AIHUB_TRAIN_BASE / f"Combined_LabelledData_{device_id}_역률평균.json"

    try:
        df_raw = load_device(fpath)
        if df_raw.empty:
            skipped += 1
            continue
        df = add_features(resample_1h(df_raw))
    except Exception as e:
        print(f"[SKIP] {device_id} 전처리 실패: {e}")
        skipped += 1
        continue

    X_all, y_all, meta_all = build_windows(df)
    if X_all is None:
        skipped += 1
        continue

    n = len(X_all)
    n_train = int(n * TRAIN_RATIO)

    if n_train < MIN_TRAIN_WINDOWS or (n - n_train) < MIN_TEST_WINDOWS:
        skipped += 1
        continue

    X_train = X_all[:n_train]
    y_train = y_all[:n_train]
    X_test  = X_all[n_train:]
    y_test  = y_all[n_train:]
    meta_test = meta_all.iloc[n_train:].reset_index(drop=True)

    try:
        # input_scaler: SCALE_COLS만 fit
        scale_idx = [FEATURE_COLS.index(c) for c in SCALE_COLS]
        X_train_2d = X_train.reshape(-1, len(FEATURE_COLS))
        X_test_2d  = X_test.reshape(-1,  len(FEATURE_COLS))

        input_scaler = StandardScaler()
        input_scaler.fit(X_train_2d[:, scale_idx])

        def scale_X(X_2d):
            X_out = X_2d.copy()
            X_out[:, scale_idx] = input_scaler.transform(X_2d[:, scale_idx])
            return X_out

        X_train_scaled = scale_X(X_train_2d).reshape(n_train, -1)
        X_test_scaled  = scale_X(X_test_2d).reshape(n - n_train, -1)

        # target: 잔차(P(t+h) - P(t-1)) 타겟
        anchor_train = meta_all["anchor_p"].values[:n_train]
        anchor_test  = meta_all["anchor_p"].values[n_train:]

        target_scaler = StandardScaler()
        all_residuals = (y_train - anchor_train[:, None]).flatten()
        target_scaler.fit(all_residuals.reshape(-1, 1))

        y_train_residual = y_train - anchor_train[:, None]
        y_train_scaled   = target_scaler.transform(
            y_train_residual.reshape(-1, 1)
        ).reshape(n_train, HORIZON)

        # Ridge 학습
        ridge = Ridge(alpha=1.0)
        ridge.fit(X_train_scaled, y_train_scaled)

        # 테스트 추론
        ridge_pred_scaled = ridge.predict(X_test_scaled)
        if ridge_pred_scaled.ndim == 1:
            ridge_pred_scaled = ridge_pred_scaled.reshape(-1, HORIZON)

        ridge_pred = target_scaler.inverse_transform(
            ridge_pred_scaled.reshape(-1, 1)
        ).reshape(-1, HORIZON) + anchor_test[:, None]

        # Seasonal Naive
        naive_pred = np.array([
            df["P"].dropna().values[max(0, i + n_train - 24): i + n_train - 24 + HORIZON]
            if i + n_train >= 24 else np.full(HORIZON, anchor_test[i])
            for i in range(len(X_test))
        ], dtype=np.float32)
        naive_pred = np.array([
            np.resize(row, HORIZON) for row in naive_pred
        ], dtype=np.float32)

        # Median 앙상블
        ensemble_pred = np.median(
            np.stack([ridge_pred, naive_pred], axis=0), axis=0
        ).astype(np.float32)

        persistence_pred = np.repeat(anchor_test[:, None], HORIZON, axis=1)

        ensemble_rmse    = float(np.sqrt(mean_squared_error(y_test, ensemble_pred)))
        persistence_rmse = float(np.sqrt(mean_squared_error(y_test, persistence_pred)))
        improvement      = (persistence_rmse - ensemble_rmse) / persistence_rmse * 100 if persistence_rmse > 0 else float("-inf")
        ensemble_mape    = calc_mape(y_test.flatten(), ensemble_pred.flatten())
        persistence_mape = calc_mape(y_test.flatten(), persistence_pred.flatten())

    except Exception as e:
        print(f"[SKIP] {device_id} 학습/추론 실패: {e}")
        skipped += 1
        continue

    meta_test["mae"]             = np.mean(np.abs(ensemble_pred - y_test), axis=1)
    meta_test["persistence_mae"] = np.mean(np.abs(persistence_pred - y_test), axis=1)

    row = {
        "device_id":        device_id,
        "n_train_windows":  n_train,
        "n_test_windows":   n - n_train,
        "ensemble_rmse":    ensemble_rmse,
        "persistence_rmse": persistence_rmse,
        "improvement_pct":  improvement,
        "ensemble_mape":    ensemble_mape,
        "persistence_mape": persistence_mape,
    }
    for label in ["정상", "주의", "경고"]:
        mask = meta_test["label"] == label
        if mask.sum() > 0:
            row[f"mae_{label}"]             = float(meta_test.loc[mask, "mae"].mean())
            row[f"persistence_mae_{label}"] = float(meta_test.loc[mask, "persistence_mae"].mean())
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