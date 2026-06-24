"""
Anomaly v84 inference runtime.

흐름:
  1. mapping.py에서 63개 계량기 라우팅 조회 (model_urn 결정)
  2. DB에서 계량기별 최근 데이터 조회
  3. 1차 분류: 물리적 이상치 태그
  4. 계량기별 예측 (잔차 복원 포함):
       v63(LSTM/CatBoost) + v67(Ridge) + v71(Seasonal Naive) → median 앙상블 → bias 보정
  5. 사전 경보: pred_t_plus_h vs val actual P 시간대별 2~98 percentile threshold (val_thresholds.csv)
  6. CSV 저장 + dict 반환

[잔차 복원]
  학습 target = scaled_P(t+h) - scaled_P(t-1)  (scaled 공간 잔차)
  복원: pred_actual = inv(anchor_scaled + pred_residual_scaled)
        where anchor_scaled = target_scaler.transform([[P(t-1)]])

CLI:
  conda run -n skn25 python -m cms.modeling.anomaly.predictor \\
      --horizon 3 --timestamp "2024-01-01T09:00:00+00:00"
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")

import numpy as np
import pandas as pd

from cms.modeling.anomaly.config import (
    BATCH_SIZE,
    ELECTRIC_MEASUREMENTS,
    HIDDEN_SIZE,
    LSTM_VARIANTS,
    MAX_FFILL_HOURS,
    METER_SPECS_BY_URN,
    MeterSpec,
    THERMAL_MEASUREMENTS,
)
from cms.modeling.anomaly.mapping import ARTIFACTS_DIR, METER_MAP

THRESHOLDS_PATH      = ARTIFACTS_DIR / "thresholds" / "val_thresholds.csv"
METER_TAGS_PATH      = Path(__file__).resolve().parent / "resources" / "meter_tags.csv"
_SEV_ORDER           = {"high": 3, "medium": 2, "low": 1}
PHYSICAL_RECENT_HOURS = 24  # physical_flag 및 recent_count 기준 시간
RAW_QUALITY_COLUMNS = tuple(dict.fromkeys((*ELECTRIC_MEASUREMENTS, *THERMAL_MEASUREMENTS)))
# Operational gate: allow short local gaps, but reject windows that are broadly stale.
MAX_TOTAL_MISSING_ROWS = 4
DEBUG_CSV_DROP_COLUMNS = (
    "physical_issue_count",
    "input_imputed_count",
)


def _load_thresholds() -> dict[str, dict[int, dict]]:
    """val_thresholds.csv → {meter_urn: {hour: {p_lower, p_upper, low_sample}}}."""
    if not THRESHOLDS_PATH.exists():
        return {}
    df = pd.read_csv(THRESHOLDS_PATH)
    out: dict[str, dict[int, dict]] = {}
    for _, row in df.iterrows():
        urn = row["meter_urn"]
        if urn not in out:
            out[urn] = {}
        out[urn][int(row["hour"])] = {
            "p_lower":    float(row["p_lower"]),
            "p_upper":    float(row["p_upper"]),
            "low_sample": bool(row.get("low_sample", False)),
        }
    return out


_THRESHOLDS: dict[str, dict[int, dict]] = {}


def _load_meter_tags() -> dict[str, list[dict]]:
    """meter_tags.csv → {meter_urn: [{issue_type, issue_detail, severity, evidence, since, until}, ...]}"""
    if not METER_TAGS_PATH.exists():
        return {}
    df = pd.read_csv(METER_TAGS_PATH)
    df = df[df["active"].astype(str).str.lower().isin(["true", "1", "yes"])]
    out: dict[str, list[dict]] = {}
    for _, row in df.iterrows():
        urn = row["meter_urn"]
        if urn not in out:
            out[urn] = []
        since_raw = row.get("since", "")
        until_raw = row.get("until", "")
        since = pd.Timestamp(since_raw, tz="UTC") if pd.notna(since_raw) and str(since_raw).strip() else None
        until = pd.Timestamp(until_raw, tz="UTC") if pd.notna(until_raw) and str(until_raw).strip() else None
        out[urn].append({
            "issue_type":   str(row["issue_type"]),
            "issue_detail": str(row["issue_detail"]),
            "severity":     str(row.get("severity", "medium")),
            "evidence":     str(row.get("evidence", "")),
            "since":        since,
            "until":        until,
        })
    return out


def _tag_fields(meter_urn: str, timestamp: pd.Timestamp | None = None) -> dict:
    all_tags = _METER_TAGS.get(meter_urn, [])
    # timestamp가 주어지면 since/until 조건 필터링
    if timestamp is not None:
        tags = [
            t for t in all_tags
            if (t["since"] is None or timestamp >= t["since"])
            and (t["until"] is None or timestamp < t["until"])
        ]
    else:
        tags = all_tags
    if not tags:
        return {"meter_issue_types": "", "meter_issue_detail": "", "meter_issue_severity": ""}
    types    = "|".join(t["issue_type"] for t in tags)
    detail   = tags[0]["issue_detail"]
    severity = max((t["severity"] for t in tags), key=lambda s: _SEV_ORDER.get(s, 0))
    return {"meter_issue_types": types, "meter_issue_detail": detail, "meter_issue_severity": severity}


def _failed_row(
    meter_urn: str,
    model_urn: str,
    horizon: int,
    timestamp: pd.Timestamp,
    status: str,
    *,
    warning_reason_code: str = "NO_PREDICTION",
    warning_reason_detail: str | None = None,
    input_quality: str = "bad",
    quality_info: dict[str, Any] | None = None,
) -> dict:
    row: dict = {
        "meter_urn":     meter_urn,
        "model_urn":     model_urn,
        "timestamp":     timestamp.isoformat(),
        "horizon":       horizon,
        "physical_flag":               False,
        "physical_issue_types":        None,
        "physical_issue_count":        0,
        "physical_issue_recent_count": 0,
        "physical_issue_pattern":      "none",
        "physical_issue_detail":       None,
        "input_quality":               input_quality,
        "input_missing_count":         0,
        "input_physical_count":        0,
        "input_imputed_count":         0,
        "warning_flag":                False,
        "warning_reason_code":         warning_reason_code,
        "warning_reason_detail":       warning_reason_detail if warning_reason_detail is not None else f"status={status}",
        "status":                      status,
    }
    if quality_info:
        row["input_quality"] = quality_info.get("input_quality", row["input_quality"])
        for key in ("input_missing_count", "input_physical_count", "input_imputed_count"):
            if key in quality_info:
                row[key] = quality_info[key]
    for step in range(1, horizon + 1):
        row[f"pred_t_plus_{step}"]             = float("nan")
        row[f"target_hour_t_plus_{step}"]      = float("nan")
        row[f"threshold_lower_t_plus_{step}"]  = float("nan")
        row[f"threshold_upper_t_plus_{step}"]  = float("nan")
        row[f"warning_type_t_plus_{step}"]     = "none"
        row[f"warning_t_plus_{step}"]          = False
        row[f"low_sample_t_plus_{step}"]       = False
    row.update(_tag_fields(meter_urn, timestamp))
    return row


_METER_TAGS: dict[str, list[dict]] = {}
_ARTIFACT_CACHE: dict = {}


def _cached(key, loader):
    """lazy artifact cache: 동일 key는 1회만 로드."""
    if key not in _ARTIFACT_CACHE:
        _ARTIFACT_CACHE[key] = loader()
    return _ARTIFACT_CACHE[key]
from cms.modeling.anomaly.db import build_engine, fetch_meter_window
from cms.modeling.anomaly.preprocessing import (
    add_derived_features,
    add_time_features,
    apply_physical_rules,
    model_feature_columns,
    normalize_ts,
    scaled_feature_columns,
    transform_input,
)
from cms.modeling.anomaly.model import RecurrentPredictor, device, predict_scaled
from cms.modeling.anomaly.catboost_model import predict_catboost_scaled
from cms.modeling.anomaly.ridge import predict_ridge_scaled
from cms.modeling.anomaly.artifacts import (
    load_bias_corrections,
    load_catboost,
    load_feature_columns,
    load_lstm_model,
    load_ridge,
    load_routing,
    load_scalers,
)

# ── LSTM 버전별 설정 (v1=시간피처없음, v4=GRU 등) ─────────────────────────────
_VERSION_USE_TIME: dict[str, bool] = {v.version: v.use_time_features for v in LSTM_VARIANTS}
_VERSION_ARCH:     dict[str, str]  = {v.version: v.model_architecture for v in LSTM_VARIANTS}


def _load_lightgbm(model_urn: str, horizon: int) -> list:
    """lightgbm_t_plus_{step}.txt → lgb.Booster 리스트."""
    import lightgbm as lgb
    d = ARTIFACTS_DIR / f"{horizon}h" / model_urn
    return [lgb.Booster(model_file=str(d / f"lightgbm_t_plus_{s}.txt")) for s in range(1, horizon + 1)]


def _predict_lightgbm_scaled(models: list, x: np.ndarray) -> np.ndarray:
    """(1, window, feat) → (1, horizon)."""
    flat = x.reshape(x.shape[0], -1)
    preds = [m.predict(flat) for m in models]
    return np.column_stack(preds).astype(np.float32)  # (N, horizon)


# ─────────────────────────────────────────────────────────────────────────────
# 1차 분류: 물리적 이상치 태그
# ─────────────────────────────────────────────────────────────────────────────

def tag_physical_anomalies(df: pd.DataFrame, spec: MeterSpec) -> pd.DataFrame:
    tagged = df.copy()
    tagged["physical_flag"] = False
    if "U1" in tagged.columns:
        u1 = pd.to_numeric(tagged["U1"], errors="coerce")
        tagged.loc[(u1 <= 0) | (u1 > 1000.0), "physical_flag"] = True
    if "PF" in tagged.columns:
        pf = pd.to_numeric(tagged["PF"], errors="coerce").abs()
        tagged.loc[pf > 1.0, "physical_flag"] = True
    if "qv" in tagged.columns:
        tagged.loc[pd.to_numeric(tagged["qv"], errors="coerce") < 0, "physical_flag"] = True
    return tagged


def _get_effective_window(routing: dict) -> int:
    """실제 추론에 사용된 모델의 입력 window 크기 반환."""
    if routing.get("v63") == "v61":        # LightGBM
        return 24
    if routing.get("v57") == "v53":        # CatBoost (v3 체크보다 먼저)
        return 24
    if routing.get("v52_source") == "v3":  # v3 LSTM (window_size=168)
        return 168
    return 24


def _compute_input_quality(raw: pd.DataFrame, spec: MeterSpec,
                           timestamp: pd.Timestamp, routing: dict) -> dict:
    """effective window 기준 입력 품질 계산. 4개 키 반환."""
    eff_window = _get_effective_window(routing)
    cutoff = timestamp - pd.Timedelta(hours=eff_window)

    ts_utc     = pd.to_datetime(raw["ts"], utc=True)
    window_raw = raw[ts_utc >= cutoff].copy()

    # ── full hourly grid (effective window) ──────────────────────────────────
    full_idx    = pd.date_range(cutoff, timestamp - pd.Timedelta(hours=1), freq="h", tz="UTC")
    existing_ts = set(pd.to_datetime(window_raw["ts"], utc=True))
    expected_latest_ts = timestamp - pd.Timedelta(hours=1)
    latest_input_missing = expected_latest_ts not in existing_ts

    # ── input_missing_count ──────────────────────────────────────────────────
    # 아예 없는 ts(gap) + 원천 계측값 중 하나라도 NaN인 행.
    # lag/rolling/time feature는 추론 내부에서 재계산되므로 DB missing으로 세지 않는다.
    gap_ts = {t for t in full_idx if t not in existing_ts}
    gap_count = len(gap_ts)
    raw_quality_cols = [c for c in RAW_QUALITY_COLUMNS if c in spec.features and c in window_raw.columns]
    if raw_quality_cols:
        feat_nan_mask = window_raw[raw_quality_cols].apply(
            lambda s: pd.to_numeric(s, errors="coerce")
        ).isna().any(axis=1)
        feat_nan_count = int(feat_nan_mask.sum())
    else:
        feat_nan_count = 0
    input_missing_count = gap_count + feat_nan_count

    # ── input_physical_count ─────────────────────────────────────────────────
    phys_mask = pd.Series(False, index=window_raw.index)
    if "PF" in window_raw.columns:
        phys_mask |= (pd.to_numeric(window_raw["PF"], errors="coerce").abs() > 1.0).fillna(False)
    if "U1" in window_raw.columns:
        u1 = pd.to_numeric(window_raw["U1"], errors="coerce")
        phys_mask |= ((u1 <= 0) | (u1 > 1000.0)).fillna(False)
    if "qv" in window_raw.columns:
        phys_mask |= (pd.to_numeric(window_raw["qv"], errors="coerce") < 0).fillna(False)
    input_physical_count = int(phys_mask.sum())

    # ── input_imputed_count (ffill로 실제 채워진 행 수, row 기준) ─────────────
    # 원천 계측값 기준: reindex → 물리 위반 NaN 처리 → ffill 전후 비교
    # 같은 timestamp에서 여러 feature가 보간되어도 1 row로 카운트 (missing/physical과 단위 일관)
    win_idx = window_raw.copy()
    win_idx["_ts"] = pd.to_datetime(win_idx["ts"], utc=True)
    win_idx = win_idx.set_index("_ts").reindex(full_idx)

    # 각 feature 전처리 후 before/after NaN을 row OR로 집계
    before_nan_mask = pd.Series(False, index=full_idx)
    after_nan_mask  = pd.Series(False, index=full_idx)
    for col in raw_quality_cols:
        col_ser = pd.to_numeric(win_idx[col], errors="coerce").copy()
        if col == "PF":
            col_ser[col_ser.abs() > 1.0] = float("nan")
        elif col == "U1":
            col_ser[(col_ser <= 0) | (col_ser > 1000.0)] = float("nan")
        elif col == "qv":
            col_ser[col_ser < 0] = float("nan")
        before_nan_mask |= col_ser.isna()
        after_nan_mask  |= col_ser.ffill(limit=MAX_FFILL_HOURS).isna()
    # 보간된 row = ffill 전에는 NaN이었지만 ffill 후에는 완전히 채워진 row
    input_imputed_count = int((before_nan_mask & ~after_nan_mask).sum())

    # ── input_quality 등급 ───────────────────────────────────────────────────
    # problem_rows = missing OR physical (중복 제거)
    problem_ts: set = set(gap_ts)
    missing_ts: set = set(gap_ts)
    win_ts_arr = pd.to_datetime(window_raw["ts"], utc=True)
    feat_nan_any = feat_nan_mask if raw_quality_cols else pd.Series(False, index=window_raw.index)
    for t, is_nan in zip(win_ts_arr, feat_nan_any):
        if is_nan:
            problem_ts.add(t)
            missing_ts.add(t)
    for t, is_phys in zip(win_ts_arr, phys_mask):
        if is_phys:
            problem_ts.add(t)

    total_missing_rows = len(missing_ts)
    max_consecutive_missing = 0
    current_missing = 0
    for t in full_idx:
        if t in missing_ts:
            current_missing += 1
            max_consecutive_missing = max(max_consecutive_missing, current_missing)
        else:
            current_missing = 0

    n_problem = len(problem_ts)
    if n_problem == 0:
        quality = "good"
    elif n_problem <= 3:
        quality = "warning"
    else:
        quality = "bad"

    return {
        "input_quality":        quality,
        "input_missing_count":  input_missing_count,
        "input_physical_count": input_physical_count,
        "input_imputed_count":  input_imputed_count,
        "latest_input_missing": latest_input_missing,
        "expected_latest_input_ts": expected_latest_ts.isoformat(),
        "total_missing_rows": total_missing_rows,
        "max_consecutive_missing": max_consecutive_missing,
    }


def _compute_warning_reason(result: dict) -> dict:
    """warning_reason_code / warning_reason_detail 계산."""
    status = result.get("status", "")

    # 0. 추론 실패
    if status != "success":
        return {
            "warning_reason_code":   f"NO_PREDICTION",
            "warning_reason_detail": f"status={status}",
        }

    # warning 없으면 NONE
    if not result.get("warning_flag", False):
        return {"warning_reason_code": "NONE", "warning_reason_detail": None}

    # 1. KNOWN_METER_ISSUE
    issue_types = result.get("meter_issue_types")
    if issue_types:
        return {
            "warning_reason_code":   "KNOWN_METER_ISSUE",
            "warning_reason_detail": str(issue_types),
        }

    # 2. INPUT_QUALITY_ISSUE
    quality = result.get("input_quality", "good")
    if quality in ("warning", "bad"):
        mc = result.get("input_missing_count", 0)
        pc = result.get("input_physical_count", 0)
        return {
            "warning_reason_code":   "INPUT_QUALITY_ISSUE",
            "warning_reason_detail": f"quality={quality}, missing={mc}, physical={pc}",
        }

    # 3. HIGH / LOW (t+3 우선, 없으면 t+2, t+1)
    horizon = result.get("horizon", 3)
    for step in range(horizon, 0, -1):
        w_type = result.get(f"warning_type_t_plus_{step}", "none")
        if w_type == "high":
            pred  = result.get(f"pred_t_plus_{step}", float("nan"))
            upper = result.get(f"threshold_upper_t_plus_{step}", float("nan"))
            return {
                "warning_reason_code":   "HIGH_LOAD_VS_USUAL_HOUR",
                "warning_reason_detail": f"t+{step}: pred={pred:.1f} > upper={upper:.1f}",
            }
        if w_type == "low":
            pred  = result.get(f"pred_t_plus_{step}", float("nan"))
            lower = result.get(f"threshold_lower_t_plus_{step}", float("nan"))
            return {
                "warning_reason_code":   "LOW_LOAD_VS_USUAL_HOUR",
                "warning_reason_detail": f"t+{step}: pred={pred:.1f} < lower={lower:.1f}",
            }

    return {"warning_reason_code": "NONE", "warning_reason_detail": None}


def _compute_physical_info(raw: pd.DataFrame, spec: MeterSpec,
                            timestamp: pd.Timestamp) -> dict:
    """물리 이상치 상세 정보 계산. physical_flag 포함 6개 키 반환."""
    _RULES: list[tuple[str, str, Any]] = []
    if "PF" in raw.columns:
        pf = pd.to_numeric(raw["PF"], errors="coerce").abs()
        _RULES.append(("PF", "PF_OUT_OF_RANGE", pf > 1.0))
    if "U1" in raw.columns:
        u1 = pd.to_numeric(raw["U1"], errors="coerce")
        _RULES.append(("U1", "U1_INVALID", (u1 <= 0) | (u1 > 1000.0)))
    if "qv" in raw.columns:
        qv = pd.to_numeric(raw["qv"], errors="coerce")
        _RULES.append(("qv", "QV_NEGATIVE", qv < 0))

    _empty = {
        "physical_flag": False,
        "physical_issue_types": None,
        "physical_issue_count": 0,
        "physical_issue_recent_count": 0,
        "physical_issue_pattern": "none",
        "physical_issue_detail": None,
    }
    if not _RULES:
        return _empty

    ts_col = pd.to_datetime(raw["ts"], utc=True)
    recent_cutoff = timestamp - pd.Timedelta(hours=PHYSICAL_RECENT_HOURS)

    # 피처별 위반 집계
    any_viol = pd.Series(False, index=raw.index)
    triggered_codes: list[str] = []
    detail_parts: list[str] = []
    for _feat, code, mask in _RULES:
        m = mask.fillna(False)
        n = int(m.sum())
        if n > 0:
            triggered_codes.append(code)
            detail_parts.append(f"{code} in {n} row{'s' if n > 1 else ''}")
        any_viol |= m

    total_count = int(any_viol.sum())
    recent_any = any_viol & (ts_col >= recent_cutoff)
    recent_count = int(recent_any.sum())

    # 최근 24h 내 최대 연속 위반 길이 → pattern
    if recent_count == 0:
        pattern = "none"
    else:
        max_consec = cur = 0
        for v in recent_any.values:
            if v:
                cur += 1
                max_consec = max(max_consec, cur)
            else:
                cur = 0
        if max_consec <= 2:
            pattern = "transient"
        elif max_consec <= 6:
            pattern = "short_sustained"
        elif max_consec <= 23:
            pattern = "sustained"
        else:
            pattern = "long_sustained"

    return {
        "physical_flag":             recent_count > 0,
        "physical_issue_types":      "|".join(triggered_codes) if triggered_codes else None,
        "physical_issue_count":      total_count,
        "physical_issue_recent_count": recent_count,
        "physical_issue_pattern":    pattern,
        "physical_issue_detail":     "; ".join(detail_parts) if detail_parts else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 입력 윈도우 구성
# ─────────────────────────────────────────────────────────────────────────────

def _build_window(
    raw: pd.DataFrame,
    spec: MeterSpec,
    use_time_features: bool,
    window_size: int,
    feature_columns: list[str],
    input_scaler,
) -> np.ndarray | None:
    """(1, window_size, n_features) 또는 None."""
    frame = raw.copy()
    frame["ts"] = normalize_ts(frame["ts"])
    frame = frame.sort_values("ts").drop_duplicates("ts", keep="last")
    frame = apply_physical_rules(frame, spec)
    # 학습과 동일하게 1h 등간격 reindex + ffill (누락 timestamp 대응)
    frame = frame.set_index("ts")
    full_idx = pd.date_range(frame.index.min(), frame.index.max(), freq="h", tz="UTC")
    frame = frame.reindex(full_idx).ffill(limit=MAX_FFILL_HOURS).reset_index()
    frame.rename(columns={"index": "ts"}, inplace=True)
    frame = add_derived_features(frame)   # lag168 계산에 전체 행 필요
    if use_time_features:
        frame = add_time_features(frame)
    frame = frame.tail(window_size)       # derived 계산 후 window 추출
    if len(frame) < window_size:
        return None
    scaler_cols = scaled_feature_columns(feature_columns)
    try:
        x = transform_input(frame.reset_index(drop=True), feature_columns, scaler_cols, input_scaler)
    except Exception:
        return None
    if np.isnan(x).any():
        return None
    return x[np.newaxis, :, :].astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# 잔차 복원: scaled 잔차 → 실제 P 값
# ─────────────────────────────────────────────────────────────────────────────

def _restore_residual(pred_residual_scaled: np.ndarray, anchor_p: float, target_scaler) -> np.ndarray:
    """
    학습 target = scaled_P(t+h) - scaled_P(t-1) 이므로:
      1. anchor_scaled = target_scaler.transform([[anchor_p]])
      2. pred_actual_scaled = anchor_scaled + pred_residual_scaled
      3. pred_actual = target_scaler.inverse_transform(pred_actual_scaled)
    """
    anchor_scaled = float(target_scaler.transform([[anchor_p]])[0, 0])
    pred_actual_scaled = (pred_residual_scaled + anchor_scaled).reshape(-1, 1)
    return target_scaler.inverse_transform(pred_actual_scaled).ravel()


# ─────────────────────────────────────────────────────────────────────────────
# v52 예측 재현 (routing.json의 v52_source 기반)
# ─────────────────────────────────────────────────────────────────────────────

def _predict_v52_scaled(
    routing: dict,
    raw: pd.DataFrame,
    model_spec,
    model_urn: str,
    horizon: int,
    batch_size: int,
    input_scaler,
    dev,
) -> np.ndarray | None:
    """v52 잔차 예측(scaled) 재현. v52_source 분기 + v52_bias_correction 적용."""
    v52_source = routing.get("v52_source")  # 구 artifact는 None → fallback to v10

    _x_cache:    dict[tuple, np.ndarray | None] = {}
    _pred_cache: dict[str, np.ndarray]          = {}

    def _run_ver(ver: str) -> np.ndarray | None:
        if ver in _pred_cache:
            return _pred_cache[ver]
        use_tf = _VERSION_USE_TIME.get(ver, True)
        arch   = _VERSION_ARCH.get(ver, "lstm")
        ws     = 168 if ver == "v3" else 24
        ver_fc = model_feature_columns(model_spec, use_tf)
        key    = (ver, tuple(ver_fc))
        if key not in _x_cache:
            _x_cache[key] = _build_window(raw, model_spec, use_tf, ws, ver_fc, input_scaler)
        X = _x_cache[key]
        if X is None:
            return None
        lstm = _cached(
            ("lstm", model_urn, horizon, ver),
            lambda ver=ver, arch=arch, ver_fc=ver_fc: load_lstm_model(
                RecurrentPredictor,
                input_size=len(ver_fc),
                hidden_size=HIDDEN_SIZE,
                output_size=horizon,
                architecture=arch,
                dropout=0.0,
                meter_urn=model_urn,
                horizon=horizon,
                version=ver,
            ).to(dev).eval()
        )
        p = predict_scaled(lstm, X, batch_size)
        _pred_cache[ver] = p
        return p

    def _run_v10() -> np.ndarray | None:
        versions = routing.get("lstm_top2_versions", ["v2", "v7"])
        weights  = np.array(routing.get("lstm_top2_weights", [0.5, 0.5]), dtype=np.float32)
        out = np.zeros((1, horizon), dtype=np.float32)
        for ver, wt in zip(versions, weights):
            p = _run_ver(ver)
            if p is None:
                return None
            out += wt * p
        return out

    def _run_stepwise(step_versions: list, step_weights: list) -> np.ndarray | None:
        out = np.zeros((1, horizon), dtype=np.float32)
        for step, (vers, wts) in enumerate(zip(step_versions, step_weights)):
            for ver, wt in zip(vers, wts):
                p = _run_ver(ver)
                if p is None:
                    return None
                out[0, step] += float(wt) * p[0, step]
        return out

    # v52_source는 학습 시점에 resolve된 concrete 값: v10/v12/v15/v24/v3
    # 구 artifact(v52_source 없음)는 v10 fallback
    if v52_source is None or v52_source == "v10":
        pred = _run_v10()

    elif v52_source == "v3":
        pred = _run_ver("v3")

    elif v52_source == "v12":
        pred = _run_stepwise(
            routing.get("v12_step_versions", []),
            routing.get("v12_step_weights",  []),
        )

    elif v52_source == "v15":
        pred = _run_stepwise(
            routing.get("v15_step_versions", []),
            routing.get("v15_step_weights",  []),
        )

    elif v52_source == "v24":
        v24_w = routing.get("v24_weights", [1.0, 0.0, 0.0])
        pred  = np.zeros((1, horizon), dtype=np.float32)
        if v24_w[0] > 0:
            p = _run_v10()
            if p is None:
                return None
            pred += v24_w[0] * p
        if v24_w[1] > 0:
            p = _run_stepwise(routing.get("v12_step_versions", []), routing.get("v12_step_weights", []))
            if p is None:
                return None
            pred += v24_w[1] * p
        if v24_w[2] > 0:
            p = _run_stepwise(routing.get("v15_step_versions", []), routing.get("v15_step_weights", []))
            if p is None:
                return None
            pred += v24_w[2] * p

    else:
        pred = _run_v10()  # 알 수 없는 source → fallback

    if pred is None:
        return None

    v52_bias = np.array(routing.get("v52_bias_correction", [0.0] * horizon), dtype=np.float32)
    return pred + v52_bias.reshape(1, -1)


# ─────────────────────────────────────────────────────────────────────────────
# 단일 계량기 예측
# ─────────────────────────────────────────────────────────────────────────────

def predict_meter(
    engine,
    meter_urn: str,
    model_urn: str,
    horizon: int,
    timestamp: pd.Timestamp,
    batch_size: int = BATCH_SIZE,
    raw_data: "pd.DataFrame | None" = None,
) -> dict[str, Any] | None:
    """
    meter_urn 데이터를 model_urn artifact로 예측.
    전이 멤버는 meter_urn != model_urn (대표 artifact 사용).
    raw_data 제공 시 DB 조회 생략 (배치 모드용).
    """
    spec       = METER_SPECS_BY_URN.get(meter_urn)
    model_spec = METER_SPECS_BY_URN.get(model_urn)
    if spec is None or model_spec is None:
        return None

    routing        = _cached(("routing", model_urn, horizon), lambda: load_routing(model_urn, horizon))
    base_fc        = _cached(("fc",      model_urn, horizon), lambda: load_feature_columns(model_urn, horizon))
    input_scaler, target_scaler = _cached(
        ("scalers", model_urn, horizon), lambda: load_scalers(model_urn, horizon))
    bias_df        = _cached(("bias",    model_urn, horizon), lambda: load_bias_corrections(model_urn, horizon))
    v57 = routing.get("v57", "v52")
    v63 = routing.get("v63", "v57")

    window_size = 24
    needed = 168 + 168 + horizon + 4  # v3 LSTM(window_size=168) 기준 diff_lag168 NaN 방지

    # diff_lag168 계산에 168h 필요 → max_window_size(168) + 168 + 여유
    if raw_data is not None:
        raw = raw_data[raw_data["ts"] < timestamp].tail(needed)
    else:
        raw = fetch_meter_window(engine, spec, end_ts=timestamp, window_hours=needed)
    if raw is None or len(raw) < window_size:
        return _failed_row(
            meter_urn,
            model_urn,
            horizon,
            timestamp,
            "insufficient_data",
            warning_reason_detail=f"insufficient input rows: rows={0 if raw is None else len(raw)}, required={window_size}",
        )

    raw = raw.sort_values("ts").reset_index(drop=True)
    phys_info    = _compute_physical_info(raw, spec, timestamp)
    quality_info = _compute_input_quality(raw, spec, timestamp, routing)
    if quality_info.get("latest_input_missing"):
        return _failed_row(
            meter_urn,
            model_urn,
            horizon,
            timestamp,
            "insufficient_data",
            warning_reason_detail=f"latest input bucket is missing: expected={quality_info.get('expected_latest_input_ts')}",
            quality_info=quality_info,
        )
    if int(quality_info.get("max_consecutive_missing", 0)) > MAX_FFILL_HOURS:
        return _failed_row(
            meter_urn,
            model_urn,
            horizon,
            timestamp,
            "insufficient_data",
            warning_reason_code="INPUT_QUALITY_ISSUE",
            warning_reason_detail=(
                f"max_consecutive_missing={quality_info.get('max_consecutive_missing')} "
                f"exceeds limit={MAX_FFILL_HOURS}"
            ),
            quality_info=quality_info,
        )
    if int(quality_info.get("total_missing_rows", 0)) > MAX_TOTAL_MISSING_ROWS:
        return _failed_row(
            meter_urn,
            model_urn,
            horizon,
            timestamp,
            "insufficient_data",
            warning_reason_code="INPUT_QUALITY_ISSUE",
            warning_reason_detail=(
                f"total_missing_rows={quality_info.get('total_missing_rows')} "
                f"exceeds limit={MAX_TOTAL_MISSING_ROWS}"
            ),
            quality_info=quality_info,
        )

    # anchor: 입력 윈도우 마지막 P값 (잔차 복원용)
    p_valid = pd.to_numeric(raw["P"], errors="coerce").dropna()
    if p_valid.empty:
        return _failed_row(
            meter_urn,
            model_urn,
            horizon,
            timestamp,
            "insufficient_data",
            warning_reason_code="INPUT_QUALITY_ISSUE",
            warning_reason_detail="input P has no valid value for residual anchor",
            quality_info=quality_info,
        )
    anchor_p = float(p_valid.iloc[-1])

    dev = device()

    # ── v63: LightGBM / CatBoost / LSTM top-2 앙상블 ───────────────────────
    if v63 == "v61":
        # LightGBM
        x_base = _build_window(raw, model_spec, True, window_size, base_fc, input_scaler)
        if x_base is None:
            return _failed_row(
                meter_urn,
                model_urn,
                horizon,
                timestamp,
                "insufficient_data",
                warning_reason_code="INPUT_QUALITY_ISSUE",
                warning_reason_detail="feature matrix contains NaN or is invalid after imputation",
                quality_info=quality_info,
            )
        lgb_models = _cached(("lgb", model_urn, horizon), lambda: _load_lightgbm(model_urn, horizon))
        v63_residual_scaled = _predict_lightgbm_scaled(lgb_models, x_base)  # (1, horizon)

    elif v57 == "v53":
        # CatBoost
        x_base = _build_window(raw, model_spec, True, window_size, base_fc, input_scaler)
        if x_base is None:
            return _failed_row(
                meter_urn,
                model_urn,
                horizon,
                timestamp,
                "insufficient_data",
                warning_reason_code="INPUT_QUALITY_ISSUE",
                warning_reason_detail="feature matrix contains NaN or is invalid after imputation",
                quality_info=quality_info,
            )
        cb = _cached(("cb", model_urn, horizon), lambda: load_catboost(model_urn, horizon))
        v63_residual_scaled = predict_catboost_scaled(cb, x_base, horizon)  # (1, horizon)

    else:
        # LSTM (v57 = v52 or v36): v52 전체 재현
        v63_residual_scaled = _predict_v52_scaled(
            routing, raw, model_spec, model_urn, horizon, batch_size, input_scaler, dev
        )
        if v63_residual_scaled is None:
            return _failed_row(
                meter_urn,
                model_urn,
                horizon,
                timestamp,
                "insufficient_data",
                warning_reason_code="INPUT_QUALITY_ISSUE",
                warning_reason_detail="v52 feature matrix contains NaN or is invalid after imputation",
                quality_info=quality_info,
            )
        if v57 == "v36":
            v36_bias = np.array(routing.get("v36_bias", [0.0] * horizon), dtype=np.float32)
            v63_residual_scaled += v36_bias.reshape(1, -1)

    # ── v67: Ridge (base feature columns) ───────────────────────────────────
    x_base_tf = _build_window(raw, model_spec, True, window_size, base_fc, input_scaler)
    if x_base_tf is None:
        return _failed_row(
            meter_urn,
            model_urn,
            horizon,
            timestamp,
            "insufficient_data",
            warning_reason_code="INPUT_QUALITY_ISSUE",
            warning_reason_detail="ridge feature matrix contains NaN or is invalid after imputation",
            quality_info=quality_info,
        )
    ridge = _cached(("ridge", model_urn, horizon), lambda: load_ridge(model_urn, horizon))
    v67_residual_scaled = predict_ridge_scaled(ridge, x_base_tf)  # (1, horizon) or (1,1)

    # ── 잔차 → 실제 P 복원 ───────────────────────────────────────────────────
    v63_pred = _restore_residual(v63_residual_scaled.ravel(), anchor_p, target_scaler)
    v67_pred = _restore_residual(v67_residual_scaled.ravel()[:horizon], anchor_p, target_scaler)

    # ── v71: Seasonal Naive (24h 전 같은 시각 실제값, 이미 원본 공간) ─────────
    p_vals = pd.to_numeric(raw["P"], errors="coerce").ffill().to_numpy(dtype=np.float32)
    if len(p_vals) >= 24:
        v71_pred = p_vals[-24:-24 + horizon] if horizon <= 24 else p_vals[-24:]
    else:
        v71_pred = np.full(horizon, anchor_p, dtype=np.float32)

    # ── median 앙상블 (원본 P 공간) ──────────────────────────────────────────
    ensemble_pred = np.median(
        np.stack([v63_pred, v67_pred, v71_pred[:horizon]], axis=0), axis=0
    )  # (horizon,)

    # ── shrunk hour bias 보정 ─────────────────────────────────────────────────
    # 학습에서 모든 step의 bias는 target_end_ts(마지막 step 시각) 기준으로 저장됨
    target_end_hour = (timestamp.hour + horizon - 1) % 24
    for step in range(1, horizon + 1):
        step_corr = bias_df[bias_df["forecast_step"] == step]
        row = step_corr[step_corr["target_hour_utc"] == target_end_hour]
        if not row.empty:
            ensemble_pred[step - 1] += float(row["median_residual_correction"].iloc[0])
        else:
            fallback = float(step_corr["fallback_global_correction"].iloc[0]) if not step_corr.empty else 0.0
            ensemble_pred[step - 1] += fallback

    # ── 사전 경보 (val actual P 시간대별 2~98 percentile 기준) ─────────────────
    meter_thresh = _THRESHOLDS.get(meter_urn, {})
    any_warning = False
    result: dict[str, Any] = {
        "meter_urn":  meter_urn,
        "model_urn":  model_urn,
        "timestamp":  timestamp.isoformat(),
        "horizon":    horizon,
        **phys_info,
        **quality_info,
    }
    for step in range(1, horizon + 1):
        pred_val    = float(ensemble_pred[step - 1])
        target_hour = (timestamp.hour + step - 1) % 24
        result[f"pred_t_plus_{step}"]         = pred_val
        result[f"target_hour_t_plus_{step}"]  = target_hour

        bounds = meter_thresh.get(target_hour)
        if bounds is not None:
            p_lower    = bounds["p_lower"]
            p_upper    = bounds["p_upper"]
            low_sample = bounds["low_sample"]
            if pred_val > p_upper:
                w_type = "high"
            elif pred_val < p_lower:
                w_type = "low"
            else:
                w_type = "none"
        else:
            p_lower = p_upper = float("nan")
            low_sample = False
            w_type = "none"

        warning = w_type != "none"
        result[f"threshold_lower_t_plus_{step}"] = p_lower
        result[f"threshold_upper_t_plus_{step}"] = p_upper
        result[f"warning_type_t_plus_{step}"]    = w_type
        result[f"warning_t_plus_{step}"]         = warning
        result[f"low_sample_t_plus_{step}"]      = low_sample
        if warning:
            any_warning = True

    result["warning_flag"] = any_warning
    result["status"] = "success"
    result.update(_tag_fields(meter_urn, timestamp))
    result.update(_compute_warning_reason(result))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 전체 실행
# ─────────────────────────────────────────────────────────────────────────────

def run_inference(
    horizon: int,
    timestamp: str | pd.Timestamp,
    output_dir: Path | None = None,
    batch_size: int = BATCH_SIZE,
) -> dict[str, dict]:
    if isinstance(timestamp, str):
        timestamp = pd.Timestamp(timestamp).tz_convert("UTC") if ("+" in timestamp or "Z" in timestamp) \
                    else pd.Timestamp(timestamp, tz="UTC")

    global _THRESHOLDS, _METER_TAGS
    _THRESHOLDS = _load_thresholds()
    _METER_TAGS = _load_meter_tags()
    if _THRESHOLDS:
        print(f"threshold 로드: {len(_THRESHOLDS)}개 계량기", flush=True)
    else:
        print("경고: val_thresholds.csv 없음 — warning_t_plus_k 비활성", flush=True)
    if _METER_TAGS:
        print(f"meter_tags 로드: {len(_METER_TAGS)}개 계량기", flush=True)

    engine  = build_engine()
    results: dict[str, dict] = {}
    rows    = []

    for meter_urn, info in sorted(METER_MAP.items()):
        if info["action"] == "skip":
            continue
        model_urn = info["model_urn"]
        art_path  = ARTIFACTS_DIR / f"{horizon}h" / model_urn / "routing.json"
        if not art_path.exists():
            print(f"  [SKIP] {meter_urn}: artifact 없음 ({model_urn})", flush=True)
            failed = _failed_row(
                meter_urn,
                model_urn,
                horizon,
                timestamp,
                "no_artifact",
                warning_reason_detail=f"artifact missing: model_urn={model_urn}",
            )
            rows.append(failed)
            results[meter_urn] = failed
            continue
        try:
            res = predict_meter(engine, meter_urn, model_urn, horizon, timestamp, batch_size)
            if res is None:
                print(f"  [SKIP] {meter_urn}: 데이터 부족", flush=True)
                failed = _failed_row(
                    meter_urn,
                    model_urn,
                    horizon,
                    timestamp,
                    "insufficient_data",
                    warning_reason_detail="insufficient data",
                )
                rows.append(failed)
                results[meter_urn] = failed
                continue
            results[meter_urn] = res
            rows.append(res)
            if res.get("status") != "success":
                detail = res.get("warning_reason_detail") or res.get("warning_reason_code") or res.get("status")
                print(f"  [SKIP] {meter_urn}: {detail}", flush=True)
                continue
            flag   = " ⚑ 물리이상" if res["physical_flag"]  else ""
            w_flag = " ⚠ 사전경보" if res["warning_flag"]   else ""
            preds  = ", ".join(f"t+{s}={res[f'pred_t_plus_{s}']:.1f}" for s in range(1, horizon + 1))
            print(f"  {meter_urn}: {preds}{flag}{w_flag}", flush=True)
        except Exception as e:
            print(f"  [오류] {meter_urn}: {e}", flush=True)
            failed = _failed_row(
                meter_urn,
                model_urn,
                horizon,
                timestamp,
                "error",
                warning_reason_detail=f"exception: {type(e).__name__}",
            )
            rows.append(failed)
            results[meter_urn] = failed

    if rows:
        out = output_dir or (ARTIFACTS_DIR / "inference_results")
        out.mkdir(parents=True, exist_ok=True)
        ts_str   = timestamp.strftime("%Y%m%dT%H%M")
        csv_path = out / f"predictions_{horizon}h_{ts_str}.csv"
        csv_frame = pd.DataFrame(rows)
        drop_columns = list(DEBUG_CSV_DROP_COLUMNS) + [f"warning_t_plus_{step}" for step in range(1, horizon + 1)]
        csv_frame = csv_frame.drop(columns=[column for column in drop_columns if column in csv_frame.columns])
        csv_frame.to_csv(csv_path, index=False)
        print(f"\n결과 저장: {csv_path}")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(description="CMS anomaly v84 inference pipeline")
    p.add_argument("--horizon",    type=int, choices=[1, 3], required=True)
    p.add_argument("--timestamp",  type=str, default=None,
                   help="예측 기준 시각 (ISO 8601). 기본값: 현재 UTC")
    p.add_argument("--output-dir", type=str, default=None)
    return p.parse_args()


def main():
    args = _parse_args()
    ts  = args.timestamp or datetime.now(timezone.utc).isoformat()
    out = Path(args.output_dir) if args.output_dir else None
    print(f"=== CMS anomaly v84 inference | horizon={args.horizon}h | ts={ts} ===")
    results = run_inference(horizon=args.horizon, timestamp=ts, output_dir=out)
    print(f"\n예측 완료: {len(results)}개 계량기")


if __name__ == "__main__":
    main()
