"""
AI Hub v84 이상탐지 검증
- Training 데이터로 기기별 시간대별 2~98 percentile threshold 계산
- Validation 데이터에서 v84 inference 돌려서 예측값 뽑기
- actual P vs threshold 비교로 po_ao/po_ai/pi_ao/pi_ai 계산
- Honda 파이프라인과 동일한 방식
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")

# ── 경로 설정 ──────────────────────────────────────────────────────────────────
AIHUB_TRAIN_BASE = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/1.Training/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
AIHUB_VAL_BASE   = Path.home() / "SKN25-FINAL-4Team/149.전력_설비_에너지_품질/01.데이터/2.Validation/라벨링데이터/1.펌프_일반모터/2.SOH진단/1.역률평균/0.Combined"
V84_ROOT         = Path.home() / "SKN25-FINAL-4Team/share_test6_residual_v84_20260609"
OUT_PATH         = Path.home() / "SKN25-FINAL-4Team/outputs/aihub_v84_anomaly_per_device.csv"

sys.path.insert(0, str(V84_ROOT))

HORIZON      = 3
PROXY_METER  = "H1.Z10"
FLOOR_LOWER  = -50.0  # Honda와 동일한 floor

ITEM_MAP = {
    "유효전력평균": "P",
    "상전압평균":   "U1",
    "역률평균":     "PF",
}


# ── 데이터 로드 ────────────────────────────────────────────────────────────────
def load_device(fpath: Path) -> pd.DataFrame | None:
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
        })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df = df.pivot_table(index="ts", columns="item", values="value", aggfunc="first").reset_index()
    df.columns.name = None
    df = df.sort_values("ts").reset_index(drop=True)

    g = df.set_index("ts")
    result = pd.DataFrame({
        "P":  g["P"].resample("1h").mean().clip(lower=0),
        "U1": g["U1"].resample("1h").mean(),
        "PF": g["PF"].resample("1h").mean(),
    }).dropna(subset=["P", "U1", "PF"]).reset_index()
    result["ts"] = result["ts"].dt.tz_localize("UTC")
    return result


# ── Training 데이터로 threshold 계산 ──────────────────────────────────────────
def compute_thresholds(train_df: pd.DataFrame) -> pd.DataFrame:
    """시간대별 2~98 percentile threshold 계산 (Honda와 동일)"""
    train_df = train_df.copy()
    train_df["hour"] = train_df["ts"].dt.hour
    rows = []
    for hour in range(24):
        subset = train_df[train_df["hour"] == hour]["P"].dropna()
        if len(subset) < 10:
            continue
        p_lower = float(np.percentile(subset, 2))
        p_upper = float(np.percentile(subset, 98))
        # Honda와 동일한 floor 적용
        if abs(p_lower) < 10:
            p_lower = FLOOR_LOWER
        rows.append({
            "hour":    hour,
            "p_lower": p_lower,
            "p_upper": p_upper,
            "n_samples": len(subset),
        })
    return pd.DataFrame(rows).set_index("hour")


# ── METER_SPECS_BY_URN 패치 ───────────────────────────────────────────────────
from test6_residual.pipeline.common.config import MeterSpec
import test6_residual.pipeline.common.config as _cfg
import test6_residual.pipeline.inference as _inf

def _register_device(device_id: str):
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


def _inject_thresholds(device_id: str, thresholds: pd.DataFrame) -> None:
    """기기별 threshold를 _inf._THRESHOLDS에 주입"""
    _inf._THRESHOLDS[device_id] = {
        int(hour): {
            "p_lower":    float(row["p_lower"]),
            "p_upper":    float(row["p_upper"]),
            "low_sample": bool(row["n_samples"] < 100),
        }
        for hour, row in thresholds.iterrows()
    }


# ── 메인 ──────────────────────────────────────────────────────────────────────
train_files = sorted(AIHUB_TRAIN_BASE.glob("Combined_LabelledData_*_역률평균.json"))
val_files   = sorted(AIHUB_VAL_BASE.glob("Combined_LabelledData_*_역률평균.json"))

train_ids = {f.stem.split("_")[2] for f in train_files}
val_ids   = {f.stem.split("_")[2] for f in val_files}
device_ids = sorted(train_ids & val_ids)  # Training/Validation 모두 있는 기기만

print(f"Training 기기: {len(train_ids)}개, Validation 기기: {len(val_ids)}개")
print(f"공통 기기: {len(device_ids)}개")
print(f"proxy artifact: {PROXY_METER}")

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
if OUT_PATH.exists():
    done_ids = set(pd.read_csv(OUT_PATH)["device_id"].astype(str))
    print(f"이미 완료: {len(done_ids)}개 — 이어서 실행")
else:
    done_ids = set()

skipped   = 0
completed = 0
MIN_HISTORY = 340

for device_id in tqdm(device_ids, desc="기기별 이상탐지 검증"):
    if device_id in done_ids:
        completed += 1
        continue

    # Training 데이터 로드 + threshold 계산
    train_path = AIHUB_TRAIN_BASE / f"Combined_LabelledData_{device_id}_역률평균.json"
    val_path   = AIHUB_VAL_BASE   / f"Combined_LabelledData_{device_id}_역률평균.json"

    train_df = load_device(train_path)
    val_df   = load_device(val_path)

    if train_df is None or val_df is None or len(train_df) < MIN_HISTORY or len(val_df) < HORIZON + 1:
        skipped += 1
        continue

    thresholds = compute_thresholds(train_df)
    if len(thresholds) < 20:
        skipped += 1
        continue

    # Training 뒤에 Validation 이어붙여서 raw_data로 주입
    # Validation 구간 시작 인덱스 기록
    combined_df = pd.concat([train_df, val_df], ignore_index=True).sort_values("ts").reset_index(drop=True)
    val_start_idx = len(train_df)  # combined에서 Validation 시작 위치

    _register_device(device_id)
    _inject_thresholds(device_id, thresholds)

    # Validation 구간에서만 추론 (history는 Training 데이터로 충당)
    rows_out = []

    for i in range(val_start_idx, len(combined_df) - HORIZON):
        ts     = combined_df["ts"].iloc[i]
        actual = combined_df["P"].iloc[i + 1]  # t+1 actual
        hour   = ts.hour

        if hour not in thresholds.index:
            continue
        if np.isnan(actual):
            continue

        p_lower = thresholds.loc[hour, "p_lower"]
        p_upper = thresholds.loc[hour, "p_upper"]

        # actual P 기반 이상 여부 (Honda와 동일)
        is_outlier = (actual < p_lower) or (actual > p_upper)

        try:
            result = _inf.predict_meter(
                engine=None,
                meter_urn=device_id,
                model_urn=PROXY_METER,
                horizon=HORIZON,
                timestamp=ts,
                raw_data=combined_df,
            )
        except Exception:
            continue

        if result is None:
            continue

        warning_flag = result.get("warning_flag", False)

        rows_out.append({
            "ts":          ts,
            "actual_P":    actual,
            "p_lower":     p_lower,
            "p_upper":     p_upper,
            "is_outlier":  is_outlier,
            "warning_flag": warning_flag,
        })

    if len(rows_out) < 5:
        skipped += 1
        continue

    res_df = pd.DataFrame(rows_out)

    # 4분류 계산 (Honda와 동일)
    po_ao = int(( res_df["is_outlier"] &  res_df["warning_flag"]).sum())  # 이상 & 경보
    po_ai = int((~res_df["is_outlier"] &  res_df["warning_flag"]).sum())  # 정상 & 경보 (오탐)
    pi_ao = int(( res_df["is_outlier"] & ~res_df["warning_flag"]).sum())  # 이상 & 무경보 (미탐)
    pi_ai = int((~res_df["is_outlier"] & ~res_df["warning_flag"]).sum())  # 정상 & 무경보

    n_total   = len(res_df)
    n_outlier = int(res_df["is_outlier"].sum())
    n_warning = int(res_df["warning_flag"].sum())

    warning_rate = n_warning / n_total * 100 if n_total > 0 else np.nan
    po_ao_rate   = po_ao / n_total * 100 if n_total > 0 else np.nan
    po_ai_rate   = po_ai / n_total * 100 if n_total > 0 else np.nan
    pi_ao_rate   = pi_ao / n_total * 100 if n_total > 0 else np.nan

    row = {
        "device_id":    device_id,
        "n_total":      n_total,
        "n_outlier":    n_outlier,
        "n_warning":    n_warning,
        "warning_rate": round(warning_rate, 4),
        "po_ao":        po_ao,
        "po_ai":        po_ai,
        "pi_ao":        pi_ao,
        "pi_ai":        pi_ai,
        "po_ao_rate":   round(po_ao_rate, 4),
        "po_ai_rate":   round(po_ai_rate, 4),
        "pi_ao_rate":   round(pi_ao_rate, 4),
    }

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
    print(f"\n=== 결과 요약 ({len(df_res)}개 기기) ===")
    print(f"  평균 경보율:      {df_res['warning_rate'].mean():.2f}%")
    print(f"  평균 po_ao율:     {df_res['po_ao_rate'].mean():.2f}%")
    print(f"  평균 po_ai율:     {df_res['po_ai_rate'].mean():.2f}%")
    print(f"  평균 pi_ao율:     {df_res['pi_ao_rate'].mean():.2f}%")
    print(f"\n결과 저장: {OUT_PATH}")

print("\n완료.")