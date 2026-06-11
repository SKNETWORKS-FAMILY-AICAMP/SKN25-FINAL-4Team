"""
inference.py 기반 v84 AI Hub Training 데이터 추론
- Honda 가중치(proxy: H1.Z10) 그대로 사용
- AI Hub 데이터를 raw_data로 직접 주입 (DB 조회 없음)
- METER_SPECS_BY_URN에 AI Hub 설비 등록 후 predict_meter 호출
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
V84_ROOT = Path.home() / "SKN25-FINAL-4Team/share_test6_residual_v84_20260609"
OUT_PATH = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_v84_inference_per_device.csv"

sys.path.insert(0, str(V84_ROOT))

HORIZON    = 3
PROXY_METER = "H1.Z10"  # Honda artifact 대표 계량기

ITEM_MAP = {
    "유효전력평균": "P",
    "상전압평균":   "U1",
    "역률평균":     "PF",
}


# ── AI Hub 데이터 로드 ─────────────────────────────────────────────────────────
def load_aihub_device(device_id: str) -> pd.DataFrame | None:
    fpath = AIHUB_TRAIN_BASE / f"Combined_LabelledData_{device_id}_역률평균.json"
    if not fpath.exists():
        return None
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
        return None
    df = pd.DataFrame(rows)
    df = df.pivot_table(index=["ts", "label"], columns="item", values="value", aggfunc="first").reset_index()
    df.columns.name = None
    df = df.sort_values("ts").reset_index(drop=True)

    # 1시간 리샘플링
    g = df.set_index("ts")
    result = pd.DataFrame({
        "P":     g["P"].resample("1h").mean().clip(lower=0),
        "U1":    g["U1"].resample("1h").mean(),
        "PF":    g["PF"].resample("1h").mean(),
        "label": g["label"].resample("1h").last(),
    }).dropna(subset=["P", "U1", "PF"]).reset_index()
    result["ts"] = result["ts"].dt.tz_localize("UTC")
    result["meter_urn"] = device_id
    return result


# ── METER_SPECS_BY_URN 패치 ───────────────────────────────────────────────────
from test6_residual.pipeline.common.config import MeterSpec
import test6_residual.pipeline.common.config as _cfg
import test6_residual.pipeline.inference as _inf

def _register_device(device_id: str):
    """AI Hub 설비를 METER_SPECS_BY_URN에 등록"""
    spec = MeterSpec(
        meter_urn=device_id,
        group="electric",
        role="singleton",
        features=("P", "U1", "PF"),
        source="aihub",
        note="AI Hub pump/motor device",
    )
    _cfg.METER_SPECS_BY_URN[device_id] = spec
    _inf.METER_SPECS_BY_URN[device_id] = spec
    return spec


def calc_mape(actual, pred):
    mask = np.abs(actual) > 1e-6
    if mask.sum() == 0:
        return np.nan
    return float(np.mean(np.abs((actual[mask] - pred[mask]) / actual[mask])) * 100)


# ── 메인 ──────────────────────────────────────────────────────────────────────
train_files = sorted(AIHUB_TRAIN_BASE.glob("Combined_LabelledData_*_역률평균.json"))
device_ids  = sorted({f.stem.split("_")[2] for f in train_files})
print(f"AI Hub Training 설비: {len(device_ids)}개")
print(f"proxy artifact: {PROXY_METER}")

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

    df = load_aihub_device(device_id)
    if df is None or len(df) < 170:
        skipped += 1
        continue

    # METER_SPECS_BY_URN에 등록
    _register_device(device_id)

    # 슬라이딩 윈도우 추론
    preds_list, actuals_list, persist_list, meta_list = [], [], [], []
    MIN_HISTORY = 340  # needed = 168 + 168 + horizon + 4

    for i in range(MIN_HISTORY, len(df) - HORIZON):
        ts        = df["ts"].iloc[i]
        actual    = df["P"].iloc[i:i + HORIZON].to_numpy(dtype=np.float32)
        persistence = float(df["P"].iloc[i - 1])

        if np.isnan(actual).any():
            continue

        try:
            result = _inf.predict_meter(
                engine=None,
                meter_urn=device_id,
                model_urn=PROXY_METER,
                horizon=HORIZON,
                timestamp=ts,
                raw_data=df,
            )
        except Exception:
            continue

        if result is None:
            continue

        pred = np.array([
            result.get(f"pred_t_plus_{h+1}", np.nan) for h in range(HORIZON)
        ], dtype=np.float32)

        if np.isnan(pred).any():
            continue

        preds_list.append(pred)
        actuals_list.append(actual)
        persist_list.append(np.full(HORIZON, persistence, dtype=np.float32))
        meta_list.append({
            "ts":          ts,
            "label":       df["label"].iloc[i],
            "persistence": persistence,
        })

    if len(preds_list) < 5:
        skipped += 1
        continue

    preds    = np.array(preds_list,   dtype=np.float32)
    actuals  = np.array(actuals_list, dtype=np.float32)
    persists = np.array(persist_list, dtype=np.float32)
    meta_df  = pd.DataFrame(meta_list)

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