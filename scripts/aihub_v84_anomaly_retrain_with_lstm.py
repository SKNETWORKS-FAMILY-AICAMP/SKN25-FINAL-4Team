"""
v84 이상탐지 AI Hub 검증 — 방법 2-B: LSTM 포함 재학습

구조:
  - v63: LSTM top2 앙상블 (v1/v2/v4/v6/v7, v3 제외 — 24h 윈도우만)
  - v67: Ridge
  - v71: Seasonal Naive
  - median 앙상블 + shrunk hour bias correction × gain 1.30
  - threshold: val anomaly_score_mae 99.5 percentile
  - 타겟: 잔차 P(t) - P(t-1) (USE_RESIDUAL_TARGET=True)

데이터:
  - Training: 설비별 학습 + val 분할 (내부 4/6 split)
  - Validation: 테스트
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")

# ── 경로 ──────────────────────────────────────────────────────────────────────
AIHUB_TRAIN_BASE = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/1.Training/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
AIHUB_VAL_BASE   = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/2.Validation/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
OUT_PATH = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_v84_anomaly_retrain_with_lstm.csv"

ITEM_MAP = {"유효전력평균": "P", "상전압평균": "U1", "역률평균": "PF"}
HORIZON  = 3
WINDOW   = 24
MIN_WINDOWS = 10

V84_CORRECTION_GAIN      = 1.30
V84_SHRINKAGE_PRIOR_ROWS = 168.0
VAL_THRESHOLD_QUANTILE   = 0.90
HIDDEN_SIZE = 32
EPOCHS      = 12
BATCH_SIZE  = 64
LR          = 1e-3
PATIENCE    = 4

TIME_FEATURE_COLUMNS    = ["hour_sin","hour_cos","day_of_week_sin","day_of_week_cos","month_sin","month_cos"]
DERIVED_FEATURE_COLUMNS = ["diff_lag24","diff_lag168","is_workday","rolling_mean_24h"]
FEATURE_COLS = ["P","U1","PF"] + TIME_FEATURE_COLUMNS + DERIVED_FEATURE_COLUMNS

# v3 제외 LSTM 변형 (24h 윈도우만)
LSTM_VARIANTS = [
    {"version": "v1", "use_time": False, "arch": "lstm", "dropout": 0.0, "loss": "mse"},
    {"version": "v2", "use_time": True,  "arch": "lstm", "dropout": 0.0, "loss": "mse"},
    {"version": "v4", "use_time": True,  "arch": "gru",  "dropout": 0.0, "loss": "mse"},
    {"version": "v6", "use_time": True,  "arch": "lstm", "dropout": 0.2, "loss": "mse"},
    {"version": "v7", "use_time": True,  "arch": "lstm", "dropout": 0.0, "loss": "smooth_l1"},
]

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


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
    df = df.pivot_table(index=["ts","label"], columns="item", values="value", aggfunc="first").reset_index()
    df.columns.name = None
    return df.sort_values("ts").reset_index(drop=True)


def resample_1h(df: pd.DataFrame) -> pd.DataFrame:
    g = df.set_index("ts")
    result = pd.DataFrame({
        "P":     g["P"].resample("1h").mean().clip(lower=0),
        "label": g["label"].resample("1h").last(),
    })
    if "U1" in g.columns:
        result["U1"] = g["U1"].resample("1h").mean()
    if "PF" in g.columns:
        result["PF"] = g["PF"].resample("1h").mean()
    return result.dropna(subset=["P"]).reset_index()


def apply_physical_rules(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    if "PF" in d.columns:
        d.loc[pd.to_numeric(d["PF"], errors="coerce").abs() > 1.0, "PF"] = np.nan
    if "U1" in d.columns:
        u1 = pd.to_numeric(d["U1"], errors="coerce")
        d.loc[(u1 <= 0) | (u1 > 1000.0), "U1"] = np.nan
    return d


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.sort_values("ts").reset_index(drop=True)
    ts = pd.to_datetime(d["ts"])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    p = pd.to_numeric(d["P"], errors="coerce")
    d["hour_sin"]         = np.sin(2 * np.pi * ts.dt.hour / 24)
    d["hour_cos"]         = np.cos(2 * np.pi * ts.dt.hour / 24)
    d["day_of_week_sin"]  = np.sin(2 * np.pi * ts.dt.dayofweek / 7)
    d["day_of_week_cos"]  = np.cos(2 * np.pi * ts.dt.dayofweek / 7)
    d["month_sin"]        = np.sin(2 * np.pi * (ts.dt.month - 1) / 12)
    d["month_cos"]        = np.cos(2 * np.pi * (ts.dt.month - 1) / 12)
    d["diff_lag24"]       = p.diff(24)
    d["diff_lag168"]      = p.diff(168)
    d["is_workday"]       = (ts.dt.dayofweek < 5).astype(np.float32)
    d["rolling_mean_24h"] = p.rolling(window=24, min_periods=1).mean()
    return d


def get_available_features(df: pd.DataFrame, use_time: bool = True) -> list:
    base = ["P","U1","PF"] if all(c in df.columns for c in ["U1","PF"]) else \
           [c for c in ["P","U1","PF"] if c in df.columns]
    if use_time:
        return base + TIME_FEATURE_COLUMNS + DERIVED_FEATURE_COLUMNS
    return base + DERIVED_FEATURE_COLUMNS


# ── 윈도우 생성 ────────────────────────────────────────────────────────────────
def build_windows(df: pd.DataFrame, feat_cols: list, scaler_cols: list,
                  input_scaler: StandardScaler, target_scaler: StandardScaler):
    d = df.dropna(subset=feat_cols).reset_index(drop=True)
    if len(d) < WINDOW + HORIZON:
        return None, None, None

    scaled = input_scaler.transform(d[scaler_cols])
    scaled_df = pd.DataFrame(scaled, columns=scaler_cols, index=d.index)
    for c in feat_cols:
        if c not in scaler_cols:
            scaled_df[c] = d[c].values
    target_scaled = target_scaler.transform(d[["P"]]).ravel()

    X_list, y_list, meta_list = [], [], []
    for i in range(WINDOW, len(d) - HORIZON + 1):
        x = np.stack([scaled_df[c].iloc[i-WINDOW:i].values for c in feat_cols], axis=1).astype(np.float32)
        anchor = target_scaled[i - 1]
        y = (target_scaled[i:i+HORIZON] - anchor).astype(np.float32)
        if np.isnan(x).any() or np.isnan(y).any():
            continue
        ts_val = d["ts"].iloc[i] if "ts" in d.columns else None
        label  = d["label"].iloc[i] if "label" in d.columns else None
        X_list.append(x)
        y_list.append(y)
        meta_list.append({
            "ts": ts_val,
            "label": label,
            "actual_p": float(d["P"].iloc[i]),
            "anchor_p": float(d["P"].iloc[i-1]),
            "target_end_hour": pd.to_datetime(ts_val).hour if ts_val else 0,
        })
    if not X_list:
        return None, None, None
    return np.array(X_list), np.array(y_list), meta_list


# ── LSTM 모델 ──────────────────────────────────────────────────────────────────
class RecurrentPredictor(nn.Module):
    def __init__(self, input_size, hidden_size=HIDDEN_SIZE, output_size=HORIZON,
                 arch="lstm", dropout=0.0):
        super().__init__()
        arch_map = {"lstm": nn.LSTM, "gru": nn.GRU}
        self.recurrent = arch_map[arch](input_size=input_size, hidden_size=hidden_size, batch_first=True)
        self.head = nn.Sequential(
            nn.Linear(hidden_size, hidden_size), nn.ReLU(),
            nn.Dropout(p=dropout), nn.Linear(hidden_size, output_size)
        )
        self.is_lstm = (arch == "lstm")
    def forward(self, x):
        if self.is_lstm:
            _, (h, _) = self.recurrent(x)
        else:
            _, h = self.recurrent(x)
        return self.head(h[-1])


def train_lstm(X_tr, y_tr, X_va, y_va, variant: dict) -> tuple:
    input_size = X_tr.shape[2]
    model = RecurrentPredictor(input_size=input_size, arch=variant["arch"],
                                dropout=variant["dropout"]).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    loss_fn = nn.SmoothL1Loss() if variant["loss"] == "smooth_l1" else nn.MSELoss()

    tr_loader = DataLoader(TensorDataset(torch.from_numpy(X_tr), torch.from_numpy(y_tr)),
                           batch_size=BATCH_SIZE, shuffle=True)
    best_val, best_state, patience_left = float("inf"), None, PATIENCE

    for _ in range(EPOCHS):
        model.train()
        for xb, yb in tr_loader:
            xb, yb = xb.to(device), yb.to(device)
            opt.zero_grad()
            loss_fn(model(xb), yb).backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            xv = torch.from_numpy(X_va).to(device)
            vp = model(xv).cpu().numpy()
        vl = float(np.mean(np.abs(vp - y_va)))
        if vl < best_val:
            best_val = vl
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            patience_left = PATIENCE
        else:
            patience_left -= 1
            if patience_left <= 0:
                break

    if best_state:
        model.load_state_dict(best_state)
    return model, best_val


def predict_lstm(model, X: np.ndarray) -> np.ndarray:
    model.eval()
    preds = []
    with torch.no_grad():
        for i in range(0, len(X), BATCH_SIZE):
            xb = torch.from_numpy(X[i:i+BATCH_SIZE]).to(device)
            preds.append(model(xb).cpu().numpy())
    return np.vstack(preds)


# ── 잔차 복원 ──────────────────────────────────────────────────────────────────
def restore_residual(pred_residual_scaled: np.ndarray, anchor_p: float,
                     target_scaler: StandardScaler) -> np.ndarray:
    anchor_scaled = float(target_scaler.transform([[anchor_p]])[0][0])
    pred_scaled = anchor_scaled + pred_residual_scaled
    return target_scaler.inverse_transform(pred_scaled.reshape(-1, 1)).ravel()


def ensemble_to_actual(v63: np.ndarray, v67: np.ndarray, v71: np.ndarray,
                       meta_list: list, target_scaler: StandardScaler) -> np.ndarray:
    N = v63.shape[0]
    preds_actual = np.zeros((N, HORIZON), dtype=np.float32)
    for i in range(N):
        anchor_p = meta_list[i]["anchor_p"]
        p63 = restore_residual(v63[i], anchor_p, target_scaler)
        p67 = restore_residual(v67[i], anchor_p, target_scaler)
        p71 = restore_residual(v71[i], anchor_p, target_scaler)
        preds_actual[i] = np.median(np.stack([p63, p67, p71[:HORIZON]], axis=0), axis=0)
    return preds_actual


# ── Bias correction ────────────────────────────────────────────────────────────
def fit_bias_corrections(meta_list: list, preds: np.ndarray, actuals: np.ndarray) -> pd.DataFrame:
    rows = []
    hours = np.array([m["target_end_hour"] for m in meta_list])
    for step in range(1, HORIZON + 1):
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


def apply_bias(preds: np.ndarray, meta_list: list, bias_df: pd.DataFrame) -> np.ndarray:
    corrected = preds.copy()
    hours = np.array([m["target_end_hour"] for m in meta_list])
    for step in range(1, HORIZON + 1):
        step_corr = bias_df[bias_df["forecast_step"] == step].set_index("target_hour_utc")
        fallback = float(step_corr["fallback_global_correction"].iloc[0])
        for i, hour in enumerate(hours):
            if hour in step_corr.index:
                corrected[i, step-1] += float(step_corr.loc[hour, "median_residual_correction"])
            else:
                corrected[i, step-1] += fallback
    return corrected


def predict_naive(X: np.ndarray, anchors: np.ndarray,
                  target_scaler: StandardScaler) -> np.ndarray:
    naive_scaled = X[:, :HORIZON, 0]
    anchor_scaled = target_scaler.transform(anchors.reshape(-1, 1)).ravel()
    return (naive_scaled - anchor_scaled.reshape(-1, 1)).astype(np.float32)


def predict_ridge_model(model: Ridge, X: np.ndarray) -> np.ndarray:
    return np.asarray(model.predict(X.reshape(X.shape[0], -1)), dtype=np.float32)


# ── 메인 ──────────────────────────────────────────────────────────────────────
train_files = sorted(AIHUB_TRAIN_BASE.glob("Combined_LabelledData_*_역률평균.json"))
val_files   = sorted(AIHUB_VAL_BASE.glob("Combined_LabelledData_*_역률평균.json"))
train_ids = {f.stem.split("_")[2] for f in train_files}
val_ids   = {f.stem.split("_")[2] for f in val_files}
common_ids = sorted(train_ids & val_ids)
print(f"Training: {len(train_ids)}, Validation: {len(val_ids)}, 공통: {len(common_ids)}")

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
if OUT_PATH.exists():
    done_ids = set(pd.read_csv(OUT_PATH)["device_id"].astype(str))
    print(f"이미 완료: {len(done_ids)}개 — 이어서 실행")
else:
    done_ids = set()

skipped = 0
completed = 0

for device_id in tqdm(common_ids, desc="설비별 재학습(LSTM 포함)"):
    if device_id in done_ids:
        completed += 1
        continue

    train_path = AIHUB_TRAIN_BASE / f"Combined_LabelledData_{device_id}_역률평균.json"
    val_path   = AIHUB_VAL_BASE   / f"Combined_LabelledData_{device_id}_역률평균.json"

    try:
        df_tr_raw = load_device(train_path)
        df_va_raw = load_device(val_path)
    except Exception:
        skipped += 1
        continue

    if df_tr_raw.empty or df_va_raw.empty:
        skipped += 1
        continue

    try:
        df_tr_raw_resampled = apply_physical_rules(resample_1h(df_tr_raw))
        df_va_raw_resampled = apply_physical_rules(resample_1h(df_va_raw))
        df_combined = pd.concat([df_tr_raw_resampled, df_va_raw_resampled], ignore_index=True).sort_values("ts").reset_index(drop=True)
        df_combined = add_features(df_combined)
        val_start_ts = df_va_raw_resampled["ts"].min()
        df_tr = df_combined[df_combined["ts"] < val_start_ts].reset_index(drop=True)
        df_va = df_combined[df_combined["ts"] >= val_start_ts].reset_index(drop=True)
    except Exception:
        skipped += 1
        continue

    # 내부 분할
    feat_cols_time    = get_available_features(df_tr, use_time=True)
    feat_cols_notime  = get_available_features(df_tr, use_time=False)
    scaler_cols = [c for c in feat_cols_time if c not in TIME_FEATURE_COLUMNS]

    df_tr_clean = df_tr.dropna(subset=feat_cols_time).reset_index(drop=True)
    n = len(df_tr_clean)
    split = int(n * 4 / 6)

    if split < WINDOW + HORIZON + 10 or (n - split) < WINDOW + HORIZON:
        skipped += 1
        continue

    df_inner_train = df_tr_clean.iloc[:split]
    df_inner_val   = df_tr_clean.iloc[split:]

    input_scaler  = StandardScaler().fit(df_inner_train[scaler_cols])
    target_scaler = StandardScaler().fit(df_inner_train[["P"]])

    # LSTM용 (시간 피처 여부 variant별 다름)
    X_tr_t,  y_tr_t,  _       = build_windows(df_inner_train, feat_cols_time,   scaler_cols, input_scaler, target_scaler)
    X_iv_t,  y_iv_t,  meta_iv = build_windows(df_inner_val,   feat_cols_time,   scaler_cols, input_scaler, target_scaler)
    X_tr_nt, y_tr_nt, _       = build_windows(df_inner_train, feat_cols_notime, scaler_cols, input_scaler, target_scaler)
    X_iv_nt, y_iv_nt, _       = build_windows(df_inner_val,   feat_cols_notime, scaler_cols, input_scaler, target_scaler)
    X_va_t,  y_va_t,  meta_va = build_windows(df_va,          feat_cols_time,   scaler_cols, input_scaler, target_scaler)

    if X_tr_t is None or X_iv_t is None or len(X_tr_t) < MIN_WINDOWS:
        skipped += 1
        continue

    anchors_iv = np.array([m["anchor_p"] for m in meta_iv], dtype=np.float32)
    anchors_va = np.array([m["anchor_p"] for m in meta_va], dtype=np.float32) if X_va_t is not None else None

    try:
        # ── LSTM 학습 ──────────────────────────────────────────────────────
        lstm_results = {}
        for variant in LSTM_VARIANTS:
            X_tr_v = X_tr_t  if variant["use_time"] else X_tr_nt
            X_iv_v = X_iv_t  if variant["use_time"] else X_iv_nt
            y_tr_v = y_tr_t  if variant["use_time"] else y_tr_nt
            y_iv_v = y_iv_t  if variant["use_time"] else y_iv_nt
            if X_tr_v is None or X_iv_v is None:
                continue
            model_lstm, val_mae = train_lstm(X_tr_v, y_tr_v, X_iv_v, y_iv_v, variant)
            vp = predict_lstm(model_lstm, X_iv_v)
            lstm_results[variant["version"]] = {"model": model_lstm, "val_mae": val_mae,
                                                 "val_pred": vp, "use_time": variant["use_time"]}
            del model_lstm

        if not lstm_results:
            skipped += 1
            continue

        # top2 선택
        sorted_vers = sorted(lstm_results.keys(), key=lambda v: lstm_results[v]["val_mae"])[:2]
        maes = [lstm_results[v]["val_mae"] for v in sorted_vers]
        inv = np.array([1.0 / max(m, 1e-9) for m in maes])
        weights = inv / inv.sum()

        # inner val top2 앙상블
        v63_iv = sum(weights[i] * lstm_results[v]["val_pred"] for i, v in enumerate(sorted_vers))

        # Validation 추론
        v63_va = None
        if X_va_t is not None:
            preds_va = []
            for v, w in zip(sorted_vers, weights):
                X_va_v = X_va_t if lstm_results[v]["use_time"] else \
                         build_windows(df_va, feat_cols_notime, scaler_cols, input_scaler, target_scaler)[0]
                if X_va_v is not None:
                    preds_va.append(w * predict_lstm(lstm_results[v]["model"], X_va_v))
            if preds_va:
                v63_va = sum(preds_va)

        # ── v67: Ridge ─────────────────────────────────────────────────────
        rg = Ridge(alpha=100.0)
        rg.fit(X_tr_t.reshape(X_tr_t.shape[0], -1), y_tr_t)
        v67_iv = predict_ridge_model(rg, X_iv_t)
        v67_va = predict_ridge_model(rg, X_va_t) if X_va_t is not None else None

        # ── v71: Seasonal Naive ─────────────────────────────────────────────
        v71_iv = predict_naive(X_iv_t, anchors_iv, target_scaler)
        v71_va = predict_naive(X_va_t, anchors_va, target_scaler) if X_va_t is not None else None

    except Exception as e:
        skipped += 1
        continue

    # ── inner val 앙상블 → bias correction ────────────────────────────────────
    ens_iv = ensemble_to_actual(v63_iv, v67_iv, v71_iv, meta_iv, target_scaler)
    actuals_iv = np.array([m["actual_p"] for m in meta_iv], dtype=np.float32)
    actuals_iv_mat = np.tile(actuals_iv.reshape(-1, 1), (1, HORIZON))
    bias_df = fit_bias_corrections(meta_iv, ens_iv, actuals_iv_mat)

    # ── Training actual P 기준 시간대별 2~98 percentile threshold (Honda 동일) ─
    df_tr_thresh = df_tr.copy()
    df_tr_thresh["hour"] = pd.to_datetime(df_tr_thresh["ts"]).dt.hour
    hour_thresholds = {}
    for hour in range(24):
        subset = df_tr_thresh[df_tr_thresh["hour"] == hour]["P"].dropna()
        if len(subset) < 10:
            continue
        p_lower = float(np.percentile(subset, 2))
        p_upper = float(np.percentile(subset, 98))
        if abs(p_lower) < 10:
            p_lower = -50.0
        hour_thresholds[hour] = {"p_lower": p_lower, "p_upper": p_upper}

    row = {
        "device_id": device_id,
        "n_train_windows": len(X_tr_t),
        "n_val_windows": len(X_iv_t),
        "lstm_top2": ",".join(sorted_vers),
    }

    if X_va_t is not None and v63_va is not None and len(X_va_t) >= 5:
        actuals_va = np.array([m["actual_p"] for m in meta_va], dtype=np.float32)
        hours_va = np.array([pd.to_datetime(m["ts"]).hour for m in meta_va])

        # actual P vs threshold (Honda 동일 방식)
        is_outlier = np.array([
            (actuals_va[i] < hour_thresholds.get(hours_va[i], {}).get("p_lower", -np.inf)) or
            (actuals_va[i] > hour_thresholds.get(hours_va[i], {}).get("p_upper",  np.inf))
            for i in range(len(actuals_va))
        ])

        ens_va = ensemble_to_actual(v63_va, v67_va, v71_va, meta_va, target_scaler)
        ens_va_corrected = apply_bias(ens_va, meta_va, bias_df)
        pred_va_t1 = np.array([
            ens_va_corrected[i, 0] for i in range(len(meta_va))
        ], dtype=np.float32)

        # 예측값 vs threshold → warning_flag
        warning_flag = np.array([
            (pred_va_t1[i] < hour_thresholds.get(hours_va[i], {}).get("p_lower", -np.inf)) or
            (pred_va_t1[i] > hour_thresholds.get(hours_va[i], {}).get("p_upper",  np.inf))
            for i in range(len(pred_va_t1))
        ])

        po_ao = int(( is_outlier &  warning_flag).sum())
        po_ai = int((~is_outlier &  warning_flag).sum())
        pi_ao = int(( is_outlier & ~warning_flag).sum())
        pi_ai = int((~is_outlier & ~warning_flag).sum())
        n_total = len(is_outlier)

        row["n_test_windows"]  = n_total
        row["n_outlier"]       = int(is_outlier.sum())
        row["n_warning"]       = int(warning_flag.sum())
        row["warning_rate"]    = round(float(warning_flag.mean()) * 100, 4)
        row["po_ao"]           = po_ao
        row["po_ai"]           = po_ai
        row["pi_ao"]           = pi_ao
        row["pi_ai"]           = pi_ai
        row["po_ao_rate"]      = round(po_ao / n_total * 100, 4)
        row["po_ai_rate"]      = round(po_ai / n_total * 100, 4)
        row["pi_ao_rate"]      = round(pi_ao / n_total * 100, 4)
    else:
        row["n_test_windows"] = 0
        for k in ["n_outlier","n_warning","warning_rate","po_ao","po_ai","pi_ao","pi_ai","po_ao_rate","po_ai_rate","pi_ao_rate"]:
            row[k] = np.nan

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
    print(f"\n=== 라벨별 이상탐지 발동률 (전체 설비 평균) ===")
    for label in ["정상", "주의", "경고"]:
        col = f"detection_rate_{label}"
        if col in df_res.columns:
            rate = df_res[col].dropna().mean()
            n = int(df_res[f"n_{label}"].sum())
            print(f"  {label} ({n}건): 평균 탐지율 {rate*100:.1f}%")
    print(f"\n결과 저장: {OUT_PATH}")
print("\n완료.")