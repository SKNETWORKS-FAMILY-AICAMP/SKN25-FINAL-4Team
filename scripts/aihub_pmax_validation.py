"""
AI Hub 펌프_일반모터 데이터로 Import P-Max 구조 검증 (설비별 개인화)
- 설비별 따로 학습 후 결과 집계
- 1분 데이터 → 15분 리샘플링
- Import P-Max v29 구조(LightGBM×2 + XGBoost + CatBoost 앙상블)
- Persistence 대비 개선율 + MAPE 포함
"""

import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.multioutput import MultiOutputRegressor
from sklearn.metrics import mean_squared_error
from lightgbm import LGBMRegressor
from xgboost import XGBRegressor
from catboost import CatBoostRegressor
from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")

BASE = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/2.Validation/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"

ITEM_MAP = {
    "유효전력평균": "P",
    "상전압평균": "U1",
    "역률평균": "PF",
}

FEATURE_COLS = [
    "P_mean","P_max","P_std","U1_mean","PF_mean",
    "hour_sin","hour_cos","dayofweek_sin",
    "P_max_lag_1","P_max_lag_96",
    "P_max_diff_1","P_max_diff_4",
    "P_mean_diff_1","U1_mean_diff_1","PF_mean_diff_1",
    "P_max_roll_1h_mean","P_max_roll_1h_max","P_max_roll_1h_std",
    "P_max_roll_3h_mean","P_max_roll_3h_max","P_max_roll_6h_mean",
]
TARGET_COL = "P_max"
HORIZON = 4
WINDOW  = 96
MIN_WINDOWS = 20


def load_device(fpath: Path) -> pd.DataFrame:
    with open(fpath, "r") as f:
        data = json.load(f)
    rows = [
        {"ts": pd.Timestamp(d["TIMESTAMP"]), "item": ITEM_MAP[d["ITEM_NAME"]],
         "value": float(d["ITEM_VALUE"]), "label": d["LABEL_NAME"]}
        for d in data["data"] if d["ITEM_NAME"] in ITEM_MAP
    ]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.pivot_table(index=["ts","label"], columns="item", values="value", aggfunc="first").reset_index()
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
    d = df.sort_values("ts").reset_index(drop=True)
    p = d["P_max"]
    minute_of_day = d["ts"].dt.hour * 60 + d["ts"].dt.minute
    d["hour_sin"] = np.sin(2 * np.pi * minute_of_day / (24 * 60))
    d["hour_cos"] = np.cos(2 * np.pi * minute_of_day / (24 * 60))
    d["dayofweek_sin"] = np.sin(2 * np.pi * d["ts"].dt.dayofweek / 7)
    d["P_max_lag_1"]   = p.shift(1)
    d["P_max_lag_96"]  = p.shift(96)
    d["P_max_diff_1"]  = p.diff(1)
    d["P_max_diff_4"]  = p.diff(4)
    d["P_mean_diff_1"] = d["P_mean"].diff(1)
    d["U1_mean_diff_1"]= d["U1_mean"].diff(1)
    d["PF_mean_diff_1"]= d["PF_mean"].diff(1)
    d["P_max_roll_1h_mean"] = p.rolling(4,  min_periods=1).mean()
    d["P_max_roll_1h_max"]  = p.rolling(4,  min_periods=1).max()
    d["P_max_roll_1h_std"]  = p.rolling(4,  min_periods=2).std().fillna(0)
    d["P_max_roll_3h_mean"] = p.rolling(12, min_periods=1).mean()
    d["P_max_roll_3h_max"]  = p.rolling(12, min_periods=1).max()
    d["P_max_roll_6h_mean"] = p.rolling(24, min_periods=1).mean()
    return d


def build_windows(df: pd.DataFrame):
    df_c = df.dropna(subset=FEATURE_COLS + [TARGET_COL]).reset_index(drop=True)
    X_list, y_list, meta = [], [], []
    for i in range(WINDOW, len(df_c) - HORIZON):
        x = df_c[FEATURE_COLS].iloc[i-WINDOW:i].values.flatten()
        y = df_c[TARGET_COL].iloc[i:i+HORIZON].values
        if np.isnan(x).any() or np.isnan(y).any():
            continue
        X_list.append(x)
        y_list.append(y)
        meta.append({
            "ts": df_c["ts"].iloc[i],
            "label": df_c["label"].iloc[i],
            "persistence": float(df_c[TARGET_COL].iloc[i-1]),
        })
    if not X_list:
        return None, None, None
    return np.array(X_list, np.float32), np.array(y_list, np.float32), pd.DataFrame(meta)


def calc_mape(actual, pred):
    mask = np.abs(actual) > 1e-6
    if mask.sum() == 0:
        return np.nan
    return float(np.mean(np.abs((actual[mask] - pred[mask]) / actual[mask])) * 100)


def make_models():
    return {
        "lgbm_v20": MultiOutputRegressor(LGBMRegressor(n_estimators=300, learning_rate=0.03, num_leaves=31, n_jobs=-1, random_state=42, verbosity=-1)),
        "lgbm_v23": MultiOutputRegressor(LGBMRegressor(n_estimators=500, learning_rate=0.02, num_leaves=63, n_jobs=-1, random_state=43, verbosity=-1)),
        "xgb_v25":  MultiOutputRegressor(XGBRegressor(n_estimators=300, learning_rate=0.03, max_depth=6, n_jobs=-1, random_state=42, verbosity=0)),
        "cat_v27":  MultiOutputRegressor(CatBoostRegressor(iterations=400, learning_rate=0.03, depth=6, random_state=42, verbose=False, allow_writing_files=False)),
    }


# ── 메인 ──────────────────────────────────────────────────────────────────────
files = sorted(BASE.glob("Combined_LabelledData_*_역률평균.json"))
print(f"설비 파일 수: {len(files)}")

results = []
skipped = 0

for fpath in tqdm(files, desc="설비별 학습"):
    device_id = fpath.stem.split("_")[2]

    df_raw = load_device(fpath)
    if df_raw.empty:
        skipped += 1
        continue
    df15 = resample_15min(df_raw)
    df15 = add_features(df15)

    X, y, meta = build_windows(df15)
    if X is None or len(X) < MIN_WINDOWS:
        skipped += 1
        continue

    split = int(len(X) * 0.7)
    if split < 5 or len(X) - split < 5:
        skipped += 1
        continue

    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    meta_test = meta.iloc[split:].reset_index(drop=True)

    models = make_models()
    preds = {}
    try:
        for name, model in models.items():
            model.fit(X_train, y_train)
            preds[name] = model.predict(X_test)
    except Exception:
        skipped += 1
        continue

    ensemble_pred    = np.mean(list(preds.values()), axis=0)
    persistence_pred = np.repeat(meta_test["persistence"].values[:, None], HORIZON, axis=1)

    ensemble_rmse    = np.sqrt(mean_squared_error(y_test, ensemble_pred))
    persistence_rmse = np.sqrt(mean_squared_error(y_test, persistence_pred))
    improvement      = (persistence_rmse - ensemble_rmse) / persistence_rmse * 100
    ensemble_mape    = calc_mape(y_test.flatten(), ensemble_pred.flatten())
    persistence_mape = calc_mape(y_test.flatten(), persistence_pred.flatten())

    meta_test["mae"]             = np.mean(np.abs(ensemble_pred - y_test), axis=1)
    meta_test["persistence_mae"] = np.mean(np.abs(persistence_pred - y_test), axis=1)

    row = {
        "device_id": device_id,
        "n_windows": len(X),
        "ensemble_rmse": ensemble_rmse,
        "persistence_rmse": persistence_rmse,
        "improvement_pct": improvement,
        "ensemble_mape": ensemble_mape,
        "persistence_mape": persistence_mape,
    }
    for label in ["정상", "주의", "경고"]:
        mask = meta_test["label"] == label
        if mask.sum() > 0:
            row[f"mae_{label}"] = meta_test.loc[mask, "mae"].mean()
            row[f"persistence_mae_{label}"] = meta_test.loc[mask, "persistence_mae"].mean()
            row[f"n_{label}"] = int(mask.sum())
    results.append(row)

# ── 집계 ──────────────────────────────────────────────────────────────────────
print(f"\n스킵된 설비: {skipped}, 완료된 설비: {len(results)}")

if results:
    df_res = pd.DataFrame(results)
    print(f"\n=== 설비별 결과 요약 ===")
    print(f"  Persistence 대비 개선 설비: {(df_res['improvement_pct'] > 0).sum()} / {len(df_res)}")
    print(f"  평균 RMSE 개선율:  {df_res['improvement_pct'].mean():.1f}%")
    print(f"  중앙값 RMSE 개선율: {df_res['improvement_pct'].median():.1f}%")
    print(f"  평균 MAPE:         {df_res['ensemble_mape'].mean():.1f}%")
    print(f"  중앙값 MAPE:       {df_res['ensemble_mape'].median():.1f}%")
    print(f"  Persistence 평균 MAPE: {df_res['persistence_mape'].mean():.1f}%")

    print(f"\n=== 라벨별 집계 MAE ===")
    for label in ["정상", "주의", "경고"]:
        col_m = f"mae_{label}"
        col_p = f"persistence_mae_{label}"
        col_n = f"n_{label}"
        if col_m in df_res.columns:
            mae   = df_res[col_m].mean()
            p_mae = df_res[col_p].mean()
            n     = int(df_res[col_n].sum())
            imp   = (p_mae - mae) / p_mae * 100
            print(f"  {label} (총 {n}건): MAE={mae:.2f}, Persistence={p_mae:.2f}, 개선={imp:.1f}%")

    out_path = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_pmax_per_device.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df_res.to_csv(out_path, index=False)
    print(f"\n설비별 상세 결과: {out_path}")

print("\n완료.")