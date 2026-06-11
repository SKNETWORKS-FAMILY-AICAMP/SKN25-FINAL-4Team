"""
v84 이상탐지 AI Hub 검증 — Honda 가중치 그대로 추론

흐름:
  1. AI Hub Training 데이터 JSON 로드 → 15분 리샘플링 → 1시간 집계
  2. v84 artifact (Honda 학습) 로드 — inference.py 추론 구조 재현
  3. anomaly_score_mae 산출 → train_meta.json의 anomaly_threshold 적용
  4. is_anomaly vs AI Hub 라벨(정상/주의/경고) 비교
  5. 설비별 결과 저장 + 전체 집계

평가 지표:
  - 라벨별 이상탐지 발동률 (주의/경고 구간에서 is_anomaly=True 비율)
  - 정상 구간 오탐률 (is_anomaly=True 비율)

주의:
  - v84는 열계량기(K, W) 계량기 기반 학습. AI Hub는 전기 펌프/모터.
  - 피처 매핑: 유효전력평균→P, 상전압평균→U1, 역률평균→PF
  - AI Hub 1분 데이터 → 1시간 집계 (mean)
  - Honda artifact 중 전기 대표 계량기(H2.Z66) 가중치 사용
"""

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")

# ── 경로 설정 ──────────────────────────────────────────────────────────────────
ARTIFACTS_ROOT = Path.home() / "SKN25-FINAL-4Team/share_test6_residual_v84_20260609/test6_residual/pipeline/artifacts/3h"
AIHUB_TRAIN_BASE = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/1.Training/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
OUT_PATH = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_v84_anomaly_honda_per_device.csv"

# Honda 전기 대표 계량기 artifact 사용 (P, U1, PF 피처 보유)
PROXY_METER = "H2.Z66"

ITEM_MAP = {
    "유효전력평균": "P",
    "상전압평균":   "U1",
    "역률평균":     "PF",
}

HORIZON = 3
MIN_WINDOWS = 20
MAX_FFILL_HOURS = 3


# ── AI Hub JSON 로드 ───────────────────────────────────────────────────────────
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
    """1분 데이터 → 1시간 집계 (mean). label은 last."""
    g = df.set_index("ts")
    return pd.DataFrame({
        "P":     g["P"].resample("1h").mean().clip(lower=0),
        "U1":    g["U1"].resample("1h").mean() if "U1" in g.columns else np.nan,
        "PF":    g["PF"].resample("1h").mean() if "PF" in g.columns else np.nan,
        "label": g["label"].resample("1h").last(),
    }).dropna(subset=["P"]).reset_index()


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """v84 파이프라인 피처 생성 (config.py 기준)."""
    d = df.sort_values("ts").reset_index(drop=True)
    ts = pd.to_datetime(d["ts"], utc=True) if d["ts"].dt.tz is not None else pd.to_datetime(d["ts"]).dt.tz_localize("UTC")
    p = pd.to_numeric(d["P"], errors="coerce")

    d["hour_sin"]        = np.sin(2 * np.pi * ts.dt.hour / 24)
    d["hour_cos"]        = np.cos(2 * np.pi * ts.dt.hour / 24)
    d["day_of_week_sin"] = np.sin(2 * np.pi * ts.dt.dayofweek / 7)
    d["day_of_week_cos"] = np.cos(2 * np.pi * ts.dt.dayofweek / 7)
    d["month_sin"]       = np.sin(2 * np.pi * (ts.dt.month - 1) / 12)
    d["month_cos"]       = np.cos(2 * np.pi * (ts.dt.month - 1) / 12)
    d["diff_lag24"]      = p.diff(24)
    d["diff_lag168"]     = p.diff(168)
    d["is_workday"]      = (ts.dt.dayofweek < 5).astype(np.float32)
    d["rolling_mean_24h"] = p.rolling(window=24, min_periods=1).mean()
    return d


def apply_physical_rules(df: pd.DataFrame) -> pd.DataFrame:
    """물리 룰 위반 → NaN 처리."""
    d = df.copy()
    if "PF" in d.columns:
        d.loc[pd.to_numeric(d["PF"], errors="coerce").abs() > 1.0, "PF"] = np.nan
    if "U1" in d.columns:
        u1 = pd.to_numeric(d["U1"], errors="coerce")
        d.loc[(u1 <= 0) | (u1 > 1000.0), "U1"] = np.nan
    return d


# ── Artifact 로드 ──────────────────────────────────────────────────────────────
def load_proxy_artifacts(meter_urn: str, horizon: int):
    d = ARTIFACTS_ROOT / meter_urn
    input_scaler  = joblib.load(d / "input_scaler.joblib")
    target_scaler = joblib.load(d / "target_scaler.joblib")
    with open(d / "feature_columns.json") as f:
        feature_columns = json.load(f)
    with open(d / "routing.json") as f:
        routing = json.load(f)
    with open(d / "train_meta.json") as f:
        train_meta = json.load(f)
    bias_df = pd.read_csv(d / "hour_bias_corrections.csv")
    anomaly_threshold = float(train_meta["anomaly_threshold"])
    return input_scaler, target_scaler, feature_columns, routing, bias_df, anomaly_threshold


# ── 잔차 복원 ──────────────────────────────────────────────────────────────────
def restore_residual(pred_residual_scaled: np.ndarray, anchor_p: float, target_scaler) -> np.ndarray:
    """scaled 잔차 → 원본 P값 복원."""
    anchor_scaled = float(target_scaler.transform([[anchor_p]])[0][0])
    pred_scaled = anchor_scaled + pred_residual_scaled
    return target_scaler.inverse_transform(pred_scaled.reshape(-1, 1)).ravel()


# ── LSTM 로드 및 추론 ──────────────────────────────────────────────────────────
def load_lstm(meter_urn: str, version: str, input_size: int, horizon: int):
    from torch import nn
    class RecurrentPredictor(nn.Module):
        def __init__(self, input_size, hidden_size=32, output_size=3, arch="lstm", dropout=0.0):
            super().__init__()
            arch_map = {"lstm": nn.LSTM, "gru": nn.GRU}
            self.recurrent = arch_map[arch](input_size=input_size, hidden_size=hidden_size, batch_first=True)
            self.head = nn.Sequential(
                nn.Linear(hidden_size, hidden_size), nn.ReLU(),
                nn.Dropout(p=dropout), nn.Linear(hidden_size, output_size)
            )
        def forward(self, x):
            if isinstance(self.recurrent, torch.nn.LSTM):
                _, (h, _) = self.recurrent(x)
            else:
                _, h = self.recurrent(x)
            return self.head(h[-1])

    path = ARTIFACTS_ROOT / meter_urn / f"lstm_{version}.pt"
    arch = "gru" if version == "v4" else "lstm"
    dropout = 0.2 if version == "v6" else 0.0
    model = RecurrentPredictor(input_size=input_size, output_size=horizon, arch=arch, dropout=dropout)
    model.load_state_dict(torch.load(path, map_location="cpu"))
    model.eval()
    return model


def predict_lstm(model, x_window: np.ndarray) -> np.ndarray:
    """(window, feat) → (horizon,) scaled 잔차 예측."""
    t = torch.from_numpy(x_window[np.newaxis]).float()
    with torch.no_grad():
        return model(t).numpy().ravel()


# ── 윈도우 생성 ────────────────────────────────────────────────────────────────
def build_windows(df: pd.DataFrame, feature_columns: list, input_scaler,
                  target_scaler, window_size: int = 24):
    """슬라이딩 윈도우 생성. (X_list, anchor_list, meta_list) 반환."""
    from sklearn.preprocessing import StandardScaler
    scaler_cols = [c for c in feature_columns if c not in
                   ("hour_sin","hour_cos","day_of_week_sin","day_of_week_cos","month_sin","month_cos")]

    df_c = df.dropna(subset=feature_columns).reset_index(drop=True)
    if len(df_c) < window_size + HORIZON:
        return None, None, None

    # scaler 적용
    scaled_feats = input_scaler.transform(df_c[scaler_cols])
    scaled_df = pd.DataFrame(scaled_feats, columns=scaler_cols, index=df_c.index)
    for c in feature_columns:
        if c not in scaler_cols:
            scaled_df[c] = df_c[c].values

    target_scaled = target_scaler.transform(df_c[["P"]]).ravel()

    X_list, anchor_list, meta_list = [], [], []
    for i in range(window_size, len(df_c) - HORIZON + 1):
        x = np.stack([scaled_df[c].iloc[i-window_size:i].values for c in feature_columns], axis=1).astype(np.float32)
        if np.isnan(x).any():
            continue
        anchor = float(target_scaled[i - 1])
        ts_val = df_c["ts"].iloc[i] if "ts" in df_c.columns else None
        label  = df_c["label"].iloc[i] if "label" in df_c.columns else None
        actual_p = float(df_c["P"].iloc[i])
        X_list.append(x)
        anchor_list.append(anchor)
        meta_list.append({"ts": ts_val, "label": label, "actual_p": actual_p,
                          "anchor_p": float(df_c["P"].iloc[i-1])})
    if not X_list:
        return None, None, None
    return X_list, anchor_list, meta_list


# ── 메인 ──────────────────────────────────────────────────────────────────────
print("Honda artifact 로드 중...")
try:
    input_scaler, target_scaler, feature_columns, routing, bias_df, anomaly_threshold = \
        load_proxy_artifacts(PROXY_METER, HORIZON)
    print(f"  feature_columns({len(feature_columns)}): {feature_columns[:5]}...")
    print(f"  anomaly_threshold: {anomaly_threshold:.2f}")
    print(f"  routing v57={routing.get('v57')}, v63={routing.get('v63')}")
except Exception as e:
    print(f"Artifact 로드 실패: {e}")
    raise

# LSTM 버전 결정 (routing 기준)
v52_source = routing.get("v52_source", "v10")
lstm_top2  = routing.get("lstm_top2_versions", ["v1", "v2"])
lstm_top2_w = routing.get("lstm_top2_weights", [0.5, 0.5])
input_size = len(feature_columns)

# v3(168h window) 여부 확인
use_v3 = (v52_source == "v3")
window_size = 168 if use_v3 else 24
print(f"  window_size={window_size}, lstm_top2={lstm_top2}")

# LSTM 모델 로드
lstm_models = {}
for ver in set(lstm_top2):
    try:
        lstm_models[ver] = load_lstm(PROXY_METER, ver, input_size, HORIZON)
        print(f"  LSTM {ver} 로드 완료")
    except Exception as e:
        print(f"  LSTM {ver} 로드 실패: {e}")

# LightGBM (v63=v61인 경우)
lgb_models = None
if routing.get("v63") == "v61":
    try:
        import lightgbm as lgb
        lgb_models = [lgb.Booster(model_file=str(ARTIFACTS_ROOT / PROXY_METER / f"lightgbm_t_plus_{s}.txt"))
                      for s in range(1, HORIZON + 1)]
        print("  LightGBM 로드 완료")
    except Exception as e:
        print(f"  LightGBM 로드 실패 (v57 fallback): {e}")

# CatBoost (v57=v53인 경우)
cb_model = None
if routing.get("v57") == "v53":
    try:
        from catboost import CatBoostRegressor
        cb_model = CatBoostRegressor()
        cb_model.load_model(str(ARTIFACTS_ROOT / PROXY_METER / "catboost.cbm"))
        print("  CatBoost 로드 완료")
    except Exception as e:
        print(f"  CatBoost 로드 실패: {e}")

# Ridge
try:
    ridge_model = joblib.load(ARTIFACTS_ROOT / PROXY_METER / "ridge.joblib")
    print("  Ridge 로드 완료")
except Exception as e:
    print(f"  Ridge 로드 실패: {e}")
    ridge_model = None

# ── 설비별 처리 ────────────────────────────────────────────────────────────────
files = sorted(AIHUB_TRAIN_BASE.glob("Combined_LabelledData_*_역률평균.json"))
print(f"\nAI Hub Training 설비: {len(files)}개")

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
if OUT_PATH.exists():
    done_ids = set(pd.read_csv(OUT_PATH)["device_id"].astype(str))
    print(f"이미 완료: {len(done_ids)}개 — 이어서 실행")
else:
    done_ids = set()

skipped = 0
completed = 0

for fpath in tqdm(files, desc="설비별 이상탐지"):
    device_id = fpath.stem.split("_")[2]
    if device_id in done_ids:
        completed += 1
        continue

    try:
        df_raw = load_device(fpath)
    except Exception as e:
        print(f"[SKIP] {device_id} 로드 실패: {e}")
        skipped += 1
        continue

    if df_raw.empty:
        skipped += 1
        continue

    try:
        df1h = resample_1h(df_raw)
        df1h = apply_physical_rules(df1h)
        df1h = add_features(df1h)
    except Exception as e:
        print(f"[SKIP] {device_id} 전처리 실패: {e}")
        skipped += 1
        continue

    X_list, anchor_list, meta_list = build_windows(df1h, feature_columns, input_scaler, target_scaler, window_size)
    if X_list is None or len(X_list) < MIN_WINDOWS:
        skipped += 1
        continue

    # ── 윈도우별 예측 및 이상 점수 산출 ─────────────────────────────────────
    anomaly_scores = []
    is_anomaly_list = []
    labels = []
    timestamps = []

    for x_window, anchor, meta in zip(X_list, anchor_list, meta_list):
        try:
            # v63 예측 (LSTM top2 앙상블)
            if lstm_models:
                v63_preds = []
                for ver, w in zip(lstm_top2, lstm_top2_w):
                    if ver in lstm_models:
                        # v3는 168h window, 나머지는 24h
                        if ver == "v3" and window_size == 168:
                            pred = predict_lstm(lstm_models[ver], x_window)
                        elif ver != "v3" and window_size == 24:
                            pred = predict_lstm(lstm_models[ver], x_window)
                        else:
                            pred = predict_lstm(lstm_models[ver], x_window[-24:] if window_size == 168 else x_window)
                        v63_preds.append(w * pred)
                v63_scaled = np.sum(v63_preds, axis=0) if v63_preds else np.zeros(HORIZON)
            else:
                v63_scaled = np.zeros(HORIZON)

            # v67 Ridge 예측
            if ridge_model is not None:
                flat = x_window.reshape(1, -1)
                v67_scaled = ridge_model.predict(flat).ravel()[:HORIZON]
            else:
                v67_scaled = np.zeros(HORIZON)

            # v63/v67 복원 (잔차 → 실제값)
            anchor_p = meta["anchor_p"]
            v63_pred = restore_residual(v63_scaled, anchor_p, target_scaler)
            v67_pred = restore_residual(v67_scaled, anchor_p, target_scaler)

            # v71 Seasonal Naive (24h 전 P값)
            p_series = df1h["P"].values
            idx = meta_list.index(meta)
            naive_start = max(0, idx - 24)
            v71_pred = p_series[naive_start:naive_start + HORIZON].astype(np.float32)
            if len(v71_pred) < HORIZON:
                v71_pred = np.full(HORIZON, anchor_p, dtype=np.float32)

            # median 앙상블
            ensemble = np.median(np.stack([v63_pred, v67_pred, v71_pred[:HORIZON]], axis=0), axis=0)

            # bias 보정
            target_end_hour = (pd.to_datetime(meta["ts"]).hour + HORIZON - 1) % 24 if meta["ts"] else 0
            for step in range(1, HORIZON + 1):
                step_corr = bias_df[bias_df["forecast_step"] == step]
                row = step_corr[step_corr["target_hour_utc"] == target_end_hour]
                if not row.empty:
                    ensemble[step - 1] += float(row["median_residual_correction"].iloc[0])

            # anomaly_score_mae
            actual_p = meta["actual_p"]
            abs_errors = np.abs(ensemble - actual_p)
            anomaly_score = float(np.mean(abs_errors))
            is_anom = anomaly_score > anomaly_threshold

            anomaly_scores.append(anomaly_score)
            is_anomaly_list.append(is_anom)
            labels.append(meta["label"])
            timestamps.append(meta["ts"])

        except Exception:
            continue

    if not anomaly_scores:
        skipped += 1
        continue

    # ── 라벨별 집계 ──────────────────────────────────────────────────────────
    results_df = pd.DataFrame({
        "ts": timestamps,
        "label": labels,
        "anomaly_score": anomaly_scores,
        "is_anomaly": is_anomaly_list,
    })

    row = {
        "device_id": device_id,
        "n_windows": len(results_df),
        "anomaly_threshold": anomaly_threshold,
        "overall_detection_rate": float(results_df["is_anomaly"].mean()),
    }

    for label in ["정상", "주의", "경고"]:
        mask = results_df["label"] == label
        n = int(mask.sum())
        if n > 0:
            row[f"n_{label}"] = n
            row[f"detection_rate_{label}"] = float(results_df.loc[mask, "is_anomaly"].mean())
            row[f"mean_score_{label}"] = float(results_df.loc[mask, "anomaly_score"].mean())
        else:
            row[f"n_{label}"] = 0
            row[f"detection_rate_{label}"] = np.nan
            row[f"mean_score_{label}"] = np.nan

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