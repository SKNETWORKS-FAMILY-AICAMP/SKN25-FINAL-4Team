"""전처리, 스케일링, 슬라이딩 윈도우 생성. test2 의존 없음."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import StandardScaler
from torch.utils.data import DataLoader, TensorDataset

from ml.pipeline.common.config import (
    BATCH_SIZE,
    MAX_FFILL_HOURS,
    TARGET_COLUMN,
    TIME_FEATURE_COLUMNS,
    DERIVED_FEATURE_COLUMNS,
    USE_DERIVED_FEATURES,
    USE_RESIDUAL_TARGET,
    TRAIN_START,
    VAL_START,
    TEST_START,
    END_TS,
    WINDOW_SIZE,
    MeterSpec,
)


# ── DatasetBundle ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DatasetBundle:
    x_train: np.ndarray
    y_train: np.ndarray
    x_val: np.ndarray
    y_val: np.ndarray
    x_test: np.ndarray
    y_test: np.ndarray
    meta_train: pd.DataFrame
    meta_val: pd.DataFrame
    meta_test: pd.DataFrame
    input_scaler: StandardScaler
    target_scaler: StandardScaler
    split_bounds: dict[str, pd.Timestamp]
    rows_raw: int
    rows_after_reindex: int
    rows_with_required_na: int
    skipped_windows: int
    max_ffill_hours: int
    feature_columns: list[str]
    window_size: int
    # USE_RESIDUAL_TARGET=True 일 때만 채워짐. scaled P(t-1) 값 (복원용 anchor)
    anchor_train: np.ndarray | None = None
    anchor_val:   np.ndarray | None = None
    anchor_test:  np.ndarray | None = None


# ── 전처리 ───────────────────────────────────────────────────────────────────

def normalize_ts(series: pd.Series) -> pd.Series:
    values = pd.to_datetime(series)
    if values.dt.tz is None:
        return values.dt.tz_localize("UTC")
    return values.dt.tz_convert("UTC")


def add_time_features(frame: pd.DataFrame) -> pd.DataFrame:
    enriched = frame.copy()
    ts = pd.to_datetime(enriched["ts"], utc=True)
    enriched["hour_sin"]        = np.sin(2 * np.pi * ts.dt.hour / 24)
    enriched["hour_cos"]        = np.cos(2 * np.pi * ts.dt.hour / 24)
    enriched["day_of_week_sin"] = np.sin(2 * np.pi * ts.dt.dayofweek / 7)
    enriched["day_of_week_cos"] = np.cos(2 * np.pi * ts.dt.dayofweek / 7)
    enriched["month_sin"]       = np.sin(2 * np.pi * (ts.dt.month - 1) / 12)
    enriched["month_cos"]       = np.cos(2 * np.pi * (ts.dt.month - 1) / 12)
    return enriched


def add_derived_features(frame: pd.DataFrame) -> pd.DataFrame:
    """파생변수 4개 추가. P 컬럼 기반으로 계산."""
    derived = frame.copy()
    p = pd.to_numeric(derived["P"], errors="coerce")
    derived["diff_lag24"]       = p.diff(24)
    derived["diff_lag168"]      = p.diff(168)
    ts = pd.to_datetime(derived["ts"], utc=True)
    derived["is_workday"]       = (ts.dt.dayofweek < 5).astype(np.float32)
    derived["rolling_mean_24h"] = p.rolling(window=24, min_periods=1).mean()
    return derived


def model_feature_columns(spec: MeterSpec, use_time_features: bool) -> list[str]:
    cols = list(spec.features)
    if use_time_features:
        cols.extend(TIME_FEATURE_COLUMNS)
    if USE_DERIVED_FEATURES:
        cols.extend(DERIVED_FEATURE_COLUMNS)
    return list(dict.fromkeys(cols))


def apply_physical_rules(frame: pd.DataFrame, spec: MeterSpec) -> pd.DataFrame:
    cleaned = frame.copy()
    for col in spec.features:
        if col in cleaned.columns:
            cleaned[col] = pd.to_numeric(cleaned[col], errors="coerce")
    if "PF" in cleaned.columns:
        cleaned.loc[cleaned["PF"].abs() > 1.0, "PF"] = pd.NA
    if "U1" in cleaned.columns:
        cleaned.loc[(cleaned["U1"] <= 0) | (cleaned["U1"] > 1000.0), "U1"] = pd.NA
    if "qv" in cleaned.columns:
        cleaned.loc[cleaned["qv"] < 0, "qv"] = pd.NA
    return cleaned


def prepare_model_frame(
    raw_frame: pd.DataFrame,
    spec: MeterSpec,
    use_time_features: bool = False,
) -> tuple[pd.DataFrame, int, list[str]]:
    required = ["ts", *spec.features]
    missing = [c for c in required if c not in raw_frame.columns]
    if missing:
        raise ValueError(f"{spec.meter_urn}: missing columns {missing}")
    frame = raw_frame[required].copy()
    frame["ts"] = normalize_ts(frame["ts"])
    frame = frame.sort_values("ts").drop_duplicates("ts", keep="last")
    frame = apply_physical_rules(frame, spec)
    rows_raw = len(frame)
    frame = frame.set_index("ts")
    full_idx = pd.date_range(frame.index.min(), frame.index.max(), freq="h", tz="UTC")
    frame = frame.reindex(full_idx).ffill(limit=MAX_FFILL_HOURS).reset_index()
    frame.rename(columns={"index": "ts"}, inplace=True)
    if use_time_features:
        frame = add_time_features(frame)
    if USE_DERIVED_FEATURES:
        frame = add_derived_features(frame)
    return frame, rows_raw, model_feature_columns(spec, use_time_features)


# ── Split bounds ──────────────────────────────────────────────────────────────

def determine_split_bounds(frame: pd.DataFrame) -> dict[str, pd.Timestamp]:
    min_ts, max_ts = frame["ts"].min(), frame["ts"].max()
    if min_ts <= TRAIN_START and max_ts >= END_TS - pd.Timedelta(hours=1):
        return {"train_start": TRAIN_START, "val_start": VAL_START,
                "test_start": TEST_START, "end": END_TS}
    start = min_ts.ceil("h")
    end = (max_ts + pd.Timedelta(hours=1)).floor("h")
    span = end - start
    return {"train_start": start, "val_start": start + span * (4/6),
            "test_start": start + span * (5/6), "end": end}


def split_name(ts: pd.Timestamp, bounds: dict) -> str | None:
    if bounds["train_start"] <= ts < bounds["val_start"]: return "train"
    if bounds["val_start"]   <= ts < bounds["test_start"]: return "val"
    if bounds["test_start"]  <= ts < bounds["end"]:        return "test"
    return None


def horizon_split_name(t_start: pd.Timestamp, t_end: pd.Timestamp, bounds: dict) -> str | None:
    s, e = split_name(t_start, bounds), split_name(t_end, bounds)
    return s if s == e else None


# ── Scaler ────────────────────────────────────────────────────────────────────

def scaled_feature_columns(feature_columns: list[str]) -> list[str]:
    time_cols = set(TIME_FEATURE_COLUMNS)
    return [c for c in feature_columns if c not in time_cols]


def fit_scalers(
    frame: pd.DataFrame,
    feature_columns: list[str],
    bounds: dict,
) -> tuple[StandardScaler, StandardScaler, list[str]]:
    scaler_cols = scaled_feature_columns(feature_columns)
    train = frame[(frame["ts"] >= bounds["train_start"]) & (frame["ts"] < bounds["val_start"])]
    train = train.dropna(subset=scaler_cols)
    if train.empty:
        raise ValueError("No clean train rows for scaler fitting")
    input_scaler = StandardScaler().fit(train[scaler_cols])
    target_scaler = StandardScaler().fit(train[[TARGET_COLUMN]])
    return input_scaler, target_scaler, scaler_cols


def transform_input(
    segment: pd.DataFrame,
    feature_columns: list[str],
    scaler_columns: list[str],
    input_scaler: StandardScaler,
) -> np.ndarray:
    scaled = pd.DataFrame(
        input_scaler.transform(segment[scaler_columns]),
        index=segment.index, columns=scaler_columns,
    )
    cols = []
    for c in feature_columns:
        cols.append(scaled[c].to_numpy(dtype=np.float32) if c in scaler_columns
                    else segment[c].to_numpy(dtype=np.float32))
    return np.column_stack(cols)


# ── Sliding windows ───────────────────────────────────────────────────────────

def iter_continuous_segments(frame: pd.DataFrame, feature_columns: list[str], min_length: int):
    clean = frame.dropna(subset=feature_columns).reset_index(drop=True)
    if clean.empty:
        return
    groups = clean["ts"].diff().ne(pd.Timedelta(hours=1)).cumsum()
    for _, seg in clean.groupby(groups, sort=True):
        if len(seg) >= min_length:
            yield seg.reset_index(drop=True)


def build_windows(
    frame: pd.DataFrame,
    spec: MeterSpec,
    horizon: int,
    rows_raw: int,
    feature_columns: list[str],
    window_size: int = WINDOW_SIZE,
) -> DatasetBundle:
    bounds = determine_split_bounds(frame)
    input_scaler, target_scaler, scaler_cols = fit_scalers(frame, feature_columns, bounds)
    required_na = int(frame[feature_columns].isna().any(axis=1).sum())

    buckets: dict[str, list] = {f"{s}_{t}": [] for s in ("train","val","test") for t in ("x","y")}
    metas:   dict[str, list] = {s: [] for s in ("train","val","test")}
    anchors: dict[str, list] = {s: [] for s in ("train","val","test")}
    skipped = 0

    for seg in iter_continuous_segments(frame, feature_columns, window_size + horizon):
        feats = transform_input(seg, feature_columns, scaler_cols, input_scaler)
        targs = target_scaler.transform(seg[[TARGET_COLUMN]]).ravel()
        for i in range(len(seg) - window_size - horizon + 1):
            ie = i + window_size
            ts_start = seg.loc[ie, "ts"]
            ts_end   = seg.loc[ie + horizon - 1, "ts"]
            sp = horizon_split_name(ts_start, ts_end, bounds)
            if sp is None:
                continue
            x = feats[i:ie].astype(np.float32)
            # USE_RESIDUAL_TARGET: 타겟을 P(t+h) - P(t-1) 으로 변환
            # anchor = targs[ie-1] = scaled P(t-1) (입력 윈도우 마지막 값)
            # 복원: scaled_P(t+h) = anchor + residual → inverse_transform → 원본 단위
            if USE_RESIDUAL_TARGET:
                anchor = targs[ie - 1]
                y = (targs[ie:ie+horizon] - anchor).astype(np.float32)
            else:
                anchor = None
                y = targs[ie:ie+horizon].astype(np.float32)
            if np.isnan(x).any() or np.isnan(y).any():
                skipped += 1
                continue
            buckets[f"{sp}_x"].append(x)
            buckets[f"{sp}_y"].append(y)
            if USE_RESIDUAL_TARGET:
                anchors[sp].append(np.float32(anchor))
            metas[sp].append({
                "meter_urn": spec.meter_urn, "group": spec.group,
                "role": spec.role, "source": spec.source,
                "input_start_ts": seg.loc[i, "ts"],
                "input_end_ts":   seg.loc[ie - 1, "ts"],
                "target_start_ts": ts_start,
                "target_end_ts":   ts_end,
            })

    def stack(key):
        v = buckets[key]
        if not v:
            raise ValueError(f"{spec.meter_urn}: no windows for {key}")
        return np.stack(v)

    def stack_anchor(sp):
        return np.array(anchors[sp], dtype=np.float32) if USE_RESIDUAL_TARGET else None

    return DatasetBundle(
        x_train=stack("train_x"), y_train=stack("train_y"),
        x_val=stack("val_x"),     y_val=stack("val_y"),
        x_test=stack("test_x"),   y_test=stack("test_y"),
        meta_train=pd.DataFrame(metas["train"]),
        meta_val=pd.DataFrame(metas["val"]),
        meta_test=pd.DataFrame(metas["test"]),
        input_scaler=input_scaler, target_scaler=target_scaler,
        split_bounds=bounds, rows_raw=rows_raw,
        rows_after_reindex=len(frame),
        rows_with_required_na=required_na,
        skipped_windows=skipped,
        max_ffill_hours=MAX_FFILL_HOURS,
        feature_columns=feature_columns, window_size=window_size,
        anchor_train=stack_anchor("train"),
        anchor_val=stack_anchor("val"),
        anchor_test=stack_anchor("test"),
    )


def make_loader(x: np.ndarray, y: np.ndarray, batch_size: int = BATCH_SIZE, shuffle: bool = False) -> DataLoader:
    return DataLoader(TensorDataset(torch.from_numpy(x), torch.from_numpy(y)),
                      batch_size=batch_size, shuffle=shuffle)
