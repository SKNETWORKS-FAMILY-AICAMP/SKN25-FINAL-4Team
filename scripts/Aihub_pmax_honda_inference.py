"""
Honda v29 가중치 그대로 AI Hub Validation 데이터에 추론
- Honda 가중치(v20/v23/v25/v27 joblib) 로드
- AI Hub Validation 데이터에서 22개 피처 생성
- lag_192(48시간 전) 생성 불가 설비는 스킵
- 평가 지표: RMSE 개선율 + MAPE + 방향 일치율
- 설비별 중간 저장 + 이어서 돌리기 지원
"""

import json
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from sklearn.metrics import mean_squared_error
from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")

# ── 경로 설정 ──────────────────────────────────────────────────────────────────
ARTIFACTS_ROOT = Path.home() / "SKN25-FINAL-4Team/import_pmax_production_release_20260608/artifacts/import_pmax_v29_60min/input_24h/predict_60min"
VAL_BASE = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/2.Validation/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
OUT_PATH = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_pmax_honda_inference_per_device.csv"

# Honda 모델은 4개 계량기용으로 학습됐지만 구조는 동일 → V.Z81 가중치 사용
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
WINDOW     = 96   # 24시간 × 4 = 96 시점
MIN_TEST_WINDOWS = 5


# ── 모델 로드 ──────────────────────────────────────────────────────────────────
def load_artifacts(meter: str):
    meter_dir = ARTIFACTS_ROOT / meter
    weights_df = pd.read_csv(meter_dir / "v29" / "ensemble_weights.csv")
    weights = dict(zip(weights_df["candidate_version"], weights_df["weight"]))
    models = {}
    for ver in ["v20", "v23", "v25", "v27"]:
        models[ver] = joblib.load(meter_dir / "_candidate_models" / f"{ver}.joblib")
    return models, weights


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
    d = df.sort_values("ts").reset_index(drop=True)
    p = d["P_max"]
    minute_of_day = d["ts"].dt.hour * 60 + d["ts"].dt.minute
    d["hour_sin"]       = np.sin(2 * np.pi * minute_of_day / (24 * 60))
    d["hour_cos"]       = np.cos(2 * np.pi * minute_of_day / (24 * 60))
    d["dayofweek_sin"]  = np.sin(2 * np.pi * d["ts"].dt.dayofweek / 7)
    d["P_max_lag_1"]    = p.shift(1)
    d["P_max_lag_96"]   = p.shift(96)
    d["P_max_lag_192"]  = p.shift(192)   # 48시간 전 — 없으면 NaN → 윈도우 스킵
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
            "actual_next": float(df_c[TARGET_COL].iloc[i]),   # 방향 일치율용
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
    """실제 변화 방향 vs 예측 변화 방향 일치율 (t+1 기준)"""
    actual_dir = np.sign(actual[:, 0] - persistence)
    pred_dir   = np.sign(pred[:, 0]   - persistence)
    mask = actual_dir != 0   # 변화가 있는 경우만
    if mask.sum() == 0:
        return np.nan
    return float((actual_dir[mask] == pred_dir[mask]).mean() * 100)


# ── 메인 ──────────────────────────────────────────────────────────────────────
print(f"Honda 모델 로드 중: {METER}")
try:
    models, weights = load_artifacts(METER)
    print(f"모델 로드 완료: {list(models.keys())}")
    print(f"가중치: {weights}")
except Exception as e:
    print(f"모델 로드 실패: {e}")
    raise

val_files = sorted(VAL_BASE.glob("Combined_LabelledData_*_역률평균.json"))
val_ids   = sorted({f.stem.split("_")[2] for f in val_files})
print(f"Validation 설비: {len(val_ids)}개")

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
if OUT_PATH.exists():
    done_ids = set(pd.read_csv(OUT_PATH)["device_id"].astype(str))
    print(f"이미 완료된 설비: {len(done_ids)}개 — 이어서 실행")
else:
    done_ids = set()

skipped   = 0
completed = 0

for device_id in tqdm(val_ids, desc="설비별 추론"):
    if device_id in done_ids:
        completed += 1
        continue

    val_path = VAL_BASE / f"Combined_LabelledData_{device_id}_역률평균.json"

    try:
        df_val_raw = load_device(val_path)
    except Exception as e:
        print(f"[SKIP] {device_id} 파일 로드 실패: {e}")
        skipped += 1
        continue

    if df_val_raw.empty:
        skipped += 1
        continue

    try:
        df_val = add_features(resample_15min(df_val_raw))
    except Exception as e:
        print(f"[SKIP] {device_id} 전처리 실패: {e}")
        skipped += 1
        continue

    X_test, y_test, meta_test = build_windows(df_val)

    if X_test is None or len(X_test) < MIN_TEST_WINDOWS:
        # lag_192 생성 불가 또는 데이터 부족
        skipped += 1
        continue

    meta_test = meta_test.reset_index(drop=True)

    try:
        preds = {}
        for ver, model in models.items():
            if isinstance(model, list):
                # MultiOutputRegressor가 list로 저장된 경우 — 각 horizon별 estimator
                horizon_preds = [est.predict(X_test) for est in model]
                preds[ver] = np.column_stack(horizon_preds)
            else:
                preds[ver] = model.predict(X_test)

        # 가중치 앙상블
        ensemble_pred = np.zeros_like(list(preds.values())[0])
        total_w = sum(weights.values())
        for ver, pred in preds.items():
            ensemble_pred += pred * (weights[ver] / total_w)

        persistence_pred = np.repeat(meta_test["persistence"].values[:, None], HORIZON, axis=1)
        actual_next      = meta_test["actual_next"].values

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