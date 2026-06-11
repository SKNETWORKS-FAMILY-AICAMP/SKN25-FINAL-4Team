"""
AI Hub 펌프_일반모터 Training 데이터로 Import P-Max 구조 검증 (설비별 개인화)
- Training 데이터로 학습, Validation 데이터로 테스트
- 1분 데이터 → 15분 리샘플링
- Import P-Max v29 구조(LightGBM×2 + XGBoost + CatBoost 앙상블)
- Persistence 대비 개선율 + MAPE
- 중간 저장 + 이어서 돌리기 지원
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

TRAIN_BASE = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/1.Training/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
VAL_BASE   = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/2.Validation/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"

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

OUT_PATH = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_pmax_training_per_device.csv"


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
            "ts": pd.Timestamp(d["TIMESTAMP"]),
            "item": ITEM_MAP[d["ITEM_NAME"]],
            "value": val,
            "label": d.get("LABEL_NAME", None),
        })
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
train_files = sorted(TRAIN_BASE.glob("Combined_LabelledData_*_역률평균.json"))
val_files   = sorted(VAL_BASE.glob("Combined_LabelledData_*_역률평균.json"))

train_ids = {f.stem.split("_")[2] for f in train_files}
val_ids   = {f.stem.split("_")[2] for f in val_files}
common_ids = sorted(train_ids & val_ids)

print(f"Training 설비: {len(train_ids)}, Validation 설비: {len(val_ids)}, 공통: {len(common_ids)}")

# 중간 저장 파일 확인 — 이미 완료된 설비 스킵
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
if OUT_PATH.exists():
    done_ids = set(pd.read_csv(OUT_PATH)["device_id"].astype(str))
    print(f"이미 완료된 설비: {len(done_ids)}개 — 이어서 실행")
else:
    done_ids = set()

skipped = 0
completed = 0

for device_id in tqdm(common_ids, desc="설비별 학습"):
    if device_id in done_ids:
        completed += 1
        continue

    train_path = TRAIN_BASE / f"Combined_LabelledData_{device_id}_역률평균.json"
    val_path   = VAL_BASE   / f"Combined_LabelledData_{device_id}_역률평균.json"

    try:
        df_train_raw = load_device(train_path)
        df_val_raw   = load_device(val_path)
    except Exception as e:
        print(f"[SKIP] {device_id} 파일 로드 실패: {e}")
        skipped += 1
        continue

    if df_train_raw.empty or df_val_raw.empty:
        skipped += 1
        continue

    df_train = add_features(resample_15min(df_train_raw))
    df_val   = add_features(resample_15min(df_val_raw))

    X_train, y_train, _ = build_windows(df_train)
    X_test,  y_test,  meta_test = build_windows(df_val)

    if X_train is None or X_test is None or len(X_train) < MIN_WINDOWS or len(X_test) < 5:
        skipped += 1
        continue

    meta_test = meta_test.reset_index(drop=True)
    models = make_models()
    preds = {}
    try:
        for name, model in models.items():
            model.fit(X_train, y_train)
            preds[name] = model.predict(X_test)
    except Exception as e:
        print(f"[SKIP] {device_id} 학습 실패: {e}")
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
        "n_train_windows": len(X_train),
        "n_test_windows": len(X_test),
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

    # 설비 하나 끝날 때마다 즉시 저장
    row_df = pd.DataFrame([row])
    if not OUT_PATH.exists():
        row_df.to_csv(OUT_PATH, index=False)
    else:
        row_df.to_csv(OUT_PATH, mode='a', header=False, index=False)

    completed += 1
    del models, preds

# ── 집계 ──────────────────────────────────────────────────────────────────────
print(f"\n스킵된 설비: {skipped}, 완료된 설비: {completed}")

if OUT_PATH.exists():
    df_res = pd.read_csv(OUT_PATH)
    print(f"\n=== 설비별 결과 요약 ===")
    print(f"  Persistence 대비 개선 설비: {(df_res['improvement_pct'] > 0).sum()} / {len(df_res)}")
    print(f"  평균 RMSE 개선율:      {df_res['improvement_pct'].mean():.1f}%")
    print(f"  중앙값 RMSE 개선율:    {df_res['improvement_pct'].median():.1f}%")
    print(f"  평균 MAPE:             {df_res['ensemble_mape'].mean():.1f}%")
    print(f"  중앙값 MAPE:           {df_res['ensemble_mape'].median():.1f}%")
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

    print(f"\n설비별 상세 결과: {OUT_PATH}")

print("\n완료.")