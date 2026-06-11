"""
v84 앙상블 모델로 AI Hub Training 데이터 검증
- AI Hub 펌프/일반모터 73개 설비 데이터를 1시간 리샘플링
- v84 artifact 로드 후 설비별 추론
- 예측 정확도(MAPE, RMSE, beats_persistence) + 라벨별 MAE
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
V84_ROOT = Path.home() / "SKN25-FINAL-4Team/share_test6_residual_v84_20260609/test6_residual/pipeline"
OUT_PATH = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_v84_validation_per_device.csv"

# v84 파이프라인 경로 추가
sys.path.insert(0, str(V84_ROOT))

HORIZON = 3
MIN_HISTORY_HOURS = 200  # diff_lag168 생성에 최소 168시간 필요, 여유 추가
MIN_TEST_WINDOWS = 5

ITEM_MAP = {
    "유효전력평균": "P",
    "상전압평균":   "U1",
    "역률평균":     "PF",
}


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


def resample_1h(df: pd.DataFrame) -> pd.DataFrame:
    g = df.set_index("ts")
    result = pd.DataFrame({
        "P":     g["P"].resample("1h").mean().clip(lower=0),
        "U1":    g["U1"].resample("1h").mean(),
        "PF":    g["PF"].resample("1h").mean(),
        "label": g["label"].resample("1h").last(),
    }).dropna(subset=["P", "U1", "PF"]).reset_index()
    return result


# ── v84 피처 생성 ──────────────────────────────────────────────────────────────
def add_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.sort_values("ts").copy().reset_index(drop=True)
    p = d["P"]
    hour = d["ts"].dt.hour
    dow  = d["ts"].dt.dayofweek
    mon  = d["ts"].dt.month

    d["hour_sin"]        = np.sin(2 * np.pi * hour / 24)
    d["hour_cos"]        = np.cos(2 * np.pi * hour / 24)
    d["day_of_week_sin"] = np.sin(2 * np.pi * dow / 7)
    d["day_of_week_cos"] = np.cos(2 * np.pi * dow / 7)
    d["month_sin"]       = np.sin(2 * np.pi * (mon - 1) / 12)
    d["month_cos"]       = np.cos(2 * np.pi * (mon - 1) / 12)
    d["is_workday"]      = (dow < 5).astype(float)
    d["diff_lag24"]      = p.diff(24)
    d["diff_lag168"]     = p.diff(168)
    d["rolling_mean_24h"] = p.rolling(24, min_periods=1).mean()
    return d


# ── 간단한 앙상블 예측 (artifact 직접 로드) ──────────────────────────────────
def load_artifact(meter_urn: str) -> dict | None:
    """v84 artifact에서 필요한 것만 로드 (Ridge + Seasonal Naive 기반 간이 앙상블)"""
    art_dir = V84_ROOT / "artifacts" / f"{HORIZON}h" / meter_urn
    if not art_dir.exists():
        return None
    try:
        import joblib
        routing_path = art_dir / "routing.json"
        if not routing_path.exists():
            return None
        with open(routing_path) as f:
            routing = json.load(f)

        input_scaler  = joblib.load(art_dir / "input_scaler.joblib")
        target_scaler = joblib.load(art_dir / "target_scaler.joblib")

        ridge = joblib.load(art_dir / "ridge.joblib")

        bias_df = None
        bias_path = art_dir / "hour_bias_corrections.csv"
        if bias_path.exists():
            bias_df = pd.read_csv(bias_path)

        # feature_columns.json 별도 파일에서 로드
        fc_path = art_dir / "feature_columns.json"
        if fc_path.exists():
            with open(fc_path) as f:
                feature_columns = json.load(f)
        else:
            feature_columns = [
                "P", "U1", "PF",
                "hour_sin", "hour_cos", "day_of_week_sin", "day_of_week_cos",
                "month_sin", "month_cos",
                "diff_lag24", "diff_lag168", "is_workday", "rolling_mean_24h",
            ]

        return {
            "routing":         routing,
            "feature_columns": feature_columns,
            "input_scaler":    input_scaler,
            "target_scaler":   target_scaler,
            "ridge":           ridge,
            "bias_df":         bias_df,
            "art_dir":         art_dir,
            "scale_cols":      list(input_scaler.feature_names_in_),
        }
    except Exception as e:
        print(f"  artifact 로드 실패 ({meter_urn}): {e}")
        return None


def predict_window(df_window: pd.DataFrame, artifact: dict, timestamp: pd.Timestamp) -> np.ndarray | None:
    """단일 윈도우 예측 (Ridge + Seasonal Naive median 앙상블)"""
    WINDOW_SIZE = 24
    routing       = artifact["routing"]
    input_scaler  = artifact["input_scaler"]
    target_scaler = artifact["target_scaler"]
    ridge         = artifact["ridge"]
    bias_df       = artifact["bias_df"]
    scale_cols    = artifact.get("scale_cols", list(input_scaler.feature_names_in_))

    # 피처 컬럼 목록
    feature_cols = artifact.get("feature_columns", [
        "P", "U1", "PF",
        "hour_sin", "hour_cos", "day_of_week_sin", "day_of_week_cos",
        "month_sin", "month_cos",
        "diff_lag24", "diff_lag168", "is_workday", "rolling_mean_24h",
    ])

    # 최근 24시간 윈도우
    window = df_window.tail(WINDOW_SIZE)
    if len(window) < WINDOW_SIZE:
        return None

    available = [c for c in feature_cols if c in window.columns]
    if len(available) < len(feature_cols):
        return None

    x = window[feature_cols].to_numpy(dtype=np.float32)
    if np.isnan(x).any():
        return None

    # anchor P (마지막 실제값, 잔차 복원용)
    anchor_p = float(window["P"].iloc[-1])

    # Ridge 예측 (잔차 복원)
    try:
        # input_scaler는 7개 피처만 스케일링
        scale_cols = list(input_scaler.feature_names_in_)
        x_to_scale = window[scale_cols].to_numpy(dtype=np.float32)
        x_scaled_part = input_scaler.transform(x_to_scale)

        # 시간 피처는 스케일링 없이 그대로
        time_cols = [c for c in feature_cols if c not in scale_cols]
        x_time = window[time_cols].to_numpy(dtype=np.float32) if time_cols else np.empty((WINDOW_SIZE, 0), dtype=np.float32)

        # 피처 순서: feature_cols 순서대로 재조합
        x_full = np.zeros((WINDOW_SIZE, len(feature_cols)), dtype=np.float32)
        scale_idx = [feature_cols.index(c) for c in scale_cols]
        time_idx  = [feature_cols.index(c) for c in time_cols]
        for j, idx in enumerate(scale_idx):
            x_full[:, idx] = x_scaled_part[:, j]
        for j, idx in enumerate(time_idx):
            x_full[:, idx] = x_time[:, j]

        x_flat = x_full.flatten().reshape(1, -1)
        ridge_residual_scaled = ridge.predict(x_flat)
        if ridge_residual_scaled.ndim == 1:
            ridge_residual_scaled = ridge_residual_scaled.reshape(1, -1)

        # 잔차 복원: pred = inv(anchor_scaled + residual_scaled)
        anchor_scaled = float(target_scaler.transform([[anchor_p]])[0][0])
        ridge_pred = np.array([
            float(target_scaler.inverse_transform([[anchor_scaled + ridge_residual_scaled[0][i]]])[0][0])
            for i in range(min(HORIZON, ridge_residual_scaled.shape[1]))
        ], dtype=np.float32)
    except Exception:
        return None

    # Seasonal Naive (24시간 전 값)
    p_vals = window["P"].to_numpy(dtype=np.float32)
    if len(p_vals) >= 24:
        naive_pred = p_vals[-24:-24 + HORIZON] if HORIZON <= 24 else p_vals[-24:]
        naive_pred = np.resize(naive_pred, HORIZON)
    else:
        naive_pred = np.full(HORIZON, anchor_p, dtype=np.float32)

    # Median 앙상블
    ensemble = np.median(np.stack([ridge_pred, naive_pred[:HORIZON]], axis=0), axis=0)

    # Bias 보정
    if bias_df is not None:
        target_end_hour = (timestamp.hour + HORIZON - 1) % 24
        for step in range(1, HORIZON + 1):
            step_corr = bias_df[bias_df["forecast_step"] == step]
            row = step_corr[step_corr["target_hour_utc"] == target_end_hour]
            if not row.empty:
                ensemble[step - 1] += float(row["median_residual_correction"].iloc[0])
            elif not step_corr.empty:
                ensemble[step - 1] += float(step_corr["fallback_global_correction"].iloc[0])

    return ensemble.astype(np.float32)


def calc_mape(actual, pred):
    mask = np.abs(actual) > 1e-6
    if mask.sum() == 0:
        return np.nan
    return float(np.mean(np.abs((actual[mask] - pred[mask]) / actual[mask])) * 100)


# ── 메인 ──────────────────────────────────────────────────────────────────────
# 사용 가능한 artifact 목록 확인
artifact_dir_3h = V84_ROOT / "artifacts" / f"{HORIZON}h"
available_meters = [d.name for d in artifact_dir_3h.iterdir() if d.is_dir()] if artifact_dir_3h.exists() else []
print(f"v84 artifact 계량기: {len(available_meters)}개")

# AI Hub 설비 목록
train_files = sorted(AIHUB_TRAIN_BASE.glob("Combined_LabelledData_*_역률평균.json"))
device_ids  = sorted({f.stem.split("_")[2] for f in train_files})
print(f"AI Hub Training 설비: {len(device_ids)}개")

# 이어서 돌리기
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
if OUT_PATH.exists():
    done_ids = set(pd.read_csv(OUT_PATH)["device_id"].astype(str))
    print(f"이미 완료: {len(done_ids)}개 — 이어서 실행")
else:
    done_ids = set()

# v84는 Honda 계량기 기준 artifact → 대표 계량기 1개 선택 (H1.Z10 또는 첫 번째)
# AI Hub 설비는 Honda 계량기와 다르므로 가장 범용적인 대표 계량기 artifact 사용
PROXY_METER = "H1.Z10" if "H1.Z10" in available_meters else (available_meters[0] if available_meters else None)
if PROXY_METER is None:
    print("사용 가능한 artifact 없음. 종료.")
    sys.exit(1)

print(f"proxy artifact: {PROXY_METER}")
artifact = load_artifact(PROXY_METER)
if artifact is None:
    print("artifact 로드 실패. 종료.")
    sys.exit(1)

skipped   = 0
completed = 0

for device_id in tqdm(device_ids, desc="설비별 추론(v84)"):
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

    if len(df) < MIN_HISTORY_HOURS + HORIZON:
        skipped += 1
        continue

    df = df.dropna(subset=["P", "U1", "PF", "diff_lag24", "diff_lag168"]).reset_index(drop=True)
    if len(df) < MIN_HISTORY_HOURS + HORIZON:
        skipped += 1
        continue

    # 슬라이딩 윈도우 추론
    preds_list, actuals_list, persist_list, meta_list = [], [], [], []
    WINDOW_SIZE = 24

    for i in range(MIN_HISTORY_HOURS, len(df) - HORIZON):
        window = df.iloc[:i]
        ts     = df["ts"].iloc[i]
        actual = df["P"].iloc[i:i + HORIZON].to_numpy(dtype=np.float32)
        persistence = float(df["P"].iloc[i - 1])

        if np.isnan(actual).any():
            continue

        pred = predict_window(window, artifact, ts)
        if pred is None:
            continue

        preds_list.append(pred)
        actuals_list.append(actual)
        persist_list.append(np.full(HORIZON, persistence, dtype=np.float32))
        meta_list.append({
            "ts":          ts,
            "label":       df["label"].iloc[i],
            "persistence": persistence,
        })

    if len(preds_list) < MIN_TEST_WINDOWS:
        skipped += 1
        continue

    preds      = np.array(preds_list,   dtype=np.float32)
    actuals    = np.array(actuals_list, dtype=np.float32)
    persists   = np.array(persist_list, dtype=np.float32)
    meta_df    = pd.DataFrame(meta_list)

    ensemble_rmse    = float(np.sqrt(mean_squared_error(actuals, preds)))
    persistence_rmse = float(np.sqrt(mean_squared_error(actuals, persists)))
    improvement      = (persistence_rmse - ensemble_rmse) / persistence_rmse * 100 if persistence_rmse > 0 else float("-inf")
    ensemble_mape    = calc_mape(actuals.flatten(), preds.flatten())
    persistence_mape = calc_mape(actuals.flatten(), persists.flatten())

    meta_df["mae"]             = np.mean(np.abs(preds   - actuals), axis=1)
    meta_df["persistence_mae"] = np.mean(np.abs(persists - actuals), axis=1)

    row = {
        "device_id":        device_id,
        "n_test_windows":   len(preds_list),
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