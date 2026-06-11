"""
Honda v29 가중치 그대로 AI Hub Training 데이터에 추론 (MinMaxScaler 정규화)
- AI Hub Training 데이터 전체로 MinMaxScaler fit
- 정규화된 피처로 Honda 가중치 추론
- 평가 지표: RMSE 개선율 + MAPE + 방향 일치율
- 설비별 중간 저장 + 이어서 돌리기 지원
"""

import json
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from sklearn.metrics import mean_squared_error
from sklearn.preprocessing import MinMaxScaler
from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")

# ── 경로 설정 ──────────────────────────────────────────────────────────────────
ARTIFACTS_ROOT = Path.home() / "SKN25-FINAL-4Team/import_pmax_production_release_20260608/artifacts/import_pmax_v29_60min/input_24h/predict_60min"
TRAIN_BASE = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/1.Training/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
OUT_PATH = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_pmax_honda_inference_Minmax_per_device.csv"

METER = "V.Z81"

ITEM_MAP = {
    "유효전력평균": "P",
    "상전압평균":   "U1",
    "역률평균":     "PF",
}

FEATURE_COLS = [
    "P_mean", "P_max", "P_std", "U1_mean", "PF_mean",
    "hour_sin", "hour_cos", "dayofweek_sin",
    "P_max_lag_1", "P_max_lag_96", "P_max_lag_192",
    "P_max_roll_1h_mean", "P_max_roll_1h_max", "P_max_roll_1h_std",
    "P_max_roll_3h_mean", "P_max_roll_3h_max", "P_max_roll_6h_mean",
    "P_max_diff_1", "P_max_diff_4",
    "P_mean_diff_1", "U1_mean_diff_1", "PF_mean_diff_1",
]
TARGET_COL = "P_max"
HORIZON    = 4
WINDOW     = 96
MIN_TEST_WINDOWS = 5


def load_artifacts(meter: str):
    meter_dir = ARTIFACTS_ROOT / meter
    weights_df = pd.read_csv(meter_dir / "v29" / "ensemble_weights.csv")
    weights = dict(zip(weights_df["candidate_version"], weights_df["weight"]))
    models = {}
    for ver in ["v20", "v23", "v25", "v27"]:
        models[ver] = joblib.load(meter_dir / "_candidate_models" / f"{ver}.joblib")
    return models, weights


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
    d = df.sort_values("ts").reset_index(drop=True)
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
    return d


def build_windows(df: pd.DataFrame):
    df_c = df.dropna(subset=FEATURE_COLS + [TARGET_COL]).reset_index(drop=True)
    X_list, y_list, meta = [], [], []
    for i in range(WINDOW, len(df_c) - HORIZON):
        x = df_c[FEATURE_COLS].iloc[i - WINDOW:i].values.flatten()
        y = df_c[TARGET_COL].iloc[i:i + HORIZON].values
        if np.isnan(x).any() or np.isnan(y).any():
            continue
        X_list.append(x)
        y_list.append(y)
        meta.append({
            "ts":          df_c["ts"].iloc[i],
            "label":       df_c["label"].iloc[i],
            "persistence": float(df_c[TARGET_COL].iloc[i - 1]),
            "actual_next": float(df_c[TARGET_COL].iloc[i]),
        })
    if not X_list:
        return None, None, None
    return np.array(X_list, np.float32), np.array(y_list, np.float32), pd.DataFrame(meta)


def calc_mape(actual, pred):
    mask = np.abs(actual) > 1e-6
    if mask.sum() == 0:
        return np.nan
    return float(np.mean(np.abs((actual[mask] - pred[mask]) / actual[mask])) * 100)


def calc_directional_accuracy(actual, pred, persistence):
    actual_dir = np.sign(actual[:, 0] - persistence)
    pred_dir   = np.sign(pred[:, 0]   - persistence)
    mask = actual_dir != 0
    if mask.sum() == 0:
        return np.nan
    return float((actual_dir[mask] == pred_dir[mask]).mean() * 100)


# ── 메인 ──────────────────────────────────────────────────────────────────────
print(f"Honda 모델 로드 중: {METER}")
models, weights = load_artifacts(METER)
print(f"모델 로드 완료: {list(models.keys())}")

train_files = sorted(TRAIN_BASE.glob("Combined_LabelledData_*_역률평균.json"))
train_ids   = sorted({f.stem.split("_")[2] for f in train_files})
print(f"Training 설비: {len(train_ids)}개")

# ── MinMaxScaler fit (전체 Training 데이터 기준) ────────────────────────────
print("MinMaxScaler fit 중 (전체 Training 데이터)...")
all_X = []
for device_id in tqdm(train_ids, desc="scaler fit용 데이터 수집"):
    fpath = TRAIN_BASE / f"Combined_LabelledData_{device_id}_역률평균.json"
    try:
        df_raw = load_device(fpath)
        if df_raw.empty:
            continue
        df_feat = add_features(resample_15min(df_raw))
        df_c = df_feat.dropna(subset=FEATURE_COLS + [TARGET_COL])
        if len(df_c) < WINDOW + HORIZON:
            continue
        all_X.append(df_c[FEATURE_COLS].values)
    except Exception:
        continue

if not all_X:
    raise RuntimeError("scaler fit용 데이터 없음")

all_X_concat = np.vstack(all_X)
scaler = MinMaxScaler()
scaler.fit(all_X_concat)
print(f"MinMaxScaler fit 완료 (총 {len(all_X_concat)}행)")

# ── 설비별 추론 ────────────────────────────────────────────────────────────────
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
if OUT_PATH.exists():
    done_ids = set(pd.read_csv(OUT_PATH)["device_id"].astype(str))
    print(f"이미 완료된 설비: {len(done_ids)}개 — 이어서 실행")
else:
    done_ids = set()

skipped   = 0
completed = 0

for device_id in tqdm(train_ids, desc="설비별 추론(Minmax)"):
    if device_id in done_ids:
        completed += 1
        continue

    fpath = TRAIN_BASE / f"Combined_LabelledData_{device_id}_역률평균.json"

    try:
        df_raw = load_device(fpath)
        if df_raw.empty:
            skipped += 1
            continue
        df_feat = add_features(resample_15min(df_raw))
    except Exception as e:
        print(f"[SKIP] {device_id} 전처리 실패: {e}")
        skipped += 1
        continue

    X_test, y_test, meta_test = build_windows(df_feat)

    if X_test is None or len(X_test) < MIN_TEST_WINDOWS:
        skipped += 1
        continue

    # MinMaxScaler 적용 — 피처 단위(22개)로 transform 후 다시 flatten
    n_windows = X_test.shape[0]
    n_features = len(FEATURE_COLS)
    X_2d = X_test.reshape(n_windows * WINDOW, n_features)
    X_scaled_2d = scaler.transform(X_2d).astype(np.float32)
    X_scaled = X_scaled_2d.reshape(n_windows, WINDOW * n_features)

    meta_test = meta_test.reset_index(drop=True)

    try:
        preds = {}
        for ver, model in models.items():
            if isinstance(model, list):
                horizon_preds = [est.predict(X_scaled) for est in model]
                preds[ver] = np.column_stack(horizon_preds)
            else:
                preds[ver] = model.predict(X_scaled)

        ensemble_pred = np.zeros_like(list(preds.values())[0])
        total_w = sum(weights.values())
        for ver, pred in preds.items():
            ensemble_pred += pred * (weights[ver] / total_w)

        persistence_pred = np.repeat(meta_test["persistence"].values[:, None], HORIZON, axis=1)

        ensemble_rmse    = np.sqrt(mean_squared_error(y_test, ensemble_pred))
        persistence_rmse = np.sqrt(mean_squared_error(y_test, persistence_pred))
        improvement      = (persistence_rmse - ensemble_rmse) / persistence_rmse * 100 if persistence_rmse > 0 else float("-inf")
        ensemble_mape    = calc_mape(y_test.flatten(), ensemble_pred.flatten())
        persistence_mape = calc_mape(y_test.flatten(), persistence_pred.flatten())
        dir_acc          = calc_directional_accuracy(y_test, ensemble_pred, meta_test["persistence"].values)

    except Exception as e:
        print(f"[SKIP] {device_id} 추론 실패: {e}")
        skipped += 1
        continue

    meta_test["mae"]             = np.mean(np.abs(ensemble_pred - y_test), axis=1)
    meta_test["persistence_mae"] = np.mean(np.abs(persistence_pred - y_test), axis=1)

    row = {
        "device_id":        device_id,
        "n_test_windows":   len(X_test),
        "ensemble_rmse":    ensemble_rmse,
        "persistence_rmse": persistence_rmse,
        "improvement_pct":  improvement,
        "ensemble_mape":    ensemble_mape,
        "persistence_mape": persistence_mape,
        "directional_acc":  dir_acc,
    }
    for label in ["정상", "주의", "경고"]:
        mask = meta_test["label"] == label
        if mask.sum() > 0:
            row[f"mae_{label}"]             = meta_test.loc[mask, "mae"].mean()
            row[f"persistence_mae_{label}"] = meta_test.loc[mask, "persistence_mae"].mean()
            row[f"n_{label}"]               = int(mask.sum())

    row_df = pd.DataFrame([row])
    if not OUT_PATH.exists():
        row_df.to_csv(OUT_PATH, index=False)
    else:
        row_df.to_csv(OUT_PATH, mode="a", header=False, index=False)

    completed += 1
    del preds

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
    print(f"  중앙값 방향 일치율:         {valid['directional_acc'].median():.1f}%")
    print(f"\n결과 저장: {OUT_PATH}")

print("\n완료.")