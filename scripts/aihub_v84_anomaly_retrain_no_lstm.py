"""
v84 이상탐지 AI Hub 검증 — 방법 2-A: LSTM 제외 재학습

Honda compute_thresholds.py 구조 그대로 재현:
  - Training actual P 시간대별 2~98 percentile → threshold
  - Validation 예측값이 threshold 범위 벗어나면 warning
  - 경보율(warning_flag rate) Honda 기준(17.79%)과 비교

모델 구조 (v84 동일):
  - v63: CatBoost
  - v67: Ridge  
  - v71: Seasonal Naive
  - median 앙상블 + shrunk hour bias correction × gain 1.30
  - 타겟: 잔차 P(t+h) - P(t-1)
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from catboost import CatBoostRegressor
from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")

AIHUB_TRAIN_BASE = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/1.Training/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
AIHUB_VAL_BASE   = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/2.Validation/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
OUT_PATH = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_v84_anomaly_retrain_no_lstm.csv"

ITEM_MAP = {"유효전력평균": "P", "상전압평균": "U1", "역률평균": "PF"}
HORIZON  = 3
WINDOW   = 24
MIN_WINDOWS = 10
LOWER_PCT = 2
UPPER_PCT = 98
V84_CORRECTION_GAIN      = 1.30
V84_SHRINKAGE_PRIOR_ROWS = 168.0
TIME_FEATURE_COLUMNS    = ["hour_sin","hour_cos","day_of_week_sin","day_of_week_cos","month_sin","month_cos"]
DERIVED_FEATURE_COLUMNS = ["diff_lag24","diff_lag168","is_workday","rolling_mean_24h"]
FEATURE_COLS = ["P","U1","PF"] + TIME_FEATURE_COLUMNS + DERIVED_FEATURE_COLUMNS
SCALER_COLS  = [c for c in FEATURE_COLS if c not in TIME_FEATURE_COLUMNS]


def load_device(fpath):
    with open(fpath) as f:
        data = json.load(f)
    rows = []
    for d in data["data"]:
        if d["ITEM_NAME"] not in ITEM_MAP or d.get("ITEM_VALUE") is None:
            continue
        try:
            rows.append({"ts": pd.Timestamp(d["TIMESTAMP"]),
                         "item": ITEM_MAP[d["ITEM_NAME"]],
                         "value": float(d["ITEM_VALUE"]),
                         "label": d.get("LABEL_NAME")})
        except (ValueError, TypeError):
            continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.pivot_table(index=["ts","label"], columns="item", values="value", aggfunc="first").reset_index()
    df.columns.name = None
    return df.sort_values("ts").reset_index(drop=True)


def resample_1h(df):
    g = df.set_index("ts")
    result = pd.DataFrame({"P": g["P"].resample("1h").mean().clip(lower=0),
                            "label": g["label"].resample("1h").last()})
    for c in ["U1","PF"]:
        if c in g.columns:
            result[c] = g[c].resample("1h").mean()
    return result.dropna(subset=["P"]).reset_index()


def apply_physical_rules(df):
    d = df.copy()
    if "PF" in d.columns:
        d.loc[pd.to_numeric(d["PF"], errors="coerce").abs() > 1.0, "PF"] = np.nan
    if "U1" in d.columns:
        u1 = pd.to_numeric(d["U1"], errors="coerce")
        d.loc[(u1 <= 0) | (u1 > 1000.0), "U1"] = np.nan
    return d


def add_features(df):
    d = df.sort_values("ts").reset_index(drop=True)
    ts = pd.to_datetime(d["ts"])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    p = pd.to_numeric(d["P"], errors="coerce")
    d["hour_sin"]         = np.sin(2*np.pi*ts.dt.hour/24)
    d["hour_cos"]         = np.cos(2*np.pi*ts.dt.hour/24)
    d["day_of_week_sin"]  = np.sin(2*np.pi*ts.dt.dayofweek/7)
    d["day_of_week_cos"]  = np.cos(2*np.pi*ts.dt.dayofweek/7)
    d["month_sin"]        = np.sin(2*np.pi*(ts.dt.month-1)/12)
    d["month_cos"]        = np.cos(2*np.pi*(ts.dt.month-1)/12)
    d["diff_lag24"]       = p.diff(24)
    d["diff_lag168"]      = p.diff(168).fillna(0.0)
    d["is_workday"]       = (ts.dt.dayofweek < 5).astype(np.float32)
    d["rolling_mean_24h"] = p.rolling(24, min_periods=1).mean()
    return d


def compute_hour_thresholds(df_train):
    """Honda compute_thresholds.py 구조 그대로: Training actual P 시간대별 2~98 percentile"""
    ts = pd.to_datetime(df_train["ts"])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    p = pd.to_numeric(df_train["P"], errors="coerce").values
    hours = ts.dt.hour.values
    thresholds = {}
    for hour in range(24):
        mask = hours == hour
        hour_p = p[mask & ~np.isnan(p)]
        if len(hour_p) == 0:
            hour_p = p[~np.isnan(p)]
        p_lower = float(np.percentile(hour_p, LOWER_PCT))
        p_upper = float(np.percentile(hour_p, UPPER_PCT))
        # floor 적용 (Honda 동일)
        if abs(p_lower) < 10 and p_lower > -50.0:
            p_lower = -50.0
        thresholds[hour] = {"p_lower": p_lower, "p_upper": p_upper}
    return thresholds


def get_available_features(df):
    return [c for c in FEATURE_COLS if c in df.columns]


def build_windows(df, feat_cols, scaler_cols, input_scaler, target_scaler):
    required = [c for c in feat_cols if c != "diff_lag168"]
    d = df.dropna(subset=required).reset_index(drop=True)
    if "diff_lag168" in d.columns:
        d["diff_lag168"] = d["diff_lag168"].fillna(0.0)
    if len(d) < WINDOW + HORIZON:
        return None, None, None
    sc = [c for c in scaler_cols if c in d.columns]
    scaled = input_scaler.transform(d[sc])
    scaled_df = pd.DataFrame(scaled, columns=sc, index=d.index)
    for c in feat_cols:
        if c not in sc:
            scaled_df[c] = d[c].values
    target_scaled = target_scaler.transform(d[["P"]]).ravel()
    X_list, y_list, meta_list = [], [], []
    for i in range(WINDOW, len(d) - HORIZON + 1):
        x = np.stack([scaled_df[c].iloc[i-WINDOW:i].values for c in feat_cols], axis=1).astype(np.float32)
        anchor = target_scaled[i-1]
        y = (target_scaled[i:i+HORIZON] - anchor).astype(np.float32)
        if np.isnan(x).any() or np.isnan(y).any():
            continue
        ts_val = d["ts"].iloc[i] if "ts" in d.columns else None
        X_list.append(x)
        y_list.append(y)
        meta_list.append({
            "ts": ts_val,
            "label": d["label"].iloc[i] if "label" in d.columns else None,
            "actual_p": float(d["P"].iloc[i]),
            "anchor_p": float(d["P"].iloc[i-1]),
            "target_end_hour": pd.to_datetime(ts_val).hour if ts_val else 0,
        })
    if not X_list:
        return None, None, None
    return np.array(X_list), np.array(y_list), meta_list


def restore_residual(pred_scaled, anchor_p, target_scaler):
    anchor_scaled = float(target_scaler.transform([[anchor_p]])[0][0])
    return target_scaler.inverse_transform((anchor_scaled + pred_scaled).reshape(-1,1)).ravel()


def ensemble_to_actual(v63, v67, v71, meta_list, target_scaler):
    N = v63.shape[0]
    out = np.zeros((N, HORIZON), dtype=np.float32)
    for i in range(N):
        p63 = restore_residual(v63[i], meta_list[i]["anchor_p"], target_scaler)
        p67 = restore_residual(v67[i], meta_list[i]["anchor_p"], target_scaler)
        p71 = restore_residual(v71[i], meta_list[i]["anchor_p"], target_scaler)
        out[i] = np.median(np.stack([p63, p67, p71[:HORIZON]], axis=0), axis=0)
    return out


def fit_bias_corrections(meta_list, preds, actuals):
    rows = []
    hours = np.array([m["target_end_hour"] for m in meta_list])
    for step in range(1, HORIZON+1):
        residuals = actuals[:, step-1] - preds[:, step-1]
        global_corr = float(np.median(residuals))
        for hour in range(24):
            mask = hours == hour
            n = int(mask.sum())
            raw = float(np.median(residuals[mask])) if n > 0 else global_corr
            alpha = n / (n + V84_SHRINKAGE_PRIOR_ROWS) if n > 0 else 0.0
            shrunk = global_corr + alpha * (raw - global_corr)
            rows.append({"forecast_step": step, "target_hour_utc": hour,
                         "median_residual_correction": V84_CORRECTION_GAIN * shrunk,
                         "fallback_global_correction": global_corr})
    return pd.DataFrame(rows)


def apply_bias(preds, meta_list, bias_df):
    corrected = preds.copy()
    hours = np.array([m["target_end_hour"] for m in meta_list])
    for step in range(1, HORIZON+1):
        sc = bias_df[bias_df["forecast_step"]==step].set_index("target_hour_utc")
        fallback = float(sc["fallback_global_correction"].iloc[0])
        for i, hour in enumerate(hours):
            corrected[i, step-1] += float(sc.loc[hour, "median_residual_correction"]) if hour in sc.index else fallback
    return corrected


def predict_naive(X, anchors, target_scaler):
    naive_scaled = X[:, :HORIZON, 0]
    anchor_scaled = target_scaler.transform(anchors.reshape(-1,1)).ravel()
    return (naive_scaled - anchor_scaled.reshape(-1,1)).astype(np.float32)


# ── 메인 ──────────────────────────────────────────────────────────────────────
train_files = sorted(AIHUB_TRAIN_BASE.glob("Combined_LabelledData_*_역률평균.json"))
val_files   = sorted(AIHUB_VAL_BASE.glob("Combined_LabelledData_*_역률평균.json"))
common_ids  = sorted({f.stem.split("_")[2] for f in train_files} & {f.stem.split("_")[2] for f in val_files})
print(f"공통 설비: {len(common_ids)}개")

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
if OUT_PATH.exists():
    done_ids = set(pd.read_csv(OUT_PATH)["device_id"].astype(str))
    print(f"이미 완료: {len(done_ids)}개 — 이어서 실행")
else:
    done_ids = set()

skipped = completed = 0

for device_id in tqdm(common_ids, desc="설비별 재학습"):
    if device_id in done_ids:
        completed += 1
        continue

    try:
        df_tr_raw = load_device(AIHUB_TRAIN_BASE / f"Combined_LabelledData_{device_id}_역률평균.json")
        df_va_raw = load_device(AIHUB_VAL_BASE   / f"Combined_LabelledData_{device_id}_역률평균.json")
    except Exception:
        skipped += 1; continue

    if df_tr_raw.empty or df_va_raw.empty:
        skipped += 1; continue

    try:
        df_tr = add_features(apply_physical_rules(resample_1h(df_tr_raw)))
        df_va = add_features(apply_physical_rules(resample_1h(df_va_raw)))
    except Exception:
        skipped += 1; continue

    feat_cols  = get_available_features(df_tr)
    scaler_cols = [c for c in SCALER_COLS if c in df_tr.columns]

    df_tr_clean = df_tr.dropna(subset=[c for c in feat_cols if c != "diff_lag168"]).reset_index(drop=True)
    if "diff_lag168" in df_tr_clean.columns:
        df_tr_clean["diff_lag168"] = df_tr_clean["diff_lag168"].fillna(0.0)

    if len(df_tr_clean) < WINDOW + HORIZON + MIN_WINDOWS:
        skipped += 1; continue

    input_scaler  = StandardScaler().fit(df_tr_clean[[c for c in scaler_cols if c in df_tr_clean.columns]])
    target_scaler = StandardScaler().fit(df_tr_clean[["P"]])

    X_tr, y_tr, meta_tr = build_windows(df_tr_clean, feat_cols, scaler_cols, input_scaler, target_scaler)
    X_va, y_va, meta_va = build_windows(df_va, feat_cols, scaler_cols, input_scaler, target_scaler)

    if X_tr is None or len(X_tr) < MIN_WINDOWS:
        skipped += 1; continue

    # Honda compute_thresholds.py 구조: val actual P 시간대별 2~98 percentile
    # AI Hub에서 val에 해당하는 건 Validation 데이터
    hour_thresholds = compute_hour_thresholds(df_va)

    try:
        x_tr_flat = X_tr.reshape(X_tr.shape[0], -1)
        cb = CatBoostRegressor(loss_function="MultiRMSE", iterations=300, learning_rate=0.05,
                               depth=6, random_seed=42, allow_writing_files=False, verbose=False)
        cb.fit(x_tr_flat, y_tr)
        v63_tr = np.asarray(cb.predict(x_tr_flat), dtype=np.float32)
        v63_va = np.asarray(cb.predict(X_va.reshape(X_va.shape[0],-1)), dtype=np.float32) if X_va is not None else None

        rg = Ridge(alpha=100.0)
        rg.fit(x_tr_flat, y_tr)
        v67_tr = np.asarray(rg.predict(x_tr_flat), dtype=np.float32)
        v67_va = np.asarray(rg.predict(X_va.reshape(X_va.shape[0],-1)), dtype=np.float32) if X_va is not None else None

        anchors_tr = np.array([m["anchor_p"] for m in meta_tr], dtype=np.float32)
        anchors_va = np.array([m["anchor_p"] for m in meta_va], dtype=np.float32) if X_va is not None else None
        v71_tr = predict_naive(X_tr, anchors_tr, target_scaler)
        v71_va = predict_naive(X_va, anchors_va, target_scaler) if X_va is not None else None
    except Exception:
        skipped += 1; continue

    ens_tr = ensemble_to_actual(v63_tr, v67_tr, v71_tr, meta_tr, target_scaler)
    actuals_tr = np.array([m["actual_p"] for m in meta_tr], dtype=np.float32)
    bias_df = fit_bias_corrections(meta_tr, ens_tr, np.tile(actuals_tr.reshape(-1,1),(1,HORIZON)))
    ens_tr_corrected = apply_bias(ens_tr, meta_tr, bias_df)

    row = {"device_id": device_id, "n_train_windows": len(X_tr)}

    if X_va is not None and len(X_va) >= 5:
        ens_va = ensemble_to_actual(v63_va, v67_va, v71_va, meta_va, target_scaler)
        ens_va_corrected = apply_bias(ens_va, meta_va, bias_df)

        # Honda 방식: 예측값이 시간대별 2~98 percentile 벗어나면 warning
        warning_list = []
        for i, meta in enumerate(meta_va):
            hour = meta["target_end_hour"]
            thr = hour_thresholds[hour]
            # t+1 기준 (Honda: t+3이 메인, t+1/t+2 보조 — OR 집계)
            any_warn = any(
                ens_va_corrected[i, step-1] > thr["p_upper"] or
                ens_va_corrected[i, step-1] < thr["p_lower"]
                for step in range(1, HORIZON+1)
            )
            warning_list.append(any_warn)

        warning_arr = np.array(warning_list)
        labels_va = [m["label"] for m in meta_va]

        results_df = pd.DataFrame({"label": labels_va, "warning": warning_arr})
        row["n_test_windows"]        = len(results_df)
        row["warning_flag_rate"]     = float(warning_arr.mean())

        for label in ["정상", "주의", "경고"]:
            mask = results_df["label"] == label
            n_label = int(mask.sum())
            row[f"n_{label}"]              = n_label
            row[f"warning_rate_{label}"]   = float(results_df.loc[mask, "warning"].mean()) if n_label > 0 else np.nan
    else:
        row["n_test_windows"]    = 0
        row["warning_flag_rate"] = np.nan
        for label in ["정상","주의","경고"]:
            row[f"n_{label}"] = 0
            row[f"warning_rate_{label}"] = np.nan

    row_df = pd.DataFrame([row])
    if not OUT_PATH.exists():
        row_df.to_csv(OUT_PATH, index=False)
    else:
        row_df.to_csv(OUT_PATH, mode="a", header=False, index=False)
    completed += 1

print(f"\n스킵: {skipped}, 완료: {completed}")
if OUT_PATH.exists():
    df_res = pd.read_csv(OUT_PATH)
    print(f"\n=== 경보율 (Honda 기준: 17.79%) ===")
    print(f"  전체 평균 경보율: {df_res['warning_flag_rate'].mean()*100:.1f}%")
    for label in ["정상","주의","경고"]:
        col = f"warning_rate_{label}"
        if col in df_res.columns:
            rate = df_res[col].dropna().mean()
            n = int(df_res[f"n_{label}"].sum())
            print(f"  {label} ({n}건): {rate*100:.1f}%")
print("\n완료.")